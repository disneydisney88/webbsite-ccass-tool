"""Tests for Phase 1.4 response shaping.

Covers the lowered default limits and the additive Big Changes enrichment
(participant_id joined from Holdings, change_shares/change_pct as explicit
numeric fields, old keys preserved for backward compatibility).
"""

import inspect
import unittest

import api


class DefaultLimitsTest(unittest.TestCase):
    def test_build_stock_payload_default_limits_lowered(self):
        defaults = {
            name: param.default
            for name, param in inspect.signature(api.build_stock_payload).parameters.items()
        }
        self.assertEqual(defaults["holdings_limit"], 15)
        self.assertEqual(defaults["changes_limit"], 20)
        self.assertEqual(defaults["big_changes_limit"], 10)
        self.assertEqual(defaults["concentration_limit"], 15)


class ParticipantIdMapTest(unittest.TestCase):
    def test_maps_name_to_ccass_id_and_skips_aggregates(self):
        holdings = [
            {"Participant": "Issued securities", "CCASS ID": None},
            {"Participant": "Total in CCASS", "CCASS ID": None},
            {"Participant": "BANK OF CHINA (HONG KONG) LTD", "CCASS ID": "C00033"},
            {"Participant": "FUTU SECURITIES INTERNATIONAL", "CCASS ID": "B01955"},
        ]
        mapping = api.build_participant_id_map(holdings)
        self.assertEqual(mapping["BANK OF CHINA (HONG KONG) LTD"], "C00033")
        self.assertEqual(mapping["FUTU SECURITIES INTERNATIONAL"], "B01955")
        self.assertNotIn("Issued securities", mapping)
        self.assertNotIn("Total in CCASS", mapping)


class EnrichBigChangesTest(unittest.TestCase):
    def setUp(self):
        self.mapping = {
            "BANK OF CHINA (HONG KONG) LTD": "C00033",
            "FUTU SECURITIES INTERNATIONAL": "B01955",
        }

    def test_adds_participant_id_and_keeps_old_keys(self):
        records = [{"Date": "26-07-09", "Participant": "BANK OF CHINA (HONG KONG) LTD", "Change %": -0.44}]
        enriched = api.enrich_big_changes(records, self.mapping)[0]
        # New explicit fields
        self.assertEqual(enriched["participant_id"], "C00033")
        self.assertEqual(enriched["participant_name"], "BANK OF CHINA (HONG KONG) LTD")
        self.assertEqual(enriched["change_pct"], -0.44)
        self.assertIsNone(enriched["change_shares"])  # no "Change in shares" column
        # Old keys preserved for backward compatibility
        self.assertEqual(enriched["Participant"], "BANK OF CHINA (HONG KONG) LTD")
        self.assertEqual(enriched["Change %"], -0.44)
        self.assertEqual(enriched["Date"], "26-07-09")

    def test_participant_id_null_when_unmatched(self):
        records = [{"Date": "26-07-09", "Participant": "SOME UNLISTED BROKER LTD", "Change %": 0.5}]
        enriched = api.enrich_big_changes(records, self.mapping)[0]
        self.assertIsNone(enriched["participant_id"])
        self.assertEqual(enriched["participant_name"], "SOME UNLISTED BROKER LTD")

    def test_change_shares_parsed_when_present(self):
        records = [
            {
                "Date": "26-07-09",
                "Participant": "FUTU SECURITIES INTERNATIONAL",
                "Change in shares": "1,260,000",
                "Change %": 0.09,
            }
        ]
        enriched = api.enrich_big_changes(records, self.mapping)[0]
        self.assertEqual(enriched["change_shares"], 1260000)
        self.assertEqual(enriched["change_pct"], 0.09)
        self.assertEqual(enriched["participant_id"], "B01955")


if __name__ == "__main__":
    unittest.main()
