"""Connection management, PRAGMAs, lazy init and seeding."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.db import (
    DEFAULT_WATCHLIST,
    STARTING_CASH,
    get_cash_balance,
    get_db_path,
    init_db,
    list_snapshots,
    list_watchlist,
    remove_from_watchlist,
    reset_db_for_tests,
    set_cash_balance,
    utc_now_iso,
)
from app.db.connection import (
    DEFAULT_DB_PATH,
    PROJECT_ROOT,
    REQUIRED_TABLES,
    close_connection,
    get_connection,
)


def test_lazy_init_creates_all_six_tables(db_path: Path) -> None:
    rows = get_connection().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {row["name"] for row in rows}
    assert set(REQUIRED_TABLES) <= names


def test_database_file_is_created_on_first_use(db_path: Path) -> None:
    assert db_path.exists()


def test_seed_profile_and_watchlist(db_path: Path) -> None:
    assert get_cash_balance() == STARTING_CASH
    assert [row["ticker"] for row in list_watchlist()] == list(DEFAULT_WATCHLIST)


def test_seed_watchlist_timestamps_strictly_increase(db_path: Path) -> None:
    stamps = [row["added_at"] for row in list_watchlist()]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


def test_seed_writes_one_opening_snapshot(db_path: Path) -> None:
    snapshots = list_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["total_value"] == STARTING_CASH


def test_init_db_is_idempotent(db_path: Path) -> None:
    init_db()
    init_db()
    assert len(list_watchlist()) == len(DEFAULT_WATCHLIST)
    assert len(list_snapshots()) == 1
    assert get_cash_balance() == STARTING_CASH


def test_reinit_does_not_resurrect_a_removed_ticker(db_path: Path) -> None:
    remove_from_watchlist("TSLA")
    # Force the "first touch" path again, as a second process would see it.
    from app.db import connection as connection_module

    connection_module._initialised_paths.clear()
    init_db()

    tickers = [row["ticker"] for row in list_watchlist()]
    assert "TSLA" not in tickers
    assert len(tickers) == len(DEFAULT_WATCHLIST) - 1


@pytest.mark.parametrize(
    ("pragma", "expected"),
    [("journal_mode", "wal"), ("foreign_keys", 1), ("busy_timeout", 5000)],
)
def test_required_pragmas(db_path: Path, pragma: str, expected: object) -> None:
    value = get_connection().execute(f"PRAGMA {pragma}").fetchone()[0]
    if isinstance(value, str):
        value = value.lower()
    assert value == expected


def test_row_factory_is_addressable_by_name(db_path: Path) -> None:
    row = get_connection().execute("SELECT cash_balance FROM users_profile").fetchone()
    assert isinstance(row, sqlite3.Row)
    assert row["cash_balance"] == STARTING_CASH


def test_each_thread_gets_its_own_connection(db_path: Path) -> None:
    main_conn = get_connection()
    seen: list[sqlite3.Connection] = []

    def worker() -> None:
        try:
            seen.append(get_connection())
        finally:
            close_connection()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == 4
    assert all(conn is not main_conn for conn in seen)
    assert len({id(conn) for conn in seen}) == 4


def test_same_thread_reuses_its_connection(db_path: Path) -> None:
    assert get_connection() is get_connection()


def test_connection_is_replaced_when_the_path_changes(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = get_connection()
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "second.db"))
    try:
        second = get_connection()
        assert second is not first
        # The stale handle is closed, not merely dropped.
        with pytest.raises(sqlite3.ProgrammingError):
            first.execute("SELECT 1")
        assert get_cash_balance() == STARTING_CASH
    finally:
        close_connection()


def test_reset_db_for_tests_wipes_an_existing_database(db_path: Path) -> None:
    set_cash_balance(42.0)
    remove_from_watchlist("AAPL")
    assert db_path.exists()

    reset_db_for_tests()

    assert get_cash_balance() == STARTING_CASH
    assert [row["ticker"] for row in list_watchlist()] == list(DEFAULT_WATCHLIST)


def test_default_path_is_repo_root_db_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINALLY_DB_PATH", raising=False)
    assert get_db_path() == DEFAULT_DB_PATH
    assert DEFAULT_DB_PATH == PROJECT_ROOT / "db" / "finally.db"
    # parents[3] must land on the repo root, not backend/.
    assert (PROJECT_ROOT / "backend").is_dir()
    assert (PROJECT_ROOT / "planning").is_dir()


def test_env_var_overrides_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "nested" / "custom.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(target))
    assert get_db_path() == target.resolve()


def test_missing_parent_directory_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "does" / "not" / "exist" / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(target))
    try:
        init_db()
        assert target.exists()
    finally:
        close_connection()


def test_utc_now_iso_shape(db_path: Path) -> None:
    stamp = utc_now_iso()
    assert stamp.endswith("Z")
    assert "+00:00" not in stamp
    assert stamp[4] == "-" and stamp[10] == "T"
    assert len(stamp) == len("2026-09-09T14:32:05.123456Z")
