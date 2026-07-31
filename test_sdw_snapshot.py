from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import utils.source_router as source_router
from utils.fetch_yahoo import (
    YAHOO_TURNOVER_WARNING,
    YAHOO_VWAP_WARNING,
    yahoo_period_for_days,
    yahoo_table_from_history,
    yahoo_ticker_from_code,
)
from utils.parse_sdw import parse_sdw_snapshot
from utils.snapshot_db import (
    build_results_from_db,
    db_restore_status,
    load_price_history,
    load_snapshot,
    load_watchlist_entries,
    restore_snapshot_db_from_backup,
    upsert_price_history,
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

    def test_mirror_probe_cache_is_scoped_to_mirror_base_url(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            cache_path = Path(tmpdir) / "mirror_probe_status.json"
            with patch.object(source_router, "PROBE_CACHE_PATH", cache_path):
                with patch.dict(os.environ, {"CCASS_MIRROR_BASE_URL": "https://webbsite.0xmd.com"}):
                    source_router._write_probe_cache("blocked_by_cloudflare")
                    self.assertEqual(source_router._probe_cache_today()["status"], "blocked_by_cloudflare")
                with patch.dict(os.environ, {"CCASS_MIRROR_BASE_URL": "https://webb-database.com"}):
                    self.assertIsNone(source_router._probe_cache_today())

    def test_mirror_probe_uses_browser_for_200_no_table_shell(self) -> None:
        request_result = source_router.FetchResult(
            name="Mirror probe",
            url="https://webb-database.com/ccass/choldings.asp?i=28222",
            status=200,
            ok=False,
            error_type="ValueError",
            error_message="no table found",
        )
        browser_result = source_router.FetchResult(
            name="Mirror probe",
            url="https://webb-database.com/ccass/choldings.asp?i=28222",
            status=200,
            ok=True,
            tables=[pd.DataFrame({"Participant": ["A"], "Holding": [1]})],
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            cache_path = Path(tmpdir) / "mirror_probe_status.json"
            with (
                patch.object(source_router, "PROBE_CACHE_PATH", cache_path),
                patch.object(source_router, "fetch_with_requests", return_value=request_result),
                patch.object(source_router, "fetch_with_playwright", return_value=browser_result) as browser_fetch,
                patch.dict(os.environ, {"CCASS_MIRROR_BASE_URL": "https://webb-database.com"}),
            ):
                self.assertEqual(source_router.mirror_probe("01912", "28222", timeout=30), "ok")
                browser_fetch.assert_called_once()

    def test_yahoo_ticker_conversion_keeps_hk_four_digit_symbol(self) -> None:
        self.assertEqual(yahoo_ticker_from_code("01449"), "1449.HK")
        self.assertEqual(yahoo_ticker_from_code("00388"), "0388.HK")
        self.assertEqual(yahoo_ticker_from_code("00700"), "0700.HK")
        self.assertEqual(yahoo_ticker_from_code("00005"), "0005.HK")
        self.assertEqual(yahoo_period_for_days(7), "1mo")
        self.assertEqual(yahoo_period_for_days(90), "3mo")

    def test_yahoo_history_table_adds_estimated_turnover_columns(self) -> None:
        history = pd.DataFrame(
            {
                "Open": [1.0000000119],
                "High": [1.23456],
                "Low": [0.98765],
                "Close": [1.1000000119],
                "Volume": [1000],
            },
            index=pd.to_datetime(["2026-07-30"]),
        )
        history.index.name = "Date"
        table = yahoo_table_from_history(history)
        self.assertEqual(table.iloc[0]["Date"], "2026-07-30")
        self.assertEqual(table.iloc[0]["price_source"], "yahoo")
        self.assertEqual(table.iloc[0]["turnover_est"], 1100.0)
        self.assertEqual(table.iloc[0]["Turnover"], 1100.0)
        self.assertEqual(table.iloc[0]["Close"], 1.1)
        self.assertEqual(table.iloc[0]["High"], 1.235)
        self.assertEqual(table.iloc[0]["vwap_est"], 1.1)
        self.assertIn("estimated", YAHOO_TURNOVER_WARNING)
        self.assertIn("estimated", YAHOO_VWAP_WARNING)

    def test_price_history_upsert_and_load_roundtrip(self) -> None:
        table = pd.DataFrame(
            [
                {
                    "Date": "2026-07-30",
                    "Close": 0.550000011920929,
                    "Open": 0.540000011920929,
                    "High": 0.560000011920929,
                    "Low": 0.530000011920929,
                    "Volume": 1000,
                    "Turnover": 550.000011920929,
                    "VWAP": "",
                    "price_source": "yahoo",
                    "turnover_est": 550.000011920929,
                }
            ]
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "snapshots.db"
            written = upsert_price_history("01449", table, path=db_path)
            loaded = load_price_history("01449", path=db_path)
        self.assertEqual(written, 1)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.iloc[0]["price_source"], "yahoo")
        self.assertEqual(loaded.iloc[0]["Close"], 0.55)
        self.assertEqual(loaded.iloc[0]["Turnover"], 550.0)
        self.assertEqual(loaded.iloc[0]["turnover_est"], 550.0)
        self.assertEqual(loaded.iloc[0]["vwap_est"], 0.55)


if __name__ == "__main__":
    unittest.main()
