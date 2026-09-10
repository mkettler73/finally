"""The single error envelope (API_CONTRACT §0).

Every 4xx/5xx body the frontend can see is
``{"detail": {"code": ..., "message": ...}}`` — including FastAPI's own
validation failures, which would otherwise arrive as the `[{"loc": ...}]` array
the frontend has no parser for.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..services.trading import TradeError

logger = logging.getLogger(__name__)

# API_CONTRACT §0. Codes not in the contract's table (NOT_FOUND,
# METHOD_NOT_ALLOWED, INVALID_REQUEST) exist only for failures the contract does
# not describe: routing misses and malformed request bodies.
STATUS_BY_CODE: dict[str, int] = {
    "INVALID_QUANTITY": 400,
    "INVALID_SIDE": 400,
    "INVALID_TICKER": 400,
    "INVALID_REQUEST": 400,
    "PRICE_UNAVAILABLE": 400,
    "INSUFFICIENT_CASH": 400,
    "INSUFFICIENT_SHARES": 400,
    "WATCHLIST_FULL": 400,
    "TICKER_NOT_WATCHED": 404,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "TICKER_ALREADY_WATCHED": 409,
    "LLM_ERROR": 502,
    "INTERNAL_ERROR": 500,
}

CODE_BY_STATUS: dict[int, str] = {
    400: "INVALID_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "TICKER_ALREADY_WATCHED",
    502: "LLM_ERROR",
}

# Request field -> the code its validation failure reports.
_CODE_BY_FIELD: dict[str, str] = {
    "quantity": "INVALID_QUANTITY",
    "side": "INVALID_SIDE",
    "ticker": "INVALID_TICKER",
    "limit": "INVALID_REQUEST",
}


class ApiError(Exception):
    """An error already expressed in contract terms."""

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code or STATUS_BY_CODE.get(code, 400)


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Map ``app.db`` exceptions onto the HTTP error envelope.

    The db layer never imports FastAPI, so the translation has to happen at the
    router boundary. The import is deferred because the module is only needed
    when a db call actually raises.
    """
    try:
        yield
    except Exception as exc:
        raise _translate(exc) from exc


def _translate(exc: Exception) -> Exception:
    from .. import db

    mapping: list[tuple[type[Exception], str]] = [
        (db.DuplicateTickerError, "TICKER_ALREADY_WATCHED"),
        (db.TickerNotFoundError, "TICKER_NOT_WATCHED"),
        (db.WatchlistFullError, "WATCHLIST_FULL"),
        (db.InsufficientCashError, "INSUFFICIENT_CASH"),
        (db.InsufficientSharesError, "INSUFFICIENT_SHARES"),
    ]
    for exc_type, code in mapping:
        if isinstance(exc, exc_type):
            return ApiError(code, str(exc))
    return exc


def _validation_code(errors: list[dict[str, Any]]) -> tuple[str, str]:
    """Pick the most specific contract code for a Pydantic failure."""
    for error in errors:
        location = [str(part) for part in error.get("loc", ())]
        for field, code in _CODE_BY_FIELD.items():
            if field in location:
                return code, f"{field}: {error.get('msg', 'is invalid')}"
    first = errors[0] if errors else {}
    field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "Request body is invalid.")
    return "INVALID_REQUEST", f"{field}: {message}" if field else message


def register_exception_handlers(app: FastAPI) -> None:
    """Install the envelope handlers on the app."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(TradeError)
    async def _handle_trade_error(request: Request, exc: TradeError) -> JSONResponse:
        return error_response(exc.code, exc.message, STATUS_BY_CODE.get(exc.code, 400))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        code, message = _validation_code(list(exc.errors()))
        return error_response(code, message, STATUS_BY_CODE.get(code, 400))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return error_response(
                str(detail["code"]),
                str(detail.get("message", "")),
                exc.status_code,
            )
        code = CODE_BY_STATUS.get(exc.status_code, "INTERNAL_ERROR")
        return error_response(code, str(detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return error_response("INTERNAL_ERROR", "An unexpected error occurred.", 500)
