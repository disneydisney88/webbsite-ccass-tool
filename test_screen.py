"""Tests for the batch screening endpoint (handover 3.1)."""

import unittest
from unittest.mock import patch

import api


def fake_payload(code: str) -> dict:
    return {
        "metadata": {"code": code, "name": f"Stock {code}", "holdings_date": "2026-07-15"},
        "holdings_summary": {"total_in_ccass_pct": "82.63%", "largest_participant": "VAST HARBOUR"},
        "holdings": [
            {"Participant": "Total in CCASS", "CCASS ID": None, "Stake %": 82.63, "category": "unknown"},
            {"Participant": "VAST HARBOUR SECURITIES LTD", "CCASS ID": "B01922", "Stake %": 38.32, "category": "boutique"},
            {"Participant": "FUTU SECURITIES INTERNATIONAL", "CCASS ID": "B01955", "Stake %": 5.07, "category": "retail"},
        ],
        "big_changes": [
            {"Date": "2026-07-15", "participant_name": "GRANSING", "category": "boutique", "change_pct": 15.95},
            {"Date": "2026-07-13", "participant_name": "CITIBANK", "category": "intl_broker", "change_pct": -3.34},
        ],
        "concentration": {
            "top5_pct_of_ccass": 72.19,
            "top5_pct_of_issued": 59.65,
            "top10_pct_of_ccass": 85.10,
            "top10_pct_of_issued": 70.33,
            "issued_shares_may_be_stale": False,
        },
        "data_quality_warnings": [],
    }


class ScreenOneTest(unittest.TestCase):
    def test_summary_extraction(self):
        with patch.object(api, "build_stock_payload", side_effect=lambda stock_code, timeout: fake_payload(stock_code)):
            summary = api.screen_one_stock("01592", 25)
        self.assertEqual(summary["code"], "01592")
        self.assertEqual(summary["name"], "Stock 01592")
        self.assertEqual(summary["ccass_total_pct"], "82.63%")
        self.assertEqual(summary["top5_pct_of_issued"], 59.65)
        # largest participant skips the aggregate "Total in CCASS" row
        self.assertEqual(summary["largest_participant"]["ccass_id"], "B01922")
        self.assertEqual(summary["largest_participant"]["category"], "boutique")
        # biggest move is Gransing +15.95 (largest magnitude)
        self.assertEqual(summary["biggest_change_5d"]["participant"], "GRANSING")
        self.assertEqual(summary["biggest_change_5d"]["change_pct"], 15.95)

    def test_invalid_code(self):
        self.assertEqual(api.screen_one_stock("abc", 25)["error"], "invalid stock code")

    def test_fetch_failure_is_captured(self):
        from fastapi import HTTPException

        with patch.object(api, "build_stock_payload", side_effect=HTTPException(status_code=502, detail="boom")):
            summary = api.screen_one_stock("01592", 25)
        self.assertEqual(summary["code"], "01592")
        self.assertEqual(summary["error"], "boom")


class ScreenBatchTest(unittest.TestCase):
    def test_dedupes_and_preserves_order(self):
        with patch.object(api, "build_stock_payload", side_effect=lambda stock_code, timeout: fake_payload(stock_code)):
            payload = api.build_screen_payload(["02028", "01592", "02028", "6162"], timeout=25)
        codes = [r["code"] for r in payload["results"]]
        self.assertEqual(codes, ["02028", "01592", "06162"])
        self.assertEqual(payload["metadata"]["requested_count"], 3)

    def test_caps_at_20(self):
        many = [f"{i:05d}" for i in range(1, 26)]
        with patch.object(api, "build_stock_payload", side_effect=lambda stock_code, timeout: fake_payload(stock_code)):
            payload = api.build_screen_payload(many, timeout=25)
        self.assertEqual(len(payload["results"]), 20)
        self.assertTrue(any("only the first 20" in w for w in payload["data_quality_warnings"]))

    def test_empty_raises(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            api.build_screen_payload(["", "xx"], timeout=25)


if __name__ == "__main__":
    unittest.main()
