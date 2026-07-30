from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.parse_sdw import parse_sdw_snapshot
from utils.snapshot_db import (
    build_results_from_db,
    db_restore_status,
    load_snapshot,
    load_watchlist_entries,
    restore_snapshot_db_from_backup,
    upsert_snapshot,
)


FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "sdw_sample.html"


class SDWParserAndSnapshotTests(unittest.TestCase):
    def test_parse_sdw_fixture_extracts_participants(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        snapshot = parse_sdw_snapshot(html, code="03321", query_date="2026/07/29")
        self.assertEqual(snapshot.code, "03321")
        self.assertEqual(snapshot.date, "2026-07-29")
        self.assertEqual(snapshot.issued_shares, "1,000,000,000")
        self.assertEqual(len(snapshot.rows or []), 3)
        self.assertEqual(snapshot.rows[0]["participant_id"], "C00019")
        self.assertEqual(snapshot.rows[0]["pct_of_issued"], 10.0)

    def test_snapshot_db_builds_section_tables(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        first = parse_sdw_snapshot(html, code="03321", query_date="2026/07/29")
        second_html = html.replace("2026/07/29", "2026/07/30").replace("100,000,000", "110,000,000").replace("10.00%", "11.00%")
        second = parse_sdw_snapshot(second_html, code="03321", query_date="2026/07/30")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "snapshots.db"
            upsert_snapshot(first, path=db_path)
            upsert_snapshot(second, path=db_path)
            loaded = load_snapshot("03321", "2026-07-30", path=db_path)
            self.assertEqual(len(loaded), 3)
            built = build_results_from_db("03321", "https://example.test/sdw", path=db_path)
            self.assertEqual(built.history_depth_days, 2)
            self.assertIn("Holdings", built.results)
            self.assertIn("Changes", built.results)
            self.assertTrue(built.results["Holdings"].ok)
            self.assertTrue(built.results["Changes"].ok)

    def test_watchlist_group_filter_handles_semicolon_membership(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            path = Path(tmpdir) / "watchlist.csv"
            path.write_text(
                "code,name,group\n"
                "01449,,caiji;lshape\n"
                "03321,,caiji\n"
                "02497,,lshape\n",
                encoding="utf-8",
            )
            all_entries = load_watchlist_entries(path)
            caiji_entries = load_watchlist_entries(path, group="caiji")
            lshape_entries = load_watchlist_entries(path, group="lshape")
        self.assertEqual([entry.code for entry in all_entries], ["01449", "03321", "02497"])
        self.assertEqual([entry.code for entry in caiji_entries], ["01449", "03321"])
        self.assertEqual([entry.code for entry in lshape_entries], ["01449", "02497"])

    def test_project_watchlist_has_expected_groups(self) -> None:
        path = Path(__file__).parent / "data" / "watchlist.csv"
        all_entries = load_watchlist_entries(path)
        caiji_entries = load_watchlist_entries(path, group="caiji")
        lshape_entries = load_watchlist_entries(path, group="lshape")
        by_code = {entry.code: entry for entry in all_entries}
        self.assertEqual(len(all_entries), 52)
        self.assertEqual(len(caiji_entries), 28)
        self.assertEqual(len(lshape_entries), 25)
        self.assertEqual(by_code["01449"].groups, ("caiji", "lshape"))
        self.assertIn("01433", by_code)

    def test_restore_snapshot_db_from_backup_copies_latest_backup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            path = Path(tmpdir) / "ccass_snapshots.db"
            backup = Path(tmpdir) / "backups" / "ccass_snapshots_latest.db"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b"sqlite bytes")
            restored = restore_snapshot_db_from_backup(path=path, backup_path=backup)
            self.assertTrue(restored)
            self.assertEqual(path.read_bytes(), b"sqlite bytes")
            self.assertTrue(db_restore_status()["db_restored_from_backup"])


if __name__ == "__main__":
    unittest.main()
