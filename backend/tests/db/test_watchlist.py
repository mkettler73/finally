"""Watchlist CRUD, ordering, duplicates and the 30-ticker cap."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.db import (
    DEFAULT_WATCHLIST,
    WATCHLIST_MAX,
    DuplicateTickerError,
    TickerNotFoundError,
    WatchlistFullError,
    add_to_watchlist,
    list_watchlist,
    remove_from_watchlist,
)
from app.db.connection import close_connection, get_connection


def test_add_appends_to_the_end(db_path: Path) -> None:
    row = add_to_watchlist("PYPL")

    assert row["ticker"] == "PYPL"
    assert row["added_at"].endswith("Z")
    assert row["id"]

    tickers = [entry["ticker"] for entry in list_watchlist()]
    assert tickers == [*DEFAULT_WATCHLIST, "PYPL"]


def test_add_normalises_case_and_whitespace(db_path: Path) -> None:
    row = add_to_watchlist("  pypl\n")
    assert row["ticker"] == "PYPL"
    assert "PYPL" in [entry["ticker"] for entry in list_watchlist()]


def test_duplicate_raises(db_path: Path) -> None:
    with pytest.raises(DuplicateTickerError) as excinfo:
        add_to_watchlist("aapl")
    assert "AAPL" in str(excinfo.value)
    assert len(list_watchlist()) == len(DEFAULT_WATCHLIST)


def test_remove_deletes_the_row(db_path: Path) -> None:
    remove_from_watchlist("tsla")
    tickers = [entry["ticker"] for entry in list_watchlist()]
    assert "TSLA" not in tickers
    assert len(tickers) == len(DEFAULT_WATCHLIST) - 1


def test_remove_unknown_raises(db_path: Path) -> None:
    with pytest.raises(TickerNotFoundError) as excinfo:
        remove_from_watchlist("ZZZZ")
    assert "ZZZZ" in str(excinfo.value)


def test_cap_at_thirty(db_path: Path) -> None:
    for i in range(WATCHLIST_MAX - len(DEFAULT_WATCHLIST)):
        add_to_watchlist(f"T{i:03d}")
    assert len(list_watchlist()) == WATCHLIST_MAX

    with pytest.raises(WatchlistFullError):
        add_to_watchlist("ONEMORE")
    assert len(list_watchlist()) == WATCHLIST_MAX


def test_removing_then_adding_frees_a_slot(db_path: Path) -> None:
    for i in range(WATCHLIST_MAX - len(DEFAULT_WATCHLIST)):
        add_to_watchlist(f"T{i:03d}")
    remove_from_watchlist("AAPL")
    add_to_watchlist("ONEMORE")
    assert len(list_watchlist()) == WATCHLIST_MAX


def test_list_is_ordered_oldest_first(db_path: Path) -> None:
    add_to_watchlist("PYPL")
    add_to_watchlist("SHOP")
    stamps = [entry["added_at"] for entry in list_watchlist()]
    assert stamps == sorted(stamps)
    assert [entry["ticker"] for entry in list_watchlist()][-2:] == ["PYPL", "SHOP"]


def test_concurrent_adds_of_the_same_ticker_insert_one_row(db_path: Path) -> None:
    barrier = threading.Barrier(6)
    added: list[str] = []
    duplicates: list[str] = []

    def worker() -> None:
        barrier.wait()
        try:
            added.append(add_to_watchlist("PYPL")["id"])
        except DuplicateTickerError:
            duplicates.append("dup")
        finally:
            close_connection()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(added) == 1
    assert len(duplicates) == 5
    count = (
        get_connection()
        .execute("SELECT COUNT(*) AS n FROM watchlist WHERE ticker = 'PYPL'")
        .fetchone()["n"]
    )
    assert count == 1
