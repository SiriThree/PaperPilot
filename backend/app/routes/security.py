from datetime import datetime
from uuid import UUID
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.auth import project_context
from app.errors import AppError
from app.routes.common import ERRORS
from app.schemas import (
    AuditList,
    SecureQueryRequest,
    SecureQueryResult,
    SecurityExperimentStart,
    SecurityExperimentState,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["security"], responses=ERRORS)


def experiment_context(request, project_id):
    ctx = project_context(request, project_id, "audit.read")
    if request.app.state.security_experiments is not None:
        if (
            request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
            or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or (
                request.method == "POST"
                and (
                    request.headers.get("origin") not in request.app.state.settings.allowed_origins
                    or urlsplit(request.headers.get("origin", "")).hostname
                    not in {"127.0.0.1", "localhost", "::1"}
                )
            )
        ):
            raise AppError(403, "LOCAL_EXPERIMENT_ONLY", "仅允许本机演示页面运行实验")
    return ctx


@router.get("/security-experiments", response_model=SecurityExperimentState)
async def experiment_status(project_id: UUID, request: Request):
    ctx = experiment_context(request, project_id)
    runner = request.app.state.security_experiments
    return runner.current(ctx) if runner is not None else SecurityExperimentState(enabled=False)


@router.post("/security-experiments", response_model=SecurityExperimentState, status_code=202)
async def start_experiment(project_id: UUID, data: SecurityExperimentStart, request: Request):
    ctx = experiment_context(request, project_id)
    runner = request.app.state.security_experiments
    if runner is None:
        raise AppError(503, "EXPERIMENT_DISABLED", "本机实时实验未启用")
    return runner.start(ctx)


@router.post("/secure-queries", response_model=SecureQueryResult)
async def secure_query(project_id: UUID, data: SecureQueryRequest, request: Request):
    try:
        ctx = project_context(request, project_id)
    except AppError as error:
        if error.status in (401, 403, 404):
            request.app.state.service.secure_query_access_denied(
                request.state.request_id,
                getattr(request.state, "authenticated_actor_id", None),
                mock=request.app.state.settings.a_mode == "mock",
            )
        raise
    result = await request.app.state.service.secure_query(ctx, data.question)
    response = JSONResponse(content=result.model_dump(mode="json"))
    if len(response.body) > 256 * 1024:
        raise AppError(503, "QUERY_RESPONSE_TOO_LARGE", "查询结果超过响应大小限制")
    return response


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
    audit_reader = request.app.state.scoped_audit_reader
    if audit_reader is None:
        audit_reader = request.app.state.adapters.data
    return audit_reader.audit(
        ctx,
        action=action,
        decision=decision,
        actor_id=actor_id,
        since=since,
        until=until,
        offset=offset,
        limit=limit,
    )
