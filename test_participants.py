"""Tests for Phase 2.4 participant categorisation and 2.2 settlement metadata."""

import unittest
from unittest.mock import patch

import api
from utils.participants import categorize


class CategorizeTest(unittest.TestCase):
    def test_by_ccass_id_takes_priority(self):
        self.assertEqual(categorize(ccass_id="B01955", name="anything"), "retail")
        self.assertEqual(categorize(ccass_id="C00033", name="anything"), "bank")

    def test_by_name_keyword_fallback(self):
        self.assertEqual(categorize(name="FUTU SECURITIES INTERNATIONAL"), "retail")
        self.assertEqual(categorize(name="THE HONGKONG AND SHANGHAI BANKING"), "bank")
        self.assertEqual(categorize(name="KINGSTON SECURITIES LTD"), "boutique")
        self.assertEqual(categorize(name="MERRILL LYNCH FAR EAST LTD"), "intl_broker")

    def test_case_insensitive(self):
        self.assertEqual(categorize(name="morgan stanley hong kong securities ltd"), "intl_broker")

    def test_unknown_default(self):
        self.assertEqual(categorize(name="SOME OBSCURE BROKER LTD"), "unknown")
        self.assertEqual(categorize(), "unknown")


class AnnotateCategoriesTest(unittest.TestCase):
    def test_holdings_use_ccass_id(self):
        records = [{"Participant": "FUTU SECURITIES INTERNATIONAL", "CCASS ID": "B01955"}]
        self.assertEqual(api.annotate_categories(records)[0]["category"], "retail")

    def test_changes_use_name_when_no_id(self):
        records = [{"Participant": "BANK OF CHINA (HONG KONG) LTD"}]
        self.assertEqual(api.annotate_categories(records)[0]["category"], "bank")

    def test_big_changes_use_participant_id(self):
        records = [{"participant_name": "X", "participant_id": "C00033"}]
        self.assertEqual(api.annotate_categories(records)[0]["category"], "bank")


class SettlementMetadataTest(unittest.TestCase):
    def test_payload_has_settlement_fields_and_categories(self):
        base = {
            "exported": {
                "metadata": {"stock_code": "01592", "stock_name": "X", "holdings_data_date": "2026-07-15"},
                "holdings": [{"Participant": "FUTU SECURITIES INTERNATIONAL", "CCASS ID": "B01955", "Holding": 100}],
                "changes": [{"Participant": "BANK OF CHINA (HONG KONG) LTD", "Change": -70000}],
                "bigchanges": [{"Date": "2026-07-15", "Participant": "KINGSTON SECURITIES LTD", "Change %": -2.38}],
                "concentration": [],
                "fetch_summary": [],
                "analysis_warnings": [],
            },
            "issue_id": "26603",
        }
        with patch.object(api, "build_base_payload", return_value=base):
            payload = api.build_stock_payload("01592")
        self.assertEqual(payload["metadata"]["data_as_of_trading_date"], "2026-07-15")
        self.assertIn("T+2", payload["metadata"]["settlement_note"])
        self.assertEqual(payload["holdings"][0]["category"], "retail")
        self.assertEqual(payload["changes"][0]["category"], "bank")
        self.assertEqual(payload["big_changes"][0]["category"], "boutique")


if __name__ == "__main__":
    unittest.main()
