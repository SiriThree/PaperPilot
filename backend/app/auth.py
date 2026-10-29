import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Request

from app.adapters.ports import Context
from app.errors import AppError


@dataclass
class Session:
    user_id: UUID
    expires_at: datetime


class DevelopmentSessions:
    """B-owned ephemeral development sessions, not password authentication."""

    def __init__(self):
        self.records: dict[str, Session] = {}

    def create(self, user_id):
        token = secrets.token_urlsafe(32)
        self.records[token] = Session(user_id=user_id, expires_at=datetime.now(UTC) + timedelta(hours=8))
        return token

    def current(self, token):
        session = self.records.get(token)
        if not session or session.expires_at <= datetime.now(UTC):
            self.records.pop(token, None)
            raise AppError(401, "UNAUTHENTICATED", "请先登录")
        return session

    def revoke(self, token):
        self.records.pop(token, None)


def current_user(request: Request):
    token = request.cookies.get("pp_session")
    session = (
        request.app.state.sessions.current(token, request.state.request_id)
        if request.app.state.settings.auth_mode == "credentials"
        else request.app.state.sessions.current(token)
    )
    user = request.app.state.adapters.data.user_by_id(session.user_id)
    if user is None:
        raise AppError(401, "UNAUTHENTICATED", "请先登录")
    return user


def project_context(request: Request, project_id: UUID, action="project.read") -> Context:
    user = current_user(request)
    request.state.authenticated_actor_id = user.user_id
    role = request.app.state.adapters.data.role(user.user_id, project_id)
    if role is None:
        raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
    ctx = Context(user_id=user.user_id, project_id=project_id, request_id=request.state.request_id)
    if action in {"members.manage", "paper.remove", "audit.read"} and role != "owner":
        raise AppError(403, "OWNER_REQUIRED", "此操作需要项目负责人权限")
    request.app.state.adapters.security.authorize(ctx, action, role)
    return ctx
