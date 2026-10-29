from uuid import UUID

from fastapi import APIRouter, Request

from app.auth import current_user, project_context
from app.routes.common import ERRORS
from app.schemas import (
    Acknowledgement,
    AuditDetails,
    Member,
    MemberInput,
    MemberList,
    Project,
    ProjectCreate,
    ProjectList,
)

router = APIRouter(prefix="/api/v1", tags=["projects"], responses=ERRORS)


@router.get("/projects", response_model=ProjectList)
def list_projects(request: Request):
    return ProjectList(items=request.app.state.adapters.data.projects(current_user(request).user_id))


@router.post("/projects", response_model=Project, status_code=201)
def create_project(data: ProjectCreate, request: Request):
    user = current_user(request)
    project = request.app.state.adapters.data.create_project(user.user_id, data, request.state.request_id)
    ctx = project_context(request, project.project_id)
    request.app.state.service.audit(
        ctx,
        "project.created",
        "project",
        details=AuditDetails(resource_ids=[project.project_id], resource_name=project.name),
    )
    return project


@router.get("/projects/{project_id}/members", response_model=MemberList)
def list_members(project_id: UUID, request: Request):
    ctx = project_context(request, project_id, "members.manage")
    return MemberList(items=request.app.state.adapters.data.members(ctx))


@router.post("/projects/{project_id}/members", response_model=Member, status_code=201)
def add_member(project_id: UUID, data: MemberInput, request: Request):
    ctx = project_context(request, project_id, "members.manage")
    result = request.app.state.adapters.data.add_member(ctx, data.username)
    request.app.state.service.audit(
        ctx,
        "member.added",
        "member",
        details=AuditDetails(resource_ids=[result.user.user_id], resource_name=result.user.display_name),
    )
    return result


@router.delete("/projects/{project_id}/members/{user_id}", response_model=Acknowledgement)
def remove_member(project_id: UUID, user_id: UUID, request: Request):
    ctx = project_context(request, project_id, "members.manage")
    request.app.state.adapters.data.remove_member(ctx, user_id)
    request.app.state.service.audit(
        ctx, "member.removed", "member", details=AuditDetails(resource_ids=[user_id])
    )
    return Acknowledgement(message="成员已移除，后续请求立即重新校验权限")
