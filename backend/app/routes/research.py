from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.auth import project_context
from app.errors import AppError
from app.routes.common import ERRORS, key, project_reader, project_report_writer
from app.schemas import (
    EvidenceList,
    QuestionRequest,
    ReportSave,
    ReportVersion,
    SearchRequest,
    SearchResult,
    Task,
    VersionList,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["research"], responses=ERRORS)


@router.post("/search", response_model=SearchResult)
async def search(project_id: UUID, data: SearchRequest, request: Request):
    return await request.app.state.service.search(project_context(request, project_id), data)


@router.post("/answers", response_model=Task, status_code=202)
def answer(project_id: UUID, data: QuestionRequest, request: Request):
    return request.app.state.service.question_task(
        project_context(request, project_id), "answer", data, key(request)
    )


@router.post("/papers/{paper_id}/evidence", response_model=Task, status_code=202)
def evidence(project_id: UUID, paper_id: UUID, request: Request):
    ctx = project_context(request, project_id)
    return request.app.state.service.question_task(
        ctx, "evidence", QuestionRequest(question="证据提取", paper_ids=[paper_id]), key(request)
    )


@router.get("/papers/{paper_id}/evidence", response_model=EvidenceList)
def evidence_history(project_id: UUID, paper_id: UUID, request: Request):
    ctx = project_context(request, project_id)
    return EvidenceList(items=request.app.state.adapters.data.evidence(ctx, paper_id))


@router.post("/report-drafts", response_model=Task, status_code=202)
def draft(project_id: UUID, data: QuestionRequest, request: Request):
    return request.app.state.service.question_task(
        project_context(request, project_id), "draft", data, key(request)
    )


@router.post("/reports", response_model=ReportVersion, status_code=201)
def save_report(project_id: UUID, data: ReportSave, request: Request):
    ctx = project_context(request, project_id)
    writer = project_report_writer(request, ctx)
    request.app.state.service.validate_sources(ctx, data.sources, source_data=writer)
    request.app.state.service.audit(ctx, "report.save_requested", "report", execution_status="pending")
    version = writer.save_report(ctx, data, key(request))
    request.app.state.service.audit(ctx, "report.saved", "report")
    return version


@router.get("/reports", response_model=VersionList)
def reports(project_id: UUID, request: Request):
    return VersionList(items=project_reader(request).reports(project_context(request, project_id)))


@router.get("/reports/{report_id}/versions", response_model=VersionList)
def versions(project_id: UUID, report_id: UUID, request: Request):
    return VersionList(
        items=project_reader(request).versions(project_context(request, project_id), report_id)
    )


@router.get(
    "/reports/{report_id}/versions/{version_id}/text",
    response_class=Response,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
)
def export_report(project_id: UUID, report_id: UUID, version_id: UUID, request: Request):
    versions = project_reader(request).versions(project_context(request, project_id), report_id)
    version = next((v for v in versions if v.version_id == version_id), None)
    if not version:
        raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
    return PlainTextResponse(
        version.body, headers={"Content-Disposition": 'attachment; filename="report.txt"'}
    )
