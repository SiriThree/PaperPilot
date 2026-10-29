from hashlib import sha256
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.adapters.ports import FileReference
from app.auth import project_context
from app.errors import AppError
from app.routes.common import ERRORS, key, project_reader, project_writer
from app.schemas import Acknowledgement, AuditDetails, PaperDetail, PaperList, Task, UploadResult

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["literature"], responses=ERRORS)

# Only AB-1 failures that establish this import did not commit permit file cleanup.
ROLLED_BACK_IMPORT_ERRORS = {
    "RESOURCE_NOT_FOUND",
    "OWNER_REQUIRED",
    "DATA_CONTRACT_ERROR",
    "DUPLICATE_PAPER",
    "IDENTIFIER_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "DATA_UNAVAILABLE",
}


def cleanup_unadopted(path, request):
    """Only call for this request's file after ownership/rollback is established.

    A failed cleanup leaves an orphan for later reconciliation; it cannot change
    an acknowledged commit or a known rejection into an unknown commit.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logging.getLogger("paperpilot").warning(
            "upload_cleanup_deferred request_id=%s", request.state.request_id
        )


@router.get("/papers", response_model=PaperList)
def list_papers(project_id: UUID, request: Request, year_from: int | None = None, year_to: int | None = None):
    ctx = project_context(request, project_id)
    if year_from is not None and year_to is not None and year_from > year_to:
        raise AppError(422, "VALIDATION_ERROR", "年份范围不正确")
    return PaperList(items=project_reader(request).papers(ctx, year_from, year_to))


@router.get("/papers/{paper_id}", response_model=PaperDetail)
def paper_detail(project_id: UUID, paper_id: UUID, request: Request):
    ctx = project_context(request, project_id)
    a = project_reader(request)
    return PaperDetail(paper=a.paper(ctx, paper_id), sources=a.sources(ctx, paper_id))


@router.post("/papers", response_model=UploadResult, status_code=202)
async def upload_paper(
    project_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=200),
    year: int | None = Form(None, ge=1000, le=3000),
):
    ctx = project_context(request, project_id)
    writer = project_writer(request, ctx)
    payload = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if len(payload) > 20 * 1024 * 1024:
        raise AppError(413, "UPLOAD_TOO_LARGE", "单篇文件上限为 20 MiB")
    if not payload.startswith(b"%PDF-"):
        raise AppError(415, "UNSUPPORTED_FILE", "仅接受 PDF 文件；Mock 不执行真实正文解析")
    ident = key(request)
    request.app.state.service.audit(
        ctx,
        "paper.import_requested",
        "paper",
        execution_status="pending",
        details=AuditDetails(resource_name=title),
    )
    paper_id = uuid4()
    root = request.app.state.settings.storage_dir
    storage_key = f"{paper_id}.pdf"
    path = root / storage_key
    created = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            created = True
            target.write(payload)
    except OSError:
        if created:
            cleanup_unadopted(path, request)
        raise AppError(503, "FILE_STORAGE_UNAVAILABLE", "文件保存失败，尚未提交论文，请稍后重试") from None
    try:
        paper, task = writer.import_paper(
            ctx,
            title,
            year,
            sha256(payload).hexdigest(),
            FileReference(storage_key, paper_id, project_id),
            ident,
        )
    except AppError as exc:
        if exc.code in ROLLED_BACK_IMPORT_ERRORS:
            cleanup_unadopted(path, request)
            raise
        raise AppError(503, "DATA_COMMIT_UNKNOWN", "数据提交状态待确认，请使用原幂等键重试") from None
    except Exception:
        # A lost commit acknowledgement may mean this file has already been adopted by the DB.
        # Preserve it until idempotent replay/reconciliation establishes ownership.
        raise AppError(503, "DATA_COMMIT_UNKNOWN", "数据提交状态待确认，请使用原幂等键重试") from None
    if paper.paper_id != paper_id:
        cleanup_unadopted(path, request)
    request.app.state.service.audit(
        ctx,
        "paper.imported",
        "paper",
        details=AuditDetails(resource_ids=[paper.paper_id], resource_name=paper.title),
    )
    return UploadResult(paper=paper, task=task)


@router.delete("/papers/{paper_id}", response_model=Acknowledgement)
def remove_paper(project_id: UUID, paper_id: UUID, request: Request, confirm_source_impact: bool = False):
    ctx = project_context(request, project_id, "paper.remove")
    paper = request.app.state.adapters.data.paper(ctx, paper_id)
    if not confirm_source_impact:
        raise AppError(
            409, "SOURCE_IMPACT_CONFIRMATION_REQUIRED", "移出后不再参与检索；历史报告保留来源快照，请确认影响"
        )
    details = AuditDetails(resource_ids=[paper_id], resource_name=paper.title)
    request.app.state.service.audit(
        ctx, "paper.remove_requested", "paper", execution_status="pending", details=details
    )
    request.app.state.adapters.data.remove_paper(ctx, paper_id)
    request.app.state.service.audit(ctx, "paper.removed", "paper", details=details)
    return Acknowledgement(message="已移出项目；历史报告快照仍受项目权限控制")


@router.get("/tasks/{task_id}", response_model=Task)
def task_status(project_id: UUID, task_id: UUID, request: Request):
    try:
        return project_reader(request).task(project_context(request, project_id), task_id)
    except AppError:
        raise
    except Exception:
        raise AppError(
            503, "TASK_QUERY_UNAVAILABLE", "任务状态暂时无法查询，请稍后使用原任务编号查询"
        ) from None


@router.post("/tasks/{task_id}/retry", response_model=Task, status_code=202)
def retry_task(project_id: UUID, task_id: UUID, request: Request):
    ctx = project_context(request, project_id)
    request.app.state.service.audit(
        ctx,
        "task.retry_requested",
        "task",
        execution_status="pending",
        details=AuditDetails(resource_ids=[task_id]),
    )
    return request.app.state.adapters.data.retry_task(ctx, task_id)
