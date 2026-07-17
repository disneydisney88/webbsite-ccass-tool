"""Tests for participant search (3.4), event tags/timeline (3.5), markdown (3.6)."""

import unittest
from unittest.mock import patch

import api


def fake_payload(code: str, include_gransing: bool = True) -> dict:
    holdings = [
        {"Rank": 1, "Participant": "NANYANG", "CCASS ID": "C00028", "Holding": 445000000, "Stake %": 51.92, "category": "bank"},
    ]
    if include_gransing:
        holdings.append({"Rank": 2, "Participant": "GRANSING", "CCASS ID": "B01660", "Holding": 218000000, "Stake %": 25.4, "category": "boutique"})
    return {
        "metadata": {"code": code, "name": f"Stock {code}", "holdings_date": "2026-07-15", "issue_id": "1", "changes_date": "2026-07-13", "settlement_note": "T+2"},
        "holdings_summary": {"holdings_total_count": 100, "holdings_returned_count": len(holdings), "total_in_ccass_pct": "82%", "largest_participant": "NANYANG", "changes_returned_count": 5, "changes_total_count": 5, "big_changes_returned_count": 2, "big_changes_total_count": 2},
        "holdings": holdings,
        "changes": [{"Participant": "FUTU", "Change": 100, "Change %": 0.1, "Stake after": 3.4, "category": "retail"}],
        "big_changes": [{"Date": "2026-07-15", "participant_name": "GRANSING", "participant_id": "B01660", "change_pct": 15.95, "category": "boutique"}],
        "concentration": {"top5_pct_of_ccass": 72.19, "top5_pct_of_issued": 59.65, "top10_pct_of_ccass": 85.1, "top10_pct_of_issued": 70.33, "issued_shares": "858,000,000", "issued_shares_as_of": "2026-07-15", "issued_shares_may_be_stale": False, "records": [{"Date": "2026-07-15", "top5_pct_of_ccass": 72.19, "top5_pct_of_issued": 59.65, "top10_pct_of_ccass": 85.1, "top10_pct_of_issued": 70.33}]},
        "data_quality_warnings": [],
        "errors": [],
    }


class ParticipantSearchTest(unittest.TestCase):
    def test_finds_participant_across_stocks(self):
        def side(stock_code, timeout):
            return fake_payload(stock_code, include_gransing=(stock_code != "00001"))
        with patch.object(api, "build_stock_payload", side_effect=side):
            payload = api.build_participant_search_payload("B01660", ["02028", "06162", "00001"], timeout=25)
        self.assertEqual(payload["metadata"]["participant_id"], "B01660")
        self.assertEqual(payload["metadata"]["found_count"], 2)
        # ranked by stake desc, both 25.4 -> order preserved among equal
        self.assertTrue(all(item["found"] for item in payload["holdings_ranked"]))
        not_found = next(r for r in payload["results"] if r["code"] == "00001")
        self.assertFalse(not_found["found"])
        self.assertIn("not in returned holdings", not_found["note"])

    def test_invalid_participant_id_raises(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            api.build_participant_search_payload("XYZ", ["02028"], timeout=25)


class EventTagsTest(unittest.TestCase):
    def test_new_tags(self):
        self.assertIn("convertible_bonds", api.announcement_event_tags({"Title": "建議發行可換股債券"}))
        self.assertIn("very_substantial_acquisition", api.announcement_event_tags({"Title": "非常重大收購事項"}))
        self.assertIn("high_concentration_warning", api.announcement_event_tags({"Title": "股權高度集中"}))
        self.assertIn("general_offer", api.announcement_event_tags({"Title": "強制性無條件現金要約"}))

    def test_tags_are_sorted_unique(self):
        tags = api.announcement_event_tags({"Title": "供股 供股 rights issue"})
        self.assertEqual(tags, sorted(set(tags)))


class MarkdownTest(unittest.TestCase):
    def test_markdown_contains_key_sections(self):
        md = api.compact_payload_to_markdown(fake_payload("02028"))
        self.assertIn("## Metadata", md)
        self.assertIn("## Holdings", md)
        self.assertIn("## Concentration (recent)", md)
        self.assertIn("GRANSING", md)
        self.assertIn("Top5: of_ccass 72.19 / of_issued 59.65", md)

    def test_markdown_empty_tables(self):
        payload = fake_payload("02028")
        payload["changes"] = []
        md = api.compact_payload_to_markdown(payload)
        self.assertIn("_none_", md)


if __name__ == "__main__":
    unittest.main()
