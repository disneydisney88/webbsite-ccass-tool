from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import utils.snapshot_db as snapshot_db
import utils.turso_db as turso_db


def _legacy_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE longbridge_holdings_daily ("
            "code TEXT, data_date TEXT, ccass_id TEXT, participant_name TEXT, "
            "holding_shares INTEGER, stake_pct_of_issued REAL, change_shares REAL, fetched_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE longbridge_credentials (credential_id TEXT PRIMARY KEY, "
            "encrypted_payload BLOB, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO longbridge_holdings_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("06182", "2026-09-04", "B01438", "KINGSTON", 540928000, 67.616, -90000000, "now"),
        )
        conn.execute(
            "INSERT INTO longbridge_credentials VALUES (?, ?, ?)",
            ("primary", b"encrypted-fixture", "now"),
        )


def test_migration_copies_holdings_and_credential_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    executed: list[tuple[str, int]] = []
    queries = [[], []]

    def fake_many(statement, rows):
        executed.append((statement, len(rows)))
        return len(rows)

    with patch.object(snapshot_db, "DB_PATH", path), patch.dict(
        "os.environ",
        {"TURSO_DATABASE_URL": "https://example.turso.io", "TURSO_AUTH_TOKEN": "fixture"},
        clear=True,
    ), patch.object(turso_db, "ensure_turso_schema"), patch.object(
        turso_db, "turso_execute_many", side_effect=fake_many
    ), patch.object(turso_db, "turso_query", side_effect=queries), patch.object(
        turso_db, "turso_execute"
    ) as execute:
        result = snapshot_db.migrate_longbridge_state_to_turso(path=path)

    assert result == {"status": "complete", "longbridge_rows": 1, "credential_migrated": True}
    assert [count for _, count in executed] == [1, 1]
    execute.assert_called_once()
    assert "longbridge_credentials" in execute.call_args.args[0]


def test_turso_longbridge_reads_do_not_initialize_local_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_query(statement, args):
        calls.append((statement, tuple(args)))
        return [{"code": "06182", "data_date": "2026-09-04", "ccass_id": "B01438"}]

    with patch.object(snapshot_db, "DB_PATH", path), patch.dict(
        "os.environ",
        {"TURSO_DATABASE_URL": "https://example.turso.io", "TURSO_AUTH_TOKEN": "fixture"},
        clear=True,
    ), patch.object(turso_db, "turso_query", side_effect=fake_query), patch.object(
        snapshot_db, "ensure_db", side_effect=AssertionError("unexpected SQLite initialization")
    ):
        rows = snapshot_db.load_longbridge_holdings("06182", path=path)

    assert rows[0]["ccass_id"] == "B01438"
    assert calls
