"""uvicorn app.security.bootstrap:create_app --factory --app-dir backend (A/C modules required)."""

from functools import partial

from app.adapters.data_binding import DataBinding
from app.config import Settings
from app.main import create_app as create_business_app
from app.security.adapter import build_security
from app.security.audit import compose_audit
from app.security.executor import verify_reader
from app.security.settings import SecuritySettings


def create_app():
    data_binding = audit_binding = None

    def close_resources():
        try:
            if audit_binding is not None:
                audit_binding.close()
        finally:
            if data_binding is not None and data_binding.close is not None:
                data_binding.close()

    try:
        settings = Settings.from_env()
        settings.validate()
        if settings.a_mode not in ("persistent", "real") or settings.d_mode != "real":
            raise ValueError("Explicit real A/D modes are required")
        configuration = SecuritySettings.from_env()
        from app.data.binding import compose_data_binding
        from app.data.database import ConnectionConfig
        from app.data.settings import DataSettings
        import psycopg

        data_config = ConnectionConfig.from_env()
        audit_config, readers = configuration.resolve(data_config)
        data_binding = compose_data_binding(data_config, DataSettings.from_env())
        audit_binding = compose_audit(audit_config)
        # Readiness checks inspect identity/ACL metadata, never run an unaudited statistics query.
        for reader in readers.values():
            with psycopg.connect(
                host=reader.host,
                port=reader.port,
                dbname=reader.dbname,
                user=reader.user,
                password=reader.password,
                connect_timeout=3,
                options="-c default_transaction_read_only=on -c statement_timeout=3000",
                autocommit=True,
            ) as connection:
                verify_reader(connection, reader.user)

        binding = DataBinding(data=data_binding.data, ready=data_binding.ready, close=close_resources)
        security_factory = partial(build_security, readers=readers, audit_append=audit_binding.append)
        if settings.c_mode == "real":
            from app.ai.bootstrap import create_app as create_real_c_app

            return create_real_c_app(settings, data_binding=binding, security_factory=security_factory)
        return create_business_app(settings, data_binding=binding, security_factory=security_factory)
    except Exception:
        try:
            close_resources()
        except Exception:
            pass
        raise RuntimeError("Security composition failed; startup refused; no Mock fallback") from None
