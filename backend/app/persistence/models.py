"""Row/JSON mapping. SQL columns hold constraints; JSON stores typed result/snapshot payloads."""

import json
from uuid import UUID

from app.adapters.ports import Context, FileReference, Job
from app.schemas import Paper, QuestionRequest, Source, Task, User


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


def task(row):
    return Task(
        task_id=row["task_id"],
        project_id=row["project_id"],
        request_id=row["request_id"],
        kind=row["kind"],
        status=row["status"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error=json.loads(row["error_json"]) if row["error_json"] else None,
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        result_ref=json.loads(row["result_ref_json"]) if row["result_ref_json"] else None,
        mock=False,
    )


def job(row):
    raw = json.loads(row["payload_json"])
    payload = {"paper_ids": [UUID(value) for value in raw["paper_ids"]]}
    if "file" in raw:
        data = raw["file"]
        payload["file"] = FileReference(data["storage_key"], UUID(data["paper_id"]), UUID(data["project_id"]))
    if "question" in raw:
        payload["question"] = QuestionRequest.model_validate(raw["question"])
    return Job(
        task(row), Context(UUID(row["actor_id"]), UUID(row["project_id"]), UUID(row["request_id"])), payload
    )
