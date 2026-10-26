import re
from uuid import uuid4

from fastapi import Request

from app.errors import AppError
from app.schemas import ErrorResponse

ERRORS = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        400: "Invalid request",
        401: "Login required",
        403: "Role or CSRF check failed",
        404: "Resource missing or inaccessible",
        409: "Conflict",
        413: "Upload too large",
        415: "Unsupported file",
        422: "Schema validation failed",
        500: "Internal error",
        502: "Adapter contract error",
        503: "Required module unavailable",
    }.items()
}


def key(request: Request):
    value = request.headers.get("Idempotency-Key") or str(uuid4())
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise AppError(400, "INVALID_IDEMPOTENCY_KEY", "幂等键格式不正确")
    return value
