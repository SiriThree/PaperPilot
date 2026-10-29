from fastapi import APIRouter, Request, Response

from app.auth import current_user
from app.errors import AppError
from app.routes.common import ERRORS
from app.schemas import Acknowledgement, Health, Identity, LoginRequest

router = APIRouter(prefix="/api/v1", tags=["identity"], responses=ERRORS)


@router.get("/health", response_model=Health)
def health(request: Request):
    s = request.app.state.settings
    if s.a_mode != "mock":
        try:
            if request.app.state.data_binding.ready() is False:
                raise RuntimeError("Provider not ready")
        except Exception:
            raise AppError(503, "DATA_UNAVAILABLE", "数据服务尚未就绪") from None
    return Health(
        modes={"a": s.a_mode, "c": s.c_mode, "d": s.d_mode},
        mock=s.auth_mode == "development" or "mock" in (s.a_mode, s.c_mode, s.d_mode),
        limitations=[
            "password_authentication" if s.auth_mode == "password" else "development_identity_only",
            "registered_persistence_provider" if s.a_mode != "mock" else "in_memory_data_lost_on_restart",
            "persistent_password_sessions"
            if s.auth_mode == "password"
            else "in_memory_sessions_lost_on_restart",
            "synthetic_ai" if s.c_mode == "mock" else "registered_ai_provider_unverified",
            "no_real_sql_gateway" if s.d_mode == "mock" else "registered_security_provider_unverified",
        ],
    )


@router.post("/auth/login", response_model=Identity)
def login(data: LoginRequest, request: Request, response: Response):
    password_mode = request.app.state.settings.auth_mode == "password"
    credential = None
    if password_mode:
        credential = request.app.state.sessions.authenticate(
            data.username,
            data.password.get_secret_value() if data.password is not None else "",
            request.client.host if request.client else "unknown",
        )
        user = request.app.state.adapters.data.user_by_id(credential.user_id) if credential else None
    else:
        if data.password is not None:
            raise AppError(422, "PASSWORD_AUTH_DISABLED", "当前为演示登录，请勿提交真实密码")
        user = request.app.state.adapters.data.user_by_name(data.username)
    request.app.state.service.identity_audit(
        user.user_id if user else None,
        request.state.request_id,
        "allowed" if user else "rejected",
        mock=not password_mode,
    )
    if not user:
        if password_mode:
            raise AppError(401, "INVALID_CREDENTIALS", "账户名或密码不正确")
        raise AppError(401, "INVALID_IDENTITY", "开发演示账户不存在")
    old = request.cookies.get("pp_session")
    if password_mode:
        token = request.app.state.sessions.create_authenticated(credential, old)
    else:
        request.app.state.sessions.revoke(old)
        token = request.app.state.sessions.create(user.user_id)
    response.set_cookie(
        "pp_session",
        token,
        httponly=True,
        samesite="strict",
        secure=password_mode or request.app.state.settings.environment == "production",
        max_age=28800,
        path="/api/v1",
    )
    return Identity(
        user=user,
        mock=not password_mode,
        authentication="password" if password_mode else "development_identity_only",
    )


@router.get("/auth/me", response_model=Identity)
def me(request: Request):
    password_mode = request.app.state.settings.auth_mode == "password"
    return Identity(
        user=current_user(request),
        mock=not password_mode,
        authentication="password" if password_mode else "development_identity_only",
    )


@router.post("/auth/logout", response_model=Acknowledgement)
def logout(request: Request, response: Response):
    current_user(request)
    request.app.state.sessions.revoke(request.cookies.get("pp_session"))
    response.delete_cookie("pp_session", path="/api/v1")
    return Acknowledgement(
        message="已退出登录" if request.app.state.settings.auth_mode == "password" else "已退出开发会话"
    )
