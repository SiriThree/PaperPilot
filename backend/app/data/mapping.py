"""Row-to-contract mapping. All returned objects are detached copies."""

import json
from uuid import UUID

from app.adapters.ports import Context, FileReference, Job
from app.schemas import (
    AnswerResult,
    DraftResult,
    Paper,
    ProcessResult,
    QuestionRequest,
    Source,
    Task,
    TaskError,
    User,
)


def user(row):
    return (
        User(user_id=row["user_id"], username=row["username"], display_name=row["display_name"])
        if row
        else None
    )


def paper(row):
    return Paper(
        paper_id=row["paper_id"],
        project_id=row["project_id"],
        title=row["title"],
        year=row["year"],
        status=row["status"],
        mock=False,
    )


def source(row):
    return Source(
        citation_id=row["citation_id"],
        paper_id=row["paper_id"],
        chunk_id=row["chunk_id"],
        page_number=row["page_number"],
        excerpt=row["excerpt"],
        synthetic=bool(row["synthetic"]),
    )


def result_ref(kind, row):
    resource_id = (
        row["paper_id"] if kind == "process" else row["evidence_id"] if kind == "evidence" else row["task_id"]
    )
    resource_type = "paper" if kind == "process" else kind
    return {"resource_type": resource_type, "resource_id": str(resource_id)}


def task(row, result=None, result_ref_value=None):
    """Assemble the public Task; success results derive from task_results, never stored JSON."""
    return Task(
        task_id=row["task_id"],
        project_id=row["project_id"],
        request_id=row["request_id"],
        kind=row["kind"],
        status=row["status"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error=(
            TaskError(code=row["error_code"], message=row["error_message"])
            if row["error_code"] is not None
            else None
        ),
        result=result,
        result_ref=result_ref_value,
        mock=False,
    )


def result_model(result_row):
    model = {"process": ProcessResult, "answer": AnswerResult, "draft": DraftResult}[result_row["kind"]]
    return model.model_validate(result_row["payload"])


def task_payload(raw):
    """Rebuild the canonical TaskPayload dict from stored jsonb."""
    payload = {"paper_ids": [UUID(value) for value in raw["paper_ids"]]}
    if "file" in raw:
        data = raw["file"]
        payload["file"] = FileReference(data["storage_key"], UUID(data["paper_id"]), UUID(data["project_id"]))
    if "question" in raw:
        payload["question"] = QuestionRequest.model_validate(raw["question"])
    return payload


def job(row):
    return Job(
        task(row),
        Context(UUID(row["actor_id"]), UUID(row["project_id"]), UUID(row["request_id"])),
        task_payload(row["payload"]),
    )


def json_field(row, name):
    value = row[name]
    return json.loads(value) if isinstance(value, str) else value
