"""Tests for the Webb-site Events and Officers parsers (Phase: new data types)."""

import os
import unittest

from utils.events import parse_events_html, parse_events_name, events_url
from utils.officers import (
    extract_org_id_from_html,
    officers_url,
    parse_officers_html,
    parse_officers_name,
    parse_shutdown_notice,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "samples")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


class EventsParserTest(unittest.TestCase):
    def setUp(self):
        self.html = load("events_03321.html")
        self.events = parse_events_html(self.html)

    def test_selects_events_table_not_listing_table(self):
        # The page also has a numtable (Exchange/Code/...); only real events return.
        self.assertEqual(len(self.events), 3)
        types = [e["type"] for e in self.events]
        self.assertEqual(types, ["Final dividend", "Split/Consol", "Interim dividend"])

    def test_split_consol_row_has_ratio_and_exdate(self):
        split = next(e for e in self.events if e["type"] == "Split/Consol")
        self.assertEqual(split["new_old"], "1:10")
        self.assertEqual(split["ex_date"], "2026-04-24")
        self.assertEqual(split["announced"], "2026-03-13")

    def test_event_id_and_detail_url_extracted(self):
        first = self.events[0]
        self.assertEqual(first["event_id"], "176855")
        self.assertTrue(first["event_details_url"].endswith("eventdets.asp?e=176855"))

    def test_name_and_url(self):
        self.assertIn("Zhongke Group Holdings", parse_events_name(self.html))
        self.assertEqual(events_url("27882"), "https://webbsite.0xmd.com/dbpub/events.asp?i=27882")


class OfficersParserTest(unittest.TestCase):
    def setUp(self):
        self.html = load("officers_03321.html")
        self.officers = parse_officers_html(self.html)

    def test_org_id_extracted_from_nav(self):
        self.assertEqual(extract_org_id_from_html(self.html), "14909854")

    def test_both_tables_parsed_and_grouped(self):
        groups = {o["table_group"] for o in self.officers}
        self.assertEqual(groups, {"Main board", "Manager/adviser/other"})
        # 3 named directors + 2 managers (continuation row without a name is skipped)
        self.assertEqual(len(self.officers), 5)

    def test_position_code_and_full_split(self):
        koh = next(o for o in self.officers if o["person_id"] == "100422")
        self.assertEqual(koh["position_code"], "INED")
        self.assertEqual(koh["position"], "Independent Non-Executive Director")
        self.assertEqual(koh["age"], 81)
        self.assertEqual(koh["sex"], "M")
        self.assertTrue(koh["is_current"])

    def test_person_id_and_url(self):
        au = self.officers[0]
        self.assertEqual(au["name"].split(",")[0], "Au")
        self.assertEqual(au["person_id"], "11263094")
        self.assertTrue(au["person_url"].endswith("positions.asp?p=11263094"))

    def test_resigned_officer_marked_not_current(self):
        li = next(o for o in self.officers if o["person_id"] == "23058382")
        self.assertEqual(li["until_date"], "2024-08-19")
        self.assertFalse(li["is_current"])

    def test_shutdown_notice_detected(self):
        notice = parse_shutdown_notice(self.html)
        self.assertIsNotNone(notice)
        self.assertIn("2025-03-31", notice)

    def test_name_and_url(self):
        self.assertIn("中科集團控股", parse_officers_name(self.html))
        self.assertEqual(
            officers_url("14909854"), "https://webbsite.0xmd.com/dbpub/officers.asp?p=14909854"
        )


if __name__ == "__main__":
    unittest.main()
