"""The single error shape, and the handlers that produce it.

`development/README.md` section 8 defines one error body for the whole API:

    { code, message, field_errors?, current_version?, correlation_id }

Every failure path goes through here, so a client can branch on `code` and
never has to parse prose. Two rules are enforced rather than documented:

- **No stack traces and no personal data leave the process.** An unhandled
  exception is logged with its correlation id and returned as a generic
  message; the detail stays in the log where only an operator sees it.
- **Every error carries a correlation id**, including unexpected ones, so a
  user can quote it and an operator can find the matching log line.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

CORRELATION_HEADER = "X-Correlation-Id"


class ErrorCode:
    """Codes from README section 8, plus the additions declared in the contract.

    A class of constants rather than an enum: the set grows every sprint, and
    these are compared against strings on the wire.
    """

    # Access
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_BLOCKED = "ACCOUNT_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"

    # Generic
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Domain
    SLOT_UNAVAILABLE = "SLOT_UNAVAILABLE"
    HOLD_EXPIRED = "HOLD_EXPIRED"
    CANCELLATION_CUTOFF_PASSED = "CANCELLATION_CUTOFF_PASSED"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    PAYMENT_ALREADY_APPLIED = "PAYMENT_ALREADY_APPLIED"
    REFUND_LIMIT_EXCEEDED = "REFUND_LIMIT_EXCEEDED"

    # Contract additions
    LAST_POWER_USER_PROTECTED = "LAST_POWER_USER_PROTECTED"
    RESOURCE_LOCATION_MISMATCH = "RESOURCE_LOCATION_MISMATCH"
    CLOSURE_CONFLICTS_BOOKINGS = "CLOSURE_CONFLICTS_BOOKINGS"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"


class ApiError(Exception):
    """A deliberate, client-facing failure.

    Raising this is how domain code reports a refusal. The message is shown to
    the user, so it must be safe for this actor to see — never include another
    customer's name, another location's data, or anything the caller is not
    authorized to know.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: dict[str, list[str]] | None = None,
        current_version: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors
        self.current_version = current_version
        # Codes that carry structured detail — conflicting intervals for
        # SLOT_UNAVAILABLE, the cutoff for CANCELLATION_CUTOFF_PASSED.
        self.extra = extra or {}


def correlation_id_for(request: Request) -> str:
    """One id per request, reused by every error raised while handling it."""
    existing = getattr(request.state, "correlation_id", None)
    if isinstance(existing, str):
        return existing

    generated = str(uuid.uuid4())
    request.state.correlation_id = generated
    return generated


def _body(
    code: str,
    message: str,
    correlation_id: str,
    *,
    field_errors: dict[str, list[str]] | None = None,
    current_version: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
    }
    if field_errors:
        body["field_errors"] = field_errors
    if current_version is not None:
        body["current_version"] = current_version
    if extra:
        body.update(extra)
    return body


def _http_status_to_contract(status_code: int, detail: object) -> tuple[str, str]:
    """Map a framework HTTP status onto a contract code and a Russian message.

    The detail string is deliberately discarded for anything the mapping
    covers: framework details are English and sometimes internal, and the
    client branches on `code` rather than reading prose.
    """
    mapping: dict[int, tuple[str, str]] = {
        401: (ErrorCode.NOT_AUTHENTICATED, "Войдите в систему."),
        403: (ErrorCode.FORBIDDEN, "Недостаточно прав для этого действия."),
        404: (ErrorCode.NOT_FOUND, "Запись не найдена."),
        405: (ErrorCode.NOT_FOUND, "Операция недоступна для этого адреса."),
        409: (ErrorCode.INVALID_TRANSITION, "Действие недоступно в текущем состоянии."),
        422: (ErrorCode.VALIDATION_FAILED, "Проверьте заполненные поля."),
        429: (ErrorCode.RATE_LIMITED, "Слишком много попыток. Повторите позже."),
    }

    if status_code in mapping:
        return mapping[status_code]

    if status_code >= 500:
        return (
            ErrorCode.INTERNAL_ERROR,
            "Произошла внутренняя ошибка. Сообщите код обращения в поддержку.",
        )

    # An uncovered 4xx. The detail is included only when it is a plain string
    # set deliberately by application code.
    message = detail if isinstance(detail, str) else "Запрос отклонён."
    return (ErrorCode.VALIDATION_FAILED, message)


def register_error_handlers(app: FastAPI) -> None:
    """Install handlers so no failure path can produce a non-contract body."""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        correlation_id = correlation_id_for(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(
                exc.code,
                exc.message,
                correlation_id,
                field_errors=exc.field_errors,
                current_version=exc.current_version,
                extra=exc.extra,
            ),
            headers={CORRELATION_HEADER: correlation_id},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Framework-raised HTTP errors — 404, 405, and anything a dependency aborts with.

        Without this, FastAPI's default `{"detail": "Not Found"}` escapes for
        the most common error on the API. A client branching on `code` would
        read it as unknown, and the single-error-shape guarantee in section 8
        would hold for domain errors only.
        """
        correlation_id = correlation_id_for(request)
        code, message = _http_status_to_contract(exc.status_code, exc.detail)

        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, message, correlation_id),
            headers={CORRELATION_HEADER: correlation_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Reshape FastAPI's validation output into the contract's field_errors.

        FastAPI's default body is its own shape; emitting it unchanged would
        mean forms have to parse two different error formats.
        """
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            # Drop the leading location segment ("body", "query") — the client
            # knows where it sent the field.
            location = [str(part) for part in error["loc"][1:]] or [str(error["loc"][0])]
            field = ".".join(location)
            field_errors.setdefault(field, []).append(error["msg"])

        correlation_id = correlation_id_for(request)
        return JSONResponse(
            status_code=422,
            content=_body(
                ErrorCode.VALIDATION_FAILED,
                "Проверьте заполненные поля.",
                correlation_id,
                field_errors=field_errors,
            ),
            headers={CORRELATION_HEADER: correlation_id},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort. The detail is logged; the client gets a generic message.

        Returning the exception text would leak internal structure, file paths
        and sometimes data. The correlation id is the bridge between what the
        user can quote and what the operator can search.
        """
        correlation_id = correlation_id_for(request)
        logger.exception("Unhandled exception", extra={"correlation_id": correlation_id})
        return JSONResponse(
            status_code=500,
            content=_body(
                ErrorCode.INTERNAL_ERROR,
                "Произошла внутренняя ошибка. Сообщите код обращения в поддержку.",
                correlation_id,
            ),
            headers={CORRELATION_HEADER: correlation_id},
        )
