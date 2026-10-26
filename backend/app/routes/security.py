from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.auth import project_context
from app.errors import AppError
from app.routes.common import ERRORS
from app.schemas import AuditList, SecureQueryRequest, SecureQueryResult

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["security"], responses=ERRORS)


@router.post("/secure-queries", response_model=SecureQueryResult)
async def secure_query(project_id: UUID, data: SecureQueryRequest, request: Request):
    return await request.app.state.service.secure_query(project_context(request, project_id), data.question)


@router.get("/audit-events", response_model=AuditList)
def audit(
    project_id: UUID,
    request: Request,
    action: str | None = None,
    decision: str | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    ctx = project_context(request, project_id, "audit.read")
    if (
        (since and since.tzinfo is None)
        or (until and until.tzinfo is None)
        or (since and until and since > until)
    ):
        raise AppError(422, "VALIDATION_ERROR", "时间筛选须带时区且起止顺序正确")
    return request.app.state.adapters.data.audit(
        ctx,
        action=action,
        decision=decision,
        actor_id=actor_id,
        since=since,
        until=until,
        offset=offset,
        limit=limit,
    )
