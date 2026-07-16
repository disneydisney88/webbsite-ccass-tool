"""Tests for the 同花順 F10 equity-page parser (share capital changes + buybacks)."""

import os
import unittest

from utils.f10_equity import (
    f10_equity_url,
    latest_share_capital,
    parse_f10_buybacks,
    parse_f10_share_changes,
    reason_tags,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "samples")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


class ShareChangesTest(unittest.TestCase):
    def setUp(self):
        self.html = load("f10_equity_02028.html")
        self.changes = parse_f10_share_changes(self.html)

    def test_rows_parsed_and_capit_box_ignored(self):
        # 6 rows in the 股本变化 table; the 总股本结构 (capit) box must not leak in.
        self.assertEqual(len(self.changes), 6)
        self.assertEqual(self.changes[0]["announce_date"], "2026-07-14")

    def test_placement_row(self):
        first = self.changes[0]
        self.assertEqual(first["shares_million"], 858.33)
        self.assertEqual(first["shares_approx"], 858_330_000)
        self.assertEqual(first["reason"], "配售新股")
        self.assertEqual(first["reason_tags"], ["placement"])
        self.assertEqual(first["change_date"], "2026-07-14")

    def test_multi_reason_row_gets_multiple_tags(self):
        multi = next(c for c in self.changes if c["announce_date"] == "2007-05-03")
        self.assertEqual(set(multi["reason_tags"]), {"placement", "consideration_issue", "buyback_cancellation"})

    def test_reason_tag_helper(self):
        self.assertEqual(reason_tags("行使购股权"), ["option_exercise"])
        self.assertEqual(reason_tags("港股首发上市"), ["ipo"])
        self.assertEqual(reason_tags(None), [])
        self.assertEqual(reason_tags("未知原因"), [])

    def test_latest_share_capital(self):
        latest = latest_share_capital(self.changes)
        self.assertEqual(latest["as_of"], "2026-07-14")
        self.assertEqual(latest["shares_approx"], 858_330_000)

    def test_url(self):
        self.assertEqual(f10_equity_url("02028"), "https://basic.10jqka.com.cn/HK2028/equity.html")


class BuybacksTest(unittest.TestCase):
    def setUp(self):
        self.buybacks = parse_f10_buybacks(load("f10_equity_02028.html"))

    def test_rows_parsed(self):
        self.assertEqual(len(self.buybacks), 3)
        first = self.buybacks[0]
        self.assertEqual(first["buyback_date"], "2018-12-07")
        self.assertEqual(first["amount_wan"], 374.93)
        self.assertEqual(first["shares_wan"], 833.20)
        self.assertEqual(first["high_price"], 0.45)
        self.assertEqual(first["currency"], "港元")

    def test_missing_announce_date_is_null(self):
        dashed = next(b for b in self.buybacks if b["buyback_date"] == "2008-01-21")
        self.assertIsNone(dashed["announce_date"])
        self.assertEqual(dashed["amount_wan"], 0.44)


if __name__ == "__main__":
    unittest.main()
