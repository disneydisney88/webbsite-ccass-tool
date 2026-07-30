from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.parse_sdw import parse_sdw_snapshot
from utils.snapshot_db import build_results_from_db, load_snapshot, upsert_snapshot


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


if __name__ == "__main__":
    unittest.main()
