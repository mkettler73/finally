"""The error envelope (API_CONTRACT §0).

The frontend has exactly one error parser, so nothing may escape as FastAPI's
default ``{"detail": [{"loc": ...}]}`` array.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import STATUS_BY_CODE, ApiError
from app.services.trading import TradeError


def assert_envelope(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"detail"}
    assert set(body["detail"]) == {"code", "message"}
    assert body["detail"]["code"] == code
    assert isinstance(body["detail"]["message"], str)
    assert body["detail"]["message"]


def test_contract_codes_have_statuses():
    for code, status in {
        "INVALID_QUANTITY": 400,
        "INVALID_SIDE": 400,
        "INVALID_TICKER": 400,
        "PRICE_UNAVAILABLE": 400,
        "INSUFFICIENT_CASH": 400,
        "INSUFFICIENT_SHARES": 400,
        "TICKER_ALREADY_WATCHED": 409,
        "TICKER_NOT_WATCHED": 404,
        "WATCHLIST_FULL": 400,
        "LLM_ERROR": 502,
        "INTERNAL_ERROR": 500,
    }.items():
        assert STATUS_BY_CODE[code] == status


def test_malformed_body_is_enveloped(client):
    response = client.post("/api/portfolio/trade", json={"quantity": 1, "side": "buy"})

    assert_envelope(response, 400, "INVALID_TICKER")


def test_non_numeric_quantity_is_enveloped(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": "ten", "side": "buy"}
    )

    assert_envelope(response, 400, "INVALID_QUANTITY")


def test_unknown_api_path_is_enveloped(client):
    assert_envelope(client.get("/api/nope"), 404, "NOT_FOUND")


def test_wrong_method_is_enveloped(client):
    assert_envelope(client.put("/api/portfolio"), 405, "METHOD_NOT_ALLOWED")


def test_unexpected_errors_become_internal_error(harness, client, monkeypatch):
    import app.db as db_module

    def boom(user_id="default"):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(db_module, "get_cash_balance", boom)

    response = client.get("/api/portfolio")

    assert_envelope(response, 500, "INTERNAL_ERROR")
    assert "kaboom" not in response.text


@pytest.fixture
def raising_app():
    """A minimal app whose routes raise the exceptions the handlers translate."""
    from app.api.errors import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api-error")
    def api_error():
        raise ApiError("WATCHLIST_FULL", "Watchlist is full.")

    @app.get("/trade-error")
    def trade_error():
        raise TradeError("INSUFFICIENT_CASH", "Need $10.00 but only $1.00 available.")

    @app.get("/unknown-code")
    def unknown_code():
        raise ApiError("SOMETHING_NEW", "Unmapped code.")

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_api_error_handler(raising_app):
    assert_envelope(raising_app.get("/api-error"), 400, "WATCHLIST_FULL")


def test_trade_error_handler(raising_app):
    assert_envelope(raising_app.get("/trade-error"), 400, "INSUFFICIENT_CASH")


def test_unmapped_code_defaults_to_400(raising_app):
    assert_envelope(raising_app.get("/unknown-code"), 400, "SOMETHING_NEW")


def test_api_error_status_override():
    assert ApiError("LLM_ERROR", "upstream failed").status_code == 502
    assert ApiError("INVALID_TICKER", "nope", status_code=418).status_code == 418
