import asyncio
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager, suppress
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.factory import AIFactory, SecurityFactory, create_adapters
from app.adapters.data_binding import DataBinding
from app.auth import DevelopmentSessions
from app.config import Settings
from app.errors import install_errors
from app.routes import identity, literature, projects, research, security
from app.services import ResearchService


def create_app(
    settings: Settings | None = None,
    *,
    data_binding: DataBinding | None = None,
    ai_factory: AIFactory | None = None,
    security_factory: SecurityFactory | None = None,
    restricted_executor=None,
    scoped_audit_reader=None,
    scoped_business_reader=None,
    auth_database=None,
    scoped_workers=(),
    scoped_worker_audit=None,
    scoped_worker_audit_ready=None,
    scoped_system_audit=None,
):
    settings = settings or Settings.from_env()
    scoped_workers = tuple(scoped_workers)
    if scoped_system_audit is not None:
        from app.data.scoped_system_audit import ScopedSystemAuditWriter

        data_config = getattr(getattr(getattr(data_binding, "data", None), "database", None), "config", None)
        system_config = getattr(scoped_system_audit, "config", None)
        if (
            not isinstance(scoped_system_audit, ScopedSystemAuditWriter)
            or settings.environment != "test"
            or settings.auth_mode != "credentials"
            or settings.a_mode != "persistent"
            or data_config is None
            or (data_config.host, data_config.port, data_config.dbname)
            != (system_config.host, system_config.port, system_config.dbname)
            or system_config.user == data_config.user
            or (auth_database is not None and system_config.user == auth_database.config.user)
        ):
            raise RuntimeError("System audit candidate requires separate isolated credentials")
    if scoped_business_reader is not None and settings.environment != "test":
        raise RuntimeError("Scoped business read pilot requires isolated test mode")
    if scoped_workers:
        if (
            settings.environment != "test"
            or settings.c_mode != "mock"
            or scoped_business_reader is None
            or not callable(scoped_worker_audit)
        ):
            raise RuntimeError("Scoped workers require an isolated pilot, Mock AI and explicit audit sink")
        business_database = getattr(getattr(data_binding, "data", None), "database", None)
        business_config = getattr(business_database, "config", None)
        auth_config = getattr(auth_database, "config", None)
        forbidden_roles = {getattr(business_config, "user", None), getattr(auth_config, "user", None)}
        roles, scopes = set(), set()
        for scoped_worker in scoped_workers:
            scoped_business_reader.validate_worker(scoped_worker)
            config = scoped_worker.config
            scope = (config.user_id, config.project_id)
            if (
                config.connection.user in forbidden_roles
                or config.connection.user in roles
                or scope in scopes
            ):
                raise RuntimeError("Duplicate scoped worker identity")
            roles.add(config.connection.user)
            scopes.add(scope)
        if scoped_worker_audit_ready is not None and not callable(scoped_worker_audit_ready):
            raise RuntimeError("Invalid scoped worker audit readiness check")
    elif scoped_worker_audit is not None or scoped_worker_audit_ready is not None:
        raise RuntimeError("Scoped worker audit sink configured without workers")
    if (settings.security_execution == "candidate") != (restricted_executor is not None):
        raise RuntimeError("Security execution configuration and registration do not match")
    if (settings.scoped_audit == "isolated") != (scoped_audit_reader is not None):
        raise RuntimeError("Scoped audit configuration and registration do not match")
    if (settings.auth_database == "isolated") != (auth_database is not None):
        raise RuntimeError("Authentication database configuration and registration do not match")
    logger = logging.getLogger("paperpilot")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.WARNING if settings.environment == "test" else logging.INFO)
    settings.validate()
    if settings.audit_preflight == "required" and data_binding is not None:
        # Apply at the common business entry, including explicitly injected bindings.
        # The trusted identity comes from operator configuration, never the database itself.
        from app.data.audit_boundary import verify_audit_boundary

        original = data_binding
        business_database = getattr(original.data, "database", None)
        if not callable(getattr(business_database, "transaction", None)):
            raise RuntimeError("Audit preflight requires a PostgreSQL database binding")
        if settings.business_privilege_preflight == "required":
            from app.data.business_boundary import verify_business_privileges
            from app.errors import AppError

            def enforce_business_privileges(connection):
                try:
                    verify_business_privileges(connection)
                except Exception:
                    raise AppError(503, "DATA_UNAVAILABLE", "业务数据库权限配置不可用") from None

            register_guard = getattr(business_database, "set_transaction_guard", None)
            if not callable(register_guard):
                raise RuntimeError("Business database transaction guard unavailable")
            register_guard(enforce_business_privileges)

        def checked_ready():
            if original.ready() is False:
                return False
            with business_database.transaction() as connection:
                if settings.scoped_audit == "isolated":
                    verify_audit_boundary(
                        connection, trusted_owner=settings.audit_trusted_owner, scoped_read=True
                    )
                else:
                    verify_audit_boundary(connection, trusted_owner=settings.audit_trusted_owner)
            return True

        data_binding = DataBinding(original.data, checked_ready, original.close)
    adapters = create_adapters(
        settings, data_binding=data_binding, ai_factory=ai_factory, security_factory=security_factory
    )

    @asynccontextmanager
    async def lifespan(app):
        logging.getLogger("paperpilot").warning(
            "DEVELOPMENT: adapter_modes a=%s c=%s d=%s; identity=%s; audit_preflight=%s; production_unverified",
            settings.a_mode,
            settings.c_mode,
            settings.d_mode,
            settings.auth_mode,
            settings.audit_preflight,
        )
        worker = None
        isolated_tasks = []
        try:
            if app.state.auth_boundary is not None:
                app.state.auth_boundary.ready()
            if scoped_system_audit is not None and not scoped_system_audit.ready():
                raise RuntimeError("System audit readiness failed")
            executor = app.state.restricted_executor
            if executor is not None and not await executor.ready():
                raise RuntimeError("Restricted executor readiness failed")
            if scoped_audit_reader is not None and not scoped_audit_reader.ready():
                raise RuntimeError("Scoped audit readiness failed")
            if scoped_business_reader is not None and not scoped_business_reader.ready():
                raise RuntimeError("Scoped business readiness failed")
            if scoped_business_reader is None:
                worker = asyncio.create_task(app.state.service.worker())
                app.state.worker_task = worker
            else:
                if scoped_worker_audit_ready is not None and not scoped_worker_audit_ready():
                    raise RuntimeError("Scoped worker audit readiness failed")
                for scoped_worker in scoped_workers:
                    if not scoped_worker.ready():
                        raise RuntimeError("Scoped worker readiness failed")
                for service in app.state.scoped_worker_services:
                    isolated_tasks.append(asyncio.create_task(service.worker()))
                app.state.scoped_worker_tasks = isolated_tasks
            yield
        finally:
            if app.state.security_experiments is not None:
                await app.state.security_experiments.close()
            for task in isolated_tasks:
                task.cancel()
            for task in isolated_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning("scoped_worker_shutdown_failed")
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            for scoped_worker in reversed(scoped_workers):
                try:
                    scoped_worker.close()
                except Exception:
                    logger.warning("scoped_worker_shutdown_failed")
            if scoped_system_audit is not None:
                try:
                    scoped_system_audit.close()
                except Exception:
                    logger.warning("system_audit_shutdown_failed")
            if app.state.restricted_executor is not None:
                try:
                    app.state.restricted_executor.close()
                except Exception:
                    logger.warning("security_executor_shutdown_failed")
            if auth_database is not None:
                try:
                    auth_database.close()
                except Exception:
                    logger.warning("authentication_database_shutdown_failed")
            if scoped_business_reader is not None:
                try:
                    scoped_business_reader.close()
                except Exception:
                    logger.warning("scoped_business_shutdown_failed")
            if data_binding is not None and data_binding.close is not None:
                try:
                    data_binding.close()
                except Exception:
                    logger.warning("data_provider_shutdown_failed")

    app = FastAPI(
        title="PaperPilot Secure — ADAPTER DEVELOPMENT"
        if any(mode != "mock" for mode in (settings.a_mode, settings.c_mode, settings.d_mode))
        else "PaperPilot Secure Phase 1 — MOCK",
        version="0.1.0",
        lifespan=lifespan,
        description="Explicitly registered adapters; authentication mode is configurable; production unverified."
        if any(mode != "mock" for mode in (settings.a_mode, settings.c_mode, settings.d_mode))
        else "Development contracts only. No real AI, durable database or SQL security gateway.",
    )
    app.state.settings = settings
    app.state.security_experiments = None
    if settings.security_experiments:
        from app.security.experiment import LocalSecurityExperiments

        app.state.security_experiments = LocalSecurityExperiments()
    app.state.adapters = adapters
    app.state.data_binding = data_binding
    app.state.restricted_executor = restricted_executor
    app.state.scoped_audit_reader = scoped_audit_reader
    app.state.scoped_business_reader = scoped_business_reader
    app.state.scoped_worker_audit_ready = scoped_worker_audit_ready
    app.state.scoped_system_audit = scoped_system_audit
    app.state.auth_boundary = None
    if settings.auth_mode == "credentials":
        from app.data.authentication import PostgresSessions

        database = getattr(data_binding.data, "database", None) if data_binding else None
        if database is None:
            raise RuntimeError("Credential session store not registered; startup refused")
        if auth_database is not None:
            from app.data.authentication_boundary import AuthenticationBoundary

            app.state.auth_boundary = AuthenticationBoundary(
                database, auth_database, trusted_lock_owner=settings.auth_lock_owner
            )
            database = app.state.auth_boundary
        app.state.sessions = PostgresSessions(
            database,
            idle_seconds=settings.session_idle_seconds,
            absolute_seconds=settings.session_absolute_seconds,
            login_limit=settings.auth_login_limit,
            login_window=settings.auth_login_window_seconds,
        )
    else:
        app.state.sessions = DevelopmentSessions()
    data_settings = getattr(adapters.data, "settings", None)
    lease = getattr(data_settings, "lease_seconds", settings.lease_seconds)

    def security_audit_ready(ctx):
        if settings.audit_preflight == "required" and data_binding.ready() is False:
            return False
        if scoped_audit_reader is not None:
            project_check = getattr(scoped_audit_reader, "ready_for_project", None)
            if (
                project_check(ctx.project_id) if callable(project_check) else scoped_audit_reader.ready()
            ) is False:
                return False
        return True

    app.state.service = ResearchService(
        adapters,
        job_timeout_seconds=min(90.0, lease * 0.8),
        system_audit=scoped_system_audit.append_audit if scoped_system_audit is not None else None,
        audit_ready=security_audit_ready
        if settings.audit_preflight == "required" or scoped_audit_reader is not None
        else None,
    )
    app.state.worker_task = None
    app.state.scoped_worker_tasks = []
    if scoped_workers:
        from app.data.scoped_worker import isolated_worker_service

        app.state.scoped_worker_services = [
            isolated_worker_service(worker, scoped_worker_audit, job_timeout_seconds=min(90.0, lease * 0.8))
            for worker in scoped_workers
        ]
    else:
        app.state.scoped_worker_services = []
    install_errors(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        try:
            request.state.request_id = UUID(request.headers.get("X-Request-ID", ""))
        except ValueError:
            request.state.request_id = uuid4()
        rid = str(request.state.request_id)
        start = perf_counter()
        denied = None
        if scoped_business_reader is not None:
            # Unsupported routes must not use the shared business identity while
            # the isolated read/upload pilot is under trial.
            path = request.url.path
            identity_path = path in {
                "/api/v1/health",
                "/api/v1/auth/csrf",
                "/api/v1/auth/login",
                "/api/v1/auth/logout",
                "/api/v1/auth/me",
            }
            scoped_read = request.method == "GET" and re.fullmatch(
                r"/api/v1/projects/[^/]+/(?:papers(?:/[^/]+)?|tasks/[^/]+|reports(?:/[^/]+/versions(?:/[^/]+/text)?)?)",
                path,
            )
            scoped_upload = (
                scoped_business_reader.supports_upload
                and request.method == "POST"
                and re.fullmatch(r"/api/v1/projects/[^/]+/papers", path)
            )
            scoped_report = (
                scoped_business_reader.supports_reports
                and request.method == "POST"
                and re.fullmatch(r"/api/v1/projects/[^/]+/reports", path)
            )
            if (
                path.startswith("/api/v1/")
                and request.method != "OPTIONS"
                and not (identity_path or scoped_read or scoped_upload or scoped_report)
            ):
                denied = (503, "DATA_UNAVAILABLE", "项目隔离测试模式暂不支持此操作")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if settings.auth_mode == "credentials":
                csrf_cookie = request.cookies.get("pp_csrf", "")
                csrf_header = request.headers.get("X-CSRF-Token", "")
                if (
                    origin not in settings.allowed_origins
                    or not csrf_cookie
                    or not csrf_cookie.isascii()
                    or not csrf_header.isascii()
                    or not secrets.compare_digest(csrf_cookie, csrf_header)
                ):
                    denied = (403, "CSRF_REJECTED", "请求来源校验失败")
            elif request.headers.get("X-PaperPilot-Client") != "web" or (
                origin is not None and origin not in settings.allowed_origins
            ):
                denied = (403, "CSRF_REJECTED", "请求来源校验失败")
        length = request.headers.get("Content-Length", "0")
        if not length.isdigit():
            denied = (400, "VALIDATION_ERROR", "请求长度格式不正确")
        elif int(length) > 21 * 1024 * 1024:
            denied = (413, "UPLOAD_TOO_LARGE", "请求体超过允许大小")
        if denied:
            status, code, message = denied
            response = JSONResponse(
                status_code=status, content={"code": code, "message": message, "request_id": rid}
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                # Keep provider/storage exceptions out of both responses and server trace logs.
                response = JSONResponse(
                    status_code=500,
                    content={"code": "INTERNAL_ERROR", "message": "服务暂时无法处理请求", "request_id": rid},
                )
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        logging.getLogger("paperpilot.request").info(
            json.dumps(
                {
                    "request_id": rid,
                    "method": request.method,
                    "route": getattr(route, "path", "unmatched"),
                    "status": response.status_code,
                    "duration_ms": round((perf_counter() - start) * 1000, 2),
                }
            )
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Token",
            "X-PaperPilot-Client",
            "X-Request-ID",
            "Idempotency-Key",
        ],
    )
    for router in (identity.router, projects.router, literature.router, research.router, security.router):
        app.include_router(router)
    original_openapi = app.openapi

    def contract_openapi():
        document = original_openapi()
        document["components"].setdefault("securitySchemes", {})["DevelopmentSession"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "pp_session",
            "description": "Session cookie. Authentication mode is reported by the health endpoint; development sessions are not credential authentication.",
        }
        for path, operations in document["paths"].items():
            for method, operation in operations.items():
                if method not in {"get", "post", "delete", "put", "patch"}:
                    continue
                parameters = operation.setdefault("parameters", [])
                if not any(p.get("name") == "X-Request-ID" for p in parameters):
                    parameters.append(
                        {
                            "name": "X-Request-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string", "format": "uuid"},
                            "description": "Optional correlation only; never an authorization credential. Invalid values are replaced.",
                        }
                    )
                    if method != "get":
                        parameters.append(
                            {
                                "name": "X-PaperPilot-Client",
                                "in": "header",
                                "required": False,
                                "schema": {"type": "string", "const": "web"},
                                "description": "Development mode request header; not authentication or CSRF protection.",
                            }
                        )
                        parameters.append(
                            {
                                "name": "X-CSRF-Token",
                                "in": "header",
                                "required": False,
                                "schema": {"type": "string"},
                                "description": "Required for state changes in credential mode, together with a trusted Origin and matching cookie.",
                            }
                        )
                    if method == "post" and any(
                        path.endswith(s)
                        for s in ("/papers", "/answers", "/evidence", "/report-drafts", "/reports")
                    ):
                        parameters.append(
                            {
                                "name": "Idempotency-Key",
                                "in": "header",
                                "required": False,
                                "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"},
                                "description": "Reuse the same key and payload when retrying a write; absent key means a new operation.",
                            }
                        )
                if path not in {"/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/csrf"}:
                    operation["security"] = [{"DevelopmentSession": []}]
        return document

    app.openapi = contract_openapi
    return app
