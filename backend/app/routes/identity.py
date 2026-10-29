import secrets

from fastapi import APIRouter, Request, Response

from app.auth import current_user
from app.errors import AppError
from app.routes.common import ERRORS
from app.schemas import Acknowledgement, CSRFToken, ErrorResponse, Health, Identity, LoginRequest

router = APIRouter(prefix="/api/v1", tags=["identity"], responses=ERRORS)


@router.get("/health", response_model=Health)
def health(request: Request):
    s = request.app.state.settings
    worker = request.app.state.worker_task
    if (
        request.app.state.service.worker_claim_unavailable
        or (worker is not None and worker.done())
        or any(service.worker_claim_unavailable for service in request.app.state.scoped_worker_services)
        or any(task.done() for task in request.app.state.scoped_worker_tasks)
    ):
        raise AppError(503, "WORKER_UNAVAILABLE", "后台任务处理暂不可用")
    executor = request.app.state.restricted_executor
    if executor is not None and (not executor._ready or executor._closed):
        raise AppError(503, "SECURITY_EXECUTOR_UNAVAILABLE", "安全查询执行器尚未就绪")
    if s.a_mode != "mock":
        try:
            if request.app.state.data_binding.ready() is False:
                raise RuntimeError("Provider not ready")
        except Exception:
            raise AppError(503, "DATA_UNAVAILABLE", "数据服务尚未就绪") from None
    if request.app.state.auth_boundary is not None:
        request.app.state.auth_boundary.ready()
    if request.app.state.scoped_system_audit is not None:
        request.app.state.scoped_system_audit.ready()
    if request.app.state.scoped_audit_reader is not None:
        request.app.state.scoped_audit_reader.ready()
    if request.app.state.scoped_business_reader is not None:
        if not request.app.state.scoped_business_reader.ready():
            raise AppError(503, "DATA_UNAVAILABLE", "项目数据服务尚未就绪")
    audit_ready = request.app.state.scoped_worker_audit_ready
    if audit_ready is not None:
        try:
            if not audit_ready():
                raise RuntimeError("Audit not ready")
        except Exception:
            raise AppError(503, "AUDIT_UNAVAILABLE", "后台审计服务尚未就绪") from None
    return Health(
        modes={"a": s.a_mode, "c": s.c_mode, "d": s.d_mode},
        mock=True,
        limitations=[
            "authentication_credentials" if s.auth_mode == "credentials" else "development_identity_only",
            "registered_persistence_provider" if s.a_mode != "mock" else "in_memory_data_lost_on_restart",
            "persistent_sessions" if s.auth_mode == "credentials" else "in_memory_sessions_lost_on_restart",
            "isolated_auth_database_candidate" if s.auth_database == "isolated" else "shared_auth_database",
            "synthetic_ai" if s.c_mode == "mock" else "registered_ai_provider_unverified",
            "no_real_sql_gateway" if s.d_mode == "mock" else "registered_security_provider_unverified",
            "audit_preflight_required" if s.audit_preflight == "required" else "audit_preflight_disabled",
            "business_privilege_preflight_required"
            if s.business_privilege_preflight == "required"
            else "business_privilege_preflight_disabled",
            "scoped_audit_isolated" if s.scoped_audit == "isolated" else "shared_audit_read",
        ]
        + (
            ["security_executor_candidate_enabled", "security_executor_ready"]
            if executor
            else ["security_executor_not_managed"]
        )
        + (
            ["scoped_business_read_pilot"]
            + (
                ["scoped_worker_mock_pilot"]
                if request.app.state.scoped_worker_services
                else ["worker_disabled"]
            )
            if request.app.state.scoped_business_reader
            else []
        )
        + (
            ["scoped_business_upload_pilot"]
            if request.app.state.scoped_business_reader
            and request.app.state.scoped_business_reader.supports_upload
            else []
        )
        + (
            ["scoped_business_report_pilot"]
            if request.app.state.scoped_business_reader
            and request.app.state.scoped_business_reader.supports_reports
            else []
        ),
    )


@router.get("/auth/csrf", response_model=CSRFToken)
def csrf(request: Request, response: Response):
    token = request.cookies.get("pp_csrf", "")
    if len(token) < 32 or len(token) > 128 or not token.isascii():
        token = secrets.token_urlsafe(32)
    response.set_cookie(
        "pp_csrf",
        token,
        httponly=False,
        samesite="strict",
        secure=request.app.state.settings.environment == "production",
        path="/api/v1",
    )
    return CSRFToken(csrf_token=token)


def identity(user, request):
    if request.app.state.settings.auth_mode == "credentials":
        return Identity(user=user, mock=False, authentication="authentication_credentials")
    return Identity(user=user)


@router.post(
    "/auth/login",
    response_model=Identity,
    responses={429: {"model": ErrorResponse, "description": "Login rate limit exceeded"}},
)
def login(data: LoginRequest, request: Request, response: Response):
    sessions = request.app.state.sessions
    if request.app.state.settings.auth_mode == "credentials":
        sessions.check_login_rate(request.client.host if request.client else None, request.state.request_id)
        user_id = sessions.authenticate(data.username, data.password or "")
        user = request.app.state.adapters.data.user_by_id(user_id) if user_id else None
    else:
        user = request.app.state.adapters.data.user_by_name(data.username)
    request.app.state.service.identity_audit(
        user.user_id if user else None,
        request.state.request_id,
        "allowed" if user else "rejected",
        mock=request.app.state.settings.auth_mode != "credentials",
    )
    if not user:
        raise AppError(
            401,
            "INVALID_IDENTITY",
            "账号或密码错误"
            if request.app.state.settings.auth_mode == "credentials"
            else "开发演示账户不存在",
        )
    old = request.cookies.get("pp_session")
    token = (
        sessions.create(user.user_id, data.password or "", request.state.request_id, old)
        if request.app.state.settings.auth_mode == "credentials"
        else _create_development_session(sessions, user.user_id, old)
    )
    response.set_cookie(
        "pp_session",
        token,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.environment == "production",
        max_age=request.app.state.settings.session_absolute_seconds,
        path="/api/v1",
    )
    return identity(user, request)


def _create_development_session(sessions, user_id, old):
    sessions.revoke(old)
    return sessions.create(user_id)


@router.get("/auth/me", response_model=Identity)
def me(request: Request):
    return identity(current_user(request), request)


@router.post("/auth/logout", response_model=Acknowledgement)
def logout(request: Request, response: Response):
    user = current_user(request)
    if request.app.state.settings.auth_mode == "credentials":
        request.app.state.service.session_logout_audit(user.user_id, request.state.request_id)
    token = request.cookies.get("pp_session")
    if request.app.state.settings.auth_mode == "credentials":
        request.app.state.sessions.revoke(token, request.state.request_id, user.user_id)
    else:
        request.app.state.sessions.revoke(token)
    response.delete_cookie("pp_session", path="/api/v1")
    return Acknowledgement(message="已退出会话")
