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
        mock=True,
        limitations=[
            "development_identity_only",
            "registered_persistence_provider" if s.a_mode != "mock" else "in_memory_data_lost_on_restart",
            "in_memory_sessions_lost_on_restart",
            "synthetic_ai" if s.c_mode == "mock" else "registered_ai_provider_unverified",
            "no_real_sql_gateway" if s.d_mode == "mock" else "registered_security_provider_unverified",
        ],
    )


@router.post("/auth/login", response_model=Identity)
def login(data: LoginRequest, request: Request, response: Response):
    user = request.app.state.adapters.data.user_by_name(data.username)
    request.app.state.service.identity_audit(
        user.user_id if user else None, request.state.request_id, "allowed" if user else "rejected"
    )
    if not user:
        raise AppError(401, "INVALID_IDENTITY", "开发演示账户不存在")
    old = request.cookies.get("pp_session")
    request.app.state.sessions.records.pop(old, None)
    token = request.app.state.sessions.create(user.user_id)
    response.set_cookie(
        "pp_session",
        token,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.environment == "production",
        max_age=28800,
        path="/api/v1",
    )
    return Identity(user=user)


@router.get("/auth/me", response_model=Identity)
def me(request: Request):
    return Identity(user=current_user(request))


@router.post("/auth/logout", response_model=Acknowledgement)
def logout(request: Request, response: Response):
    current_user(request)
    request.app.state.sessions.records.pop(request.cookies.get("pp_session"), None)
    response.delete_cookie("pp_session", path="/api/v1")
    return Acknowledgement(message="已退出开发会话")
