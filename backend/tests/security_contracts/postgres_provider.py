"""Explicit disposable PostgreSQL workspace and the real D shared-contract provider."""

from contextlib import contextmanager
from dataclasses import replace

import asyncio
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.adapters.mock_ai import MockAI
from app.adapters.ports import Context, FileReference
from app.schemas import ProjectCreate, QuestionRequest
from app.security import ReaderConfig, build_security

A_COMMIT = "0b2953c218668af71a4bf869e5c6d238cc0d588d"
IMAGE = "pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"


@contextmanager
def postgres_workspace(record_testsuite_property=lambda *args: None):
    import app
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    root = Path(__file__).resolve().parents[3]
    source = Path(os.environ["PAPERPILOT_SECURITY_TEST_A_SOURCE"]).resolve()
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if head != A_COMMIT:
        pytest.fail("A reference must match the documented commit")
    subprocess.run(
        ["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--", "backend/app/data"], check=True
    )
    old_paths = list(app.__path__)
    old_modules = {
        key: value for key, value in sys.modules.items() if key == "app.data" or key.startswith("app.data.")
    }
    old_data_module = getattr(app, "data", None)
    app.__path__ = old_paths + [str(source / "backend/app")]
    database = None
    container = None
    try:
        from app.data.adapter import PostgresData
        from app.data.database import ConnectionConfig, PostgresDatabase
        from app.data.settings import DataSettings

        name = "paperpilot-d-step2-" + uuid4().hex[:12]
        environment = dict(os.environ, POSTGRES_PASSWORD=secrets.token_urlsafe(32))
        result = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--publish",
                "127.0.0.1::5432",
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_DB=paperpilot_d_step2",
                IMAGE,
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        container = result.stdout.strip()
        if len(container) != 64 or any(c not in "0123456789abcdef" for c in container):
            pytest.fail("Docker did not return a container identity")
        binding = subprocess.run(
            ["docker", "port", container, "5432/tcp"], check=True, capture_output=True, text=True
        ).stdout.strip()
        config = ConnectionConfig(
            host="127.0.0.1",
            port=int(binding.rsplit(":", 1)[1]),
            user="postgres",
            password=environment["POSTGRES_PASSWORD"],
            dbname="paperpilot_d_step2",
            connect_timeout=1,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(**config.conninfo(), autocommit=True) as ready:
                    assert 160000 <= ready.info.server_version < 170000
                    record_testsuite_property("security_postgres_server_version", ready.info.server_version)
                    record_testsuite_property("security_postgres_a_commit", A_COMMIT)
                    record_testsuite_property("security_postgres_image", IMAGE)
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("Dedicated PostgreSQL test container did not become ready")
                time.sleep(0.2)
        database = PostgresDatabase(config)
        database.migrate()
        database.seed_development_users()
        data = PostgresData(database, DataSettings())
        user = data.user_by_name("alice")
        contexts = []
        for label in ("A", "B"):
            project = data.create_project(user.user_id, ProjectCreate(name=f"D contract {label}"), uuid4())
            contexts.append(Context(user.user_id, project.project_id, uuid4()))

        def paper(ctx, title, year):
            paper_id = uuid4()
            result, _ = data.import_paper(
                ctx,
                title,
                year,
                hashlib.sha256(str(paper_id).encode()).hexdigest(),
                FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id),
                uuid4().hex,
            )
            return result

        first = paper(contexts[0], "two topics, evidence exists", 2024)
        missing = paper(contexts[0], "no topic, no evidence", None)
        removed = paper(contexts[0], "removed", 2023)
        other = paper(contexts[1], "other project private title", 2025)
        ai = MockAI(data)
        while (job := data.claim_job()) is not None:
            data.complete_job(
                job, asyncio.run(ai.process(job.context, job.task.task_id, job.payload["file"]))
            )
        data.create_task(
            contexts[0],
            "evidence",
            {"question": QuestionRequest(question="contract evidence"), "paper_ids": [first.paper_id]},
            uuid4().hex,
        )
        job = data.claim_job()
        # A saved all-insufficient result still counts as evidence existence, not evidence quality.
        data.complete_job(job, asyncio.run(ai.extract(contexts[0], first.paper_id, [])))
        data.remove_paper(contexts[0], removed.paper_id)

        roles = ["d_contract_" + uuid4().hex[:12] for _ in range(3)]
        passwords = [secrets.token_urlsafe(32) for _ in roles]
        with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
            for project_index, title, paper_ids in [
                (0, "rag", [first.paper_id]),
                (0, "security", [first.paper_id, removed.paper_id]),
                (1, "security", [other.paper_id]),
            ]:
                topic_id = uuid4()
                project_id = contexts[project_index].project_id
                admin.execute("INSERT INTO topics VALUES (%s,%s,%s)", (topic_id, project_id, title))
                for paper_id in paper_ids:
                    admin.execute(
                        "INSERT INTO paper_topics VALUES (%s,%s,%s)", (paper_id, topic_id, project_id)
                    )
            source_sql = (root / "backend/app/security/migrations/001_statistics_views.sql").read_text(
                encoding="utf-8"
            )
            admin.execute(source_sql, prepare=False)
            admin.execute("REVOKE CREATE, TEMPORARY ON DATABASE paperpilot_d_step2 FROM PUBLIC")
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            for index, (role, password) in enumerate(zip(roles, passwords)):
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT SELECT ON paperpilot_security.paper_statistics, "
                        "paperpilot_security.paper_topics TO {}"
                    ).format(sql.Identifier(role))
                )
                if index < 2:
                    admin.execute(
                        "INSERT INTO paperpilot_security.project_readers VALUES (%s,%s)",
                        (role, contexts[index].project_id),
                    )

        def connect(index=0):
            return psycopg.connect(
                **(config.conninfo() | {"user": roles[index], "password": passwords[index]}),
                autocommit=True,
                row_factory=dict_row,
            )

        yield SimpleNamespace(
            data=data,
            config=config,
            contexts=contexts,
            roles=roles,
            connect=connect,
            readers={
                ctx.project_id: ReaderConfig(
                    config.host, config.port, config.dbname, roles[index], passwords[index]
                )
                for index, ctx in enumerate(contexts)
            },
            first=first,
            missing=missing,
            removed=removed,
            other=other,
        )
    finally:
        if database is not None:
            database.close()
        if container is not None:
            subprocess.run(
                ["docker", "stop", "--time", "5", container], check=True, capture_output=True, timeout=30
            )
        app.__path__ = old_paths
        for name in list(sys.modules):
            if name == "app.data" or name.startswith("app.data."):
                del sys.modules[name]
        sys.modules.update(old_modules)
        if old_data_module is None:
            if hasattr(app, "data"):
                delattr(app, "data")
        else:
            app.data = old_data_module


@contextmanager
def build_harness(*, tmp_path):
    import psycopg

    from security_contracts.harness import SecurityHarness

    with postgres_workspace() as pg:
        ctx = pg.contexts[0]
        pg.data.add_member(ctx, "bob")
        member = Context(pg.data.user_by_name("bob").user_id, ctx.project_id, uuid4())
        security = build_security(pg.data, readers=pg.readers)
        bad_credentials = {
            ctx.project_id: replace(pg.readers[ctx.project_id], password=secrets.token_urlsafe(32))
        }
        # Actual lock timeout in the configured executor, while by-year remains usable.
        with psycopg.connect(**pg.config.conninfo()) as blocker:
            blocker.execute("LOCK TABLE public.paper_topics IN ACCESS EXCLUSIVE MODE")
            yield SecurityHarness(
                security=security,
                failing_security=security,
                unavailable_security=build_security(pg.data, readers=bad_credentials),
                data=pg.data,
                ctx=ctx,
                member_ctx=member,
                normal_question="按年份统计论文数量",
                rejected_question="不支持的问题",
                failure_question="按研究主题统计论文数量",
                unavailable_question="按年份统计论文数量",
                is_mock=False,
            )
            blocker.rollback()
