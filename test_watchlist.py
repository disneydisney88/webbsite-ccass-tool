from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import utils.snapshot_db as snapshot_db


def test_lshape79_fixture_has_79_normalized_entries() -> None:
    path = Path(__file__).parent / "data" / "watchlist_lshape79.csv"
    entries = snapshot_db._load_watchlist_file(path, group="lshape79")

    assert len(entries) == 79
    assert all(entry.code.isdigit() and len(entry.code) == 5 for entry in entries)
    assert all("lshape79" in entry.groups for entry in entries)
    assert all(entry.priority in {1, 2, 3} for entry in entries)


def test_combined_watchlist_preserves_caiji_and_lshape79_groups() -> None:
    entries = snapshot_db.load_watchlist_entries()
    groups = {group for entry in entries for group in entry.groups}

    assert "caiji" in groups
    assert "lshape79" in groups
    assert len(snapshot_db.load_watchlist_entries(group="lshape79")) == 79


def test_turso_watchlist_loader_reads_group_rows() -> None:
    path = Path("data/watchlist.csv")
    with patch.object(snapshot_db, "WATCHLIST_PATH", path), patch.dict(
        "os.environ",
        {"TURSO_DATABASE_URL": "https://example.turso.io", "TURSO_AUTH_TOKEN": "fixture"},
        clear=True,
    ), patch.object(
        snapshot_db,
        "_turso_rows_to_dicts",
        return_value=[
            {
                "group_name": "lshape79",
                "code": "01792",
                "name": "CMON",
                "tag": "A",
                "priority": 1,
                "added_date": "2026-09-06",
                "source": "repo_csv",
            }
        ],
    ):
        entries = snapshot_db.load_watchlist_entries(group="lshape79")

    assert entries[0].code == "01792"
    assert entries[0].groups == ("lshape79",)
    assert entries[0].priority == 1
