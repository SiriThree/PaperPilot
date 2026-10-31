"""Local HTTPS security deployment with an optional explicitly configured SQL model."""

from dataclasses import replace
import asyncio
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import SimpleNamespace
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from typing import Literal

from app.adapters.data_binding import DataBinding
from app.config import Settings
from app.data.audit_boundary import verify_audit_boundary
from app.data.binding import compose_data_binding
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.settings import DataSettings
from app.data.deliverer import OutboxDeliverer
from app.main import create_app as business_app
from app.security.adapter import build_security
from app.security.audit import compose_audit, verify_writer
from app.security.executor import ReaderConfig, verify_reader


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user: str = Field(pattern=r"^pp_(business|auth|audit|reader|outbox)_[a-z0-9_]{1,48}$")
    password: SecretStr = Field(min_length=32)
    project_id: UUID | None = None


class SQLModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str
    api_key: SecretStr
    chat_model: str
    timeout_seconds: float = 45
    token_limit_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    response_format: Literal["json_schema", "json_object"] = "json_schema"

    def chat_settings(self):
        from app.ai.settings import ChatSettings

        return ChatSettings(**{**self.model_dump(), "api_key": self.api_key.get_secret_value()})

    @model_validator(mode="after")
    def valid_provider(self):
        self.chat_settings()
        if not self.base_url.startswith("https://"):
            raise ValueError("Production SQL model requires HTTPS")
        return self

    def fingerprint(self):
        # A changed provider, model, request format or credential requires fresh acceptance.
        content = {**self.model_dump(), "api_key": self.api_key.get_secret_value()}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


class Runtime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: int = Field(1, ge=1, le=1)
    deployment_id: UUID
    host: str = Field(pattern=r"^postgres$")
    port: int = Field(5432, ge=5432, le=5432)
    dbname: str = Field(pattern=r"^paperpilot_production$")
    audit_owner: str = Field(pattern=r"^pp_audit_owner_[a-z0-9]{12}$")
    auth_lock_owner: str = Field(pattern=r"^pp_auth_lock_[a-z0-9]{12}$")
    business: Login
    authentication: Login
    audit: Login
    outbox: Login
    readers: list[Login] = Field(min_length=1, max_length=256)
    sql_model: SQLModel | None = None


def runtime_config():
    path = Path(os.environ["PAPERPILOT_PRODUCTION_CONFIG"])
    info = path.lstat()
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError("Invalid production configuration")
    if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("Production credentials require current-user-only ownership")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate production configuration key")
            result[key] = value
        return result

    config = Runtime.model_validate(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique))
    identities = [config.business, config.authentication, config.audit, config.outbox, *config.readers]
    if len({v.user for v in identities}) != len(identities) or len(
        {v.password.get_secret_value() for v in identities}
    ) != len(identities):
        raise ValueError("Separate production identities and secrets required")
    if len({v.project_id for v in config.readers}) != len(config.readers) or any(
        v.project_id is None for v in config.readers
    ):
        raise ValueError("Separate project bindings required")
    for kind, login in [
        ("business", config.business),
        ("auth", config.authentication),
        ("audit", config.audit),
        ("outbox", config.outbox),
        *[("reader", r) for r in config.readers],
    ]:
        if not login.user.startswith(f"pp_{kind}_"):
            raise ValueError("Production identity kind mismatch")
    return config


def verify_outbox(connection, user):
    from psycopg.rows import tuple_row
    from app.security.executor import verify_login, QueryUnavailable

    with connection.cursor(row_factory=tuple_row) as cur:
        verify_login(cur, user)
        row = cur.execute(
            "SELECT has_table_privilege('public.outbox_events','SELECT') AND has_table_privilege('public.outbox_events','UPDATE'), "
            "NOT has_table_privilege('public.outbox_events','SELECT WITH GRANT OPTION,UPDATE WITH GRANT OPTION'), "
            "NOT has_table_privilege('public.outbox_events','INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER'), "
            "NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND c.relkind IN ('r','p','v','m','f') AND c.oid<>'public.outbox_events'::regclass "
            "AND (has_table_privilege(c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
            "OR has_any_column_privilege(c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))), "
            "NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
            "AND p.prosecdef AND has_function_privilege(p.oid,'EXECUTE'))"
        ).fetchone()
        if not row or not all(row):
            raise QueryUnavailable()


def create_app():
    binding = auth_db = audit = outbox_db = None
    try:
        config = runtime_config()
        settings = replace(
            Settings.from_env(),
            auth_lock_owner=config.auth_lock_owner,
            audit_trusted_owner=config.audit_owner,
        )
        settings.validate()
        if settings.environment != "production" or not settings.production_security_profile:
            raise ValueError("Production entry requires explicit production profile")

        def connection(login):
            return ConnectionConfig(
                config.host, config.port, login.user, login.password.get_secret_value(), config.dbname
            )

        binding = compose_data_binding(connection(config.business), DataSettings.from_env())
        auth_db = PostgresDatabase(connection(config.authentication))
        audit_config = connection(config.audit)
        audit = compose_audit(audit_config)
        outbox_db = PostgresDatabase(connection(config.outbox))
        outbox_db.set_transaction_guard(lambda conn: verify_outbox(conn, config.outbox.user))
        deliverer = OutboxDeliverer(outbox_db, SimpleNamespace(append_audit=audit.append))
        readers = {
            v.project_id: ReaderConfig(
                config.host, config.port, config.dbname, v.user, v.password.get_secret_value()
            )
            for v in config.readers
        }

        def ready():
            binding.ready()
            with outbox_db.transaction() as conn:
                conn.execute("SELECT 1")
            with binding.data.database.transaction() as conn:
                marker = conn.execute(
                    "SELECT shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=current_database()"
                ).fetchone()["marker"]
                if marker != "PaperPilot local production " + str(config.deployment_id):
                    raise ValueError("Database deployment binding mismatch")
                verify_audit_boundary(conn, trusted_owner=config.audit_owner)
            with psycopg.connect(**audit_config.conninfo(), autocommit=True) as conn:
                verify_writer(conn, config.audit.user)
            for reader in readers.values():
                with psycopg.connect(
                    **connection(next(v for v in config.readers if v.user == reader.user)).conninfo(),
                    autocommit=True,
                ) as conn:
                    verify_reader(conn, reader.user)
            return True

        def close():
            try:
                outbox_db.close()
                audit.close()
            finally:
                binding.close()

        composed = DataBinding(binding.data, ready, close)
        candidate_generator = None
        if config.sql_model is not None:
            from app.ai.client import ModelClient
            from app.ai.sql_generation import ModelSQLGenerator
            from app.security.sql_policy import SQL_BY_QUESTION

            candidate_generator = ModelSQLGenerator(
                ModelClient(config.sql_model.chat_settings()), SQL_BY_QUESTION
            )
        app = business_app(
            settings,
            data_binding=composed,
            auth_database=auth_db,
            security_factory=partial(
                build_security,
                readers=readers,
                audit_append=audit.append,
                candidate_generator=candidate_generator,
            ),
        )
        app.state.production_outbox_unavailable = False

        async def run_outbox():
            while True:
                try:
                    await asyncio.to_thread(deliverer.run_once)
                    app.state.production_outbox_unavailable = False
                except Exception:
                    app.state.production_outbox_unavailable = True
                await asyncio.sleep(0.5)

        app.state.production_outbox_runner = run_outbox
        app.state.production_reader_projects = frozenset(readers)
        source = os.environ["PAPERPILOT_SOURCE_FINGERPRINT"]
        if not re.fullmatch(r"[a-f0-9]{64}", source):
            raise ValueError("Missing immutable source fingerprint")

        def deployment_status():
            if app.state.production_outbox_unavailable:
                from app.errors import AppError

                raise AppError(503, "AUDIT_UNAVAILABLE", "生产审计交付暂不可用")
            ready()
            app.state.auth_boundary.ready()
            acceptance = None
            path = Path("/run/acceptance/acceptance.json")
            if path.is_file() and path.stat().st_size <= 65536:
                evidence = json.loads(path.read_text(encoding="utf-8"))
                if (
                    evidence.get("deployment_id") == str(config.deployment_id)
                    and evidence.get("source_fingerprint") == source
                    and evidence.get("passed") is True
                    and evidence.get("statistics_sql_mode", "template")
                    == ("model" if config.sql_model else "template")
                    and evidence.get("sql_model_fingerprint")
                    == (config.sql_model.fingerprint() if config.sql_model else None)
                ):
                    acceptance = {
                        "completed_at": evidence["completed_at"],
                        "passed_checks": len(evidence["checks"]),
                    }
            return {
                "environment": "production",
                "profile": "local_https_security",
                "deployment_id": config.deployment_id,
                "source_fingerprint": source,
                "runtime_checks_passed": True,
                "ai_available": False,
                "statistics_sql_mode": "model" if config.sql_model else "template",
                "statistics_model_name": config.sql_model.chat_model if config.sql_model else None,
                "sql_model_fingerprint": config.sql_model.fingerprint() if config.sql_model else None,
                "acceptance": acceptance,
            }

        app.state.deployment_status = deployment_status
        return app
    except Exception:
        if outbox_db is not None:
            outbox_db.close()
        if audit is not None:
            audit.close()
        if auth_db is not None:
            auth_db.close()
        if binding is not None:
            binding.close()
        raise RuntimeError("Production security composition failed; startup refused") from None
