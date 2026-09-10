"""Chat history: JSON round-tripping of `actions`, ordering and limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import append_chat_message, list_chat_messages
from app.db.connection import get_connection


def test_append_and_list_round_trip(db_path: Path) -> None:
    append_chat_message("user", "Buy 5 NVDA")
    append_chat_message("assistant", "Bought 5 NVDA.", actions=[{"type": "trade", "status": "ok"}])

    messages = list_chat_messages()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Buy 5 NVDA"
    assert messages[0]["actions"] is None
    assert messages[1]["actions"] == [{"type": "trade", "status": "ok"}]
    assert set(messages[0]) == {"id", "role", "content", "actions", "created_at"}


def test_actions_are_stored_as_json_text(db_path: Path) -> None:
    append_chat_message("assistant", "done", actions=[{"ticker": "NVDA", "quantity": 5.0}])
    raw = get_connection().execute("SELECT actions FROM chat_messages").fetchone()["actions"]
    assert isinstance(raw, str)
    assert '"NVDA"' in raw


def test_none_actions_stored_as_null(db_path: Path) -> None:
    append_chat_message("user", "hello")
    raw = get_connection().execute("SELECT actions FROM chat_messages").fetchone()["actions"]
    assert raw is None


def test_empty_actions_list_survives_the_round_trip(db_path: Path) -> None:
    append_chat_message("assistant", "no actions", actions=[])
    assert list_chat_messages()[0]["actions"] == []


def test_nested_action_payloads_survive(db_path: Path) -> None:
    actions = [
        {
            "type": "trade",
            "status": "ok",
            "detail": "Bought 5 NVDA @ $121.40",
            "data": {"ticker": "NVDA", "side": "buy", "quantity": 5.0, "price": 121.4},
        },
        {"type": "watchlist", "status": "ok", "data": {"ticker": "PYPL", "action": "add"}},
    ]
    append_chat_message("assistant", "done", actions=actions)
    assert list_chat_messages()[0]["actions"] == actions


def test_returns_most_recent_n_oldest_first(db_path: Path) -> None:
    for i in range(10):
        append_chat_message("user", f"message {i}")

    recent = list_chat_messages(limit=3)
    assert [message["content"] for message in recent] == ["message 7", "message 8", "message 9"]

    everything = list_chat_messages(limit=50)
    assert [message["content"] for message in everything] == [f"message {i}" for i in range(10)]


def test_invalid_role_raises(db_path: Path) -> None:
    with pytest.raises(ValueError):
        append_chat_message("system", "nope")  # type: ignore[arg-type]


def test_messages_are_scoped_by_user(db_path: Path) -> None:
    append_chat_message("user", "mine")
    append_chat_message("user", "theirs", user_id="other")

    assert [message["content"] for message in list_chat_messages()] == ["mine"]
    assert [message["content"] for message in list_chat_messages(user_id="other")] == ["theirs"]
