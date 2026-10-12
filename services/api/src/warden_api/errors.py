"""RFC 7807 Problem Details error formatting and global exception handling."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from structlog.contextvars import get_contextvars


class ProblemDetail(BaseModel):
    """RFC 7807 problem details specification model."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None


class APIError(Exception):
    """Base exception for application-level API errors producing RFC 7807 responses."""

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str,
        type_: str = "about:blank",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.type_ = type_
        self.headers = headers or {}


def _get_request_id(request: Request) -> str | None:
    """Extract request_id from structlog contextvars or request headers."""
    ctx = get_contextvars()
    req_id = ctx.get("request_id")
    if req_id and isinstance(req_id, str):
        return req_id
    return request.headers.get("x-request-id")


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Handle custom APIError exceptions with application/problem+json."""
    instance = _get_request_id(request)
    problem = ProblemDetail(
        type=exc.type_,
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=instance,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        headers={"content-type": "application/problem+json", **exc.headers},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle standard FastAPI/Starlette HTTPExceptions in RFC 7807 shape."""
    instance = _get_request_id(request)
    title_map = {
        400: "Bad Request",
        401: "Unauthorized",
        402: "Payment Required",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        412: "Precondition Failed",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    title = title_map.get(exc.status_code, "HTTP Error")
    detail_str = str(exc.detail) if exc.detail else title

    problem = ProblemDetail(
        type="about:blank",
        title=title,
        status=exc.status_code,
        detail=detail_str,
        instance=instance,
    )
    headers = {"content-type": "application/problem+json"}
    if exc.headers:
        headers.update(exc.headers)

    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Override FastAPI RequestValidationError to output RFC 7807 ProblemDetail."""
    instance = _get_request_id(request)
    error_messages: list[str] = []
    for err in exc.errors():
        loc = " -> ".join(str(item) for item in err.get("loc", []))
        msg = err.get("msg", "Validation error")
        error_messages.append(f"{loc}: {msg}" if loc else msg)

    detail_str = "; ".join(error_messages) if error_messages else "Request validation failed"

    problem = ProblemDetail(
        type="about:blank",
        title="Unprocessable Entity",
        status=422,
        detail=detail_str,
        instance=instance,
    )
    return JSONResponse(
        status_code=422,
        content=problem.model_dump(exclude_none=True),
        headers={"content-type": "application/problem+json"},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all RFC 7807 exception handlers on the FastAPI application."""
    app.add_exception_handler(APIError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
