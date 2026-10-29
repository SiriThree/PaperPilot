"""Explicit development composition; database setup remains an operator action."""

import logging
import os

from app.config import Settings
from app.main import create_app as create_business_app

LOGGER = logging.getLogger("paperpilot")


def create_app(settings=None):
    settings = settings or Settings.from_env()
    settings.validate()
    binding = None
    data_settings = None
    created = []
    auth_database = None
    try:
        if settings.a_mode != "mock":
            from app.data.binding import compose_data_binding
            from app.data.database import ConnectionConfig
            from app.data.settings import DataSettings

            data_settings = DataSettings.from_env()
            binding = compose_data_binding(ConnectionConfig.from_env(), data_settings)

        if settings.auth_database == "isolated":
            from app.data.authentication_boundary import auth_config_from_env
            from app.data.database import ConnectionConfig, PostgresDatabase

            auth_config = auth_config_from_env(os.environ, ConnectionConfig.from_env())
            auth_database = PostgresDatabase(auth_config)
            created.append(auth_database)

        ai_factory = None
        if settings.c_mode == "real":
            from app.ai.adapter import RealAI
            from app.ai.files import LocalPDFReader
            from app.ai.settings import AISettings

            configuration = AISettings.from_env()
            if data_settings is not None and configuration.timeout_seconds >= data_settings.lease_seconds:
                raise RuntimeError("AI operation deadline must be shorter than the task lease")

            def ai_factory(data):
                adapter = RealAI(data, configuration, reader=LocalPDFReader(settings.storage_dir))
                created.append(adapter)
                return adapter

        security_factory = None
        executor = None
        configurations = []
        scoped_configs = []
        scoped_audit_reader = None
        if settings.scoped_audit == "isolated":
            if settings.audit_preflight != "required":
                raise RuntimeError("Scoped audit composition requires enforced business audit preflight")
            from app.data.database import ConnectionConfig
            from app.data.scoped_audit import ScopedAuditRouter, scoped_audit_configurations

            data_connection = ConnectionConfig.from_env()
            scoped_configs = scoped_audit_configurations(os.environ, data_connection)
            if auth_database is not None and any(c.user == auth_database.config.user for c in scoped_configs):
                raise RuntimeError("Authentication and audit identities must differ")
            scoped_audit_reader = ScopedAuditRouter(scoped_configs)
        if settings.d_mode == "real":
            from app.security import build_security

            if settings.security_execution == "candidate":
                from app.data.database import ConnectionConfig
                from app.security.adapter import SecurityAdapter
                from app.security.restricted_executor import (
                    RestrictedExecutor,
                    RestrictedExecutorRouter,
                    restricted_configurations,
                )

                configurations = restricted_configurations(os.environ)
                data_connection = ConnectionConfig.from_env()
                for restricted in configurations:
                    if auth_database is not None and restricted.user == auth_database.config.user:
                        raise RuntimeError("Authentication and restricted identities must differ")
                    if restricted.user == data_connection.user:
                        raise RuntimeError("Restricted identity must differ from the data identity")
                    if (restricted.host, restricted.port, restricted.dbname) != (
                        data_connection.host,
                        data_connection.port,
                        data_connection.dbname,
                    ):
                        raise RuntimeError(
                            "Restricted and data connections must target the same isolated database"
                        )
                executor = (
                    RestrictedExecutorRouter(configurations)
                    if os.environ.get("PAPERPILOT_SECURITY_DB_BINDINGS")
                    or os.environ.get("PAPERPILOT_SECURITY_DB_BINDINGS_FILE")
                    else RestrictedExecutor(configurations[0])
                )
                created.append(executor)

            def security_factory(data):
                adapter = SecurityAdapter(data, query_executor=executor) if executor else build_security(data)
                created.append(adapter)
                return adapter

        scoped_business_reader = None
        business_prefix = "PAPERPILOT_SCOPED_BUSINESS_"
        if any(key.startswith(business_prefix) and value for key, value in os.environ.items()):
            if settings.environment != "test" or settings.a_mode not in {"persistent", "real"}:
                raise RuntimeError(
                    "Scoped business configuration requires isolated test mode and persistent data"
                )
            from app.data.database import ConnectionConfig
            from app.data.scoped_business_config import (
                build_scoped_business_router,
                scoped_business_configurations,
            )

            forbidden = {config.user for config in scoped_configs} | {
                config.user for config in configurations
            }
            if auth_database is not None:
                forbidden.add(auth_database.config.user)
            mappings = scoped_business_configurations(
                os.environ, ConnectionConfig.from_env(), forbidden_users=forbidden
            )
            scoped_business_reader = build_scoped_business_router(mappings)
            created.append(scoped_business_reader)

        app = create_business_app(
            settings,
            data_binding=binding,
            ai_factory=ai_factory,
            security_factory=security_factory,
            restricted_executor=executor,
            scoped_audit_reader=scoped_audit_reader,
            scoped_business_reader=scoped_business_reader,
            auth_database=auth_database,
        )
        return app
    except BaseException:
        for resource in reversed(created):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    LOGGER.warning("adapter_cleanup_failed")
        if binding is not None and binding.close is not None:
            try:
                binding.close()
            except BaseException:
                LOGGER.warning("data_provider_cleanup_failed")
        raise
