import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
    auth_sessions=None,
):
    settings = settings or Settings.from_env()
    logger = logging.getLogger("paperpilot")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.WARNING if settings.environment == "test" else logging.INFO)
    adapters = create_adapters(
        settings, data_binding=data_binding, ai_factory=ai_factory, security_factory=security_factory
    )
    if settings.auth_mode == "password":
        from app.accounts import PasswordSessions

        if not isinstance(auth_sessions, PasswordSessions):
            raise RuntimeError("Password authentication binding is required; startup refused")
    elif auth_sessions is not None:
        raise RuntimeError("Development login cannot accept password sessions")

    @asynccontextmanager
    async def lifespan(app):
        logging.getLogger("paperpilot").warning(
            "runtime_modes a=%s c=%s d=%s authentication=%s",
            settings.a_mode,
            settings.c_mode,
            settings.d_mode,
            settings.auth_mode,
        )
        worker = asyncio.create_task(app.state.service.worker())
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            if auth_sessions is not None:
                auth_sessions.close()
            if data_binding is not None and data_binding.close is not None:
                try:
                    data_binding.close()
                except Exception:
                    logger.warning("data_provider_shutdown_failed")

    app = FastAPI(
        title="PaperPilot Secure"
        if settings.auth_mode == "password"
        else "PaperPilot Secure — ADAPTER DEVELOPMENT"
        if any(mode != "mock" for mode in (settings.a_mode, settings.c_mode, settings.d_mode))
        else "PaperPilot Secure Phase 1 — MOCK",
        version="0.1.0",
        lifespan=lifespan,
        description="Password authentication with explicitly registered adapters; inspect health for model modes."
        if settings.auth_mode == "password"
        else "Explicitly registered adapters; development identity only; production unverified."
        if any(mode != "mock" for mode in (settings.a_mode, settings.c_mode, settings.d_mode))
        else "Development contracts only. No real AI, durable database or SQL security gateway.",
    )
    app.state.settings = settings
    app.state.adapters = adapters
    app.state.data_binding = data_binding
    app.state.sessions = auth_sessions if auth_sessions is not None else DevelopmentSessions()
    app.state.service = ResearchService(adapters)
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
        if (
            settings.auth_mode == "password"
            and request.url.scheme != "https"
            and request.url.path != "/api/v1/health"
        ):
            denied = (403, "HTTPS_REQUIRED", "密码认证要求 HTTPS 连接")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if request.headers.get("X-PaperPilot-Client") != "web" or (
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

    for router in (identity.router, projects.router, literature.router, research.router, security.router):
        app.include_router(router)
    original_openapi = app.openapi

    def contract_openapi():
        document = original_openapi()
        scheme = "PasswordSession" if settings.auth_mode == "password" else "DevelopmentSession"
        document["components"].setdefault("securitySchemes", {})[scheme] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "pp_session",
            "description": "Persistent server-side password session."
            if settings.auth_mode == "password"
            else "Ephemeral development identity session; not real password authentication.",
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
                                "required": True,
                                "schema": {"type": "string", "const": "web"},
                                "description": "Custom header required by same-origin browser request policy.",
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
                if path not in {"/api/v1/health", "/api/v1/auth/login"}:
                    operation["security"] = [{scheme: []}]
        return document

    app.openapi = contract_openapi
    return app
