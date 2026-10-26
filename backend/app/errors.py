from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def install_errors(app: FastAPI):
    def response(request, status, code, message):
        rid = str(getattr(request.state, "request_id", uuid4()))
        return JSONResponse(
            status_code=status,
            content={"code": code, "message": message, "request_id": rid},
            headers={"X-Request-ID": rid, "Cache-Control": "no-store"},
        )

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        return response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo Pydantic's input field: it may contain private text or credentials.
        return response(request, 422, "VALIDATION_ERROR", "请求格式或字段值不符合契约")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return response(request, exc.status_code, "HTTP_ERROR", "请求无法处理")

    @app.exception_handler(ResponseValidationError)
    async def invalid_adapter_output(request: Request, exc: ResponseValidationError):
        return response(request, 502, "ADAPTER_CONTRACT_ERROR", "模块响应不符合契约")

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, exc: Exception):
        return response(request, 500, "INTERNAL_ERROR", "服务暂时无法处理请求")
