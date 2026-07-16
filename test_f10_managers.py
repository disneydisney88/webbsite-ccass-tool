"""Tests for the 同花順 F10 HK manager-page parser."""

import os
import unittest

from utils.f10_managers import (
    f10_managers_url,
    f10_stock_slug,
    parse_f10_managers_html,
    parse_f10_stock_name,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "samples")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


class SlugTest(unittest.TestCase):
    def test_four_digit_slug(self):
        self.assertEqual(f10_stock_slug("00550"), "HK0550")
        self.assertEqual(f10_stock_slug("06162"), "HK6162")
        self.assertEqual(f10_stock_slug("02028"), "HK2028")
        self.assertEqual(f10_stock_slug("550"), "HK0550")

    def test_url(self):
        self.assertEqual(f10_managers_url("00550"), "https://basic.10jqka.com.cn/HK0550/manager.html")

    def test_empty(self):
        self.assertEqual(f10_stock_slug(""), "")
        self.assertEqual(f10_managers_url(""), "")


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.html = load("f10_managers_00550.html")
        self.managers = parse_f10_managers_html(self.html)

    def test_all_managers_parsed(self):
        self.assertEqual(len(self.managers), 3)
        self.assertEqual([m["name"] for m in self.managers], ["麻长炜", "甘鹏", "何佩玲"])

    def test_positions_and_tenure(self):
        ma = self.managers[0]
        self.assertEqual(ma["positions"], "董事会主席，非执行董事")
        self.assertEqual(ma["tenure_from"], "2026-01-20")
        self.assertIsNone(ma["tenure_to"])
        self.assertTrue(ma["is_current"])

    def test_intro_fields(self):
        gan = self.managers[1]
        self.assertEqual(gan["sex"], "男")
        self.assertEqual(gan["age"], 34)
        self.assertEqual(gan["education"], "本科")
        self.assertEqual(gan["salary"], "27.20万")

    def test_missing_fields_are_null(self):
        ma = self.managers[0]
        self.assertIsNone(ma["salary"])  # 报酬：--
        self.assertIsNone(ma["education"])
        ho = self.managers[2]
        self.assertIsNone(ho["age"])  # 女  硕士 (no age)
        self.assertEqual(ho["education"], "硕士")
        self.assertEqual(ho["sex"], "女")

    def test_biography_kept(self):
        for manager in self.managers:
            self.assertTrue(manager["biography"])
        self.assertIn("Alibaba.com联合创始人", self.managers[0]["biography"])
        self.assertIn("公司秘书", self.managers[2]["biography"])

    def test_stock_name(self):
        self.assertEqual(parse_f10_stock_name(self.html), "律齐文化")


if __name__ == "__main__":
    unittest.main()
