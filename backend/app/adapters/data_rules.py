"""Portable internal contract rules. No database, transport or vendor dependencies."""

import json
import re
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.adapters.ports import FileReference
from app.errors import AppError
from app.schemas import AnswerResult, AuditEvent, DraftResult, EvidenceResult, ProcessResult, QuestionRequest


def invalid():
    return AppError(422, "DATA_CONTRACT_ERROR", "数据访问参数不符合契约")


def validate_key(key):
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
        raise invalid()


def canonical(value):
    def encode(item):
        if isinstance(item, AuditEvent) and item.details is None:
            # Preserve pre-details event fingerprints and outbox replay idempotency.
            return item.model_dump(mode="json", exclude={"details"})
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, UUID):
            return str(item)
        raise TypeError("Unsupported internal contract value")

    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def validate_file(ctx, file):
    if not isinstance(file, FileReference) or file.project_id != ctx.project_id:
        raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
    if not isinstance(file.paper_id, UUID) or not isinstance(file.storage_key, str):
        raise invalid()
    key = file.storage_key
    if (
        not key
        or len(key) > 1024
        or key.startswith(("/", "\\"))
        or any(c in key for c in ("\x00", ":", "\\"))
        or ".." in key.split("/")
    ):
        raise invalid()


def normalize_payload(ctx, kind, payload):
    if kind not in {"process", "answer", "evidence", "draft"} or not isinstance(payload, dict):
        raise invalid()
    if set(payload) - {"file", "question", "paper_ids"}:
        raise invalid()
    ids = payload.get("paper_ids")
    if not isinstance(ids, list) or any(not isinstance(pid, UUID) for pid in ids):
        raise invalid()
    ids = sorted(set(ids), key=str)
    if kind == "process":
        file = payload.get("file")
        validate_file(ctx, file)
        if ids != [file.paper_id] or "question" in payload:
            raise invalid()
        return {"file": file, "paper_ids": ids}
    if "file" in payload or "question" not in payload or (kind == "evidence" and len(ids) != 1):
        raise invalid()
    try:
        value = payload["question"]
        if isinstance(value, BaseModel):
            value = value.model_dump()
        question = QuestionRequest.model_validate(value).model_copy(deep=True)
    except ValidationError:
        raise invalid() from None
    if question.paper_ids and set(question.paper_ids) != set(ids):
        raise invalid()
    question.paper_ids = sorted(set(question.paper_ids), key=str)
    return {"question": question, "paper_ids": ids}


def normalize_result(kind, result):
    models = {
        "process": ProcessResult,
        "answer": AnswerResult,
        "evidence": EvidenceResult,
        "draft": DraftResult,
    }
    try:
        # Validate again through JSON-shaped data, even for a constructed/modified model instance.
        value = result.model_dump() if isinstance(result, BaseModel) else result
        return models[kind].model_validate(value).model_copy(deep=True)
    except (KeyError, ValidationError, TypeError):
        raise AppError(502, "ADAPTER_CONTRACT_ERROR", "任务结果不符合数据契约") from None
