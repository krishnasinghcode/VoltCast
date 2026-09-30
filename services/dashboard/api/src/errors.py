"""Contract error shape (CONTRACT.md §8): {"error": {"code", "message", "details"}} on every failure."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    502: "UPSTREAM_UNAVAILABLE",
    503: "SERVICE_UNAVAILABLE",
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[dict] = None,
                 headers: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


def error_body(code: str, message: str, details: Optional[dict] = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def error_response(status_code: int, code: str, message: str, details: Optional[dict] = None,
                   headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message, details), headers=headers)


def _from_detail(status_code: int, detail: Any) -> dict:
    if isinstance(detail, dict) and "error" in detail:
        return detail
    return error_body(_STATUS_CODES.get(status_code, "ERROR"), str(detail))


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=_from_detail(exc.status_code, exc.detail),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "request validation failed", {"errors": errors})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error: %s", exc)
        return error_response(500, "INTERNAL_ERROR", "internal server error")
