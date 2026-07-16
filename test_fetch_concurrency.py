"""Tests for concurrent CCASS section fetching.

Regression guard for the Phase 1 fix where "Price History" (always the last
section) was starved of the shared timeout budget by the four sections fetched
ahead of it. Sections are now fetched concurrently, so a budget that would only
allow a couple of serial fetches still lets every section complete.
"""

import time
import unittest

import api
from utils.fetcher import FetchResult, IssueLookup


def make_ok_result(name: str, url: str, timeout: int, sleep_seconds: float = 0.0) -> FetchResult:
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return FetchResult(name=name, url=url, method="fake", ok=True, status=200)


class FetchCompactResultsConcurrencyTest(unittest.TestCase):
    def _lookup(self) -> IssueLookup:
        orgdata = FetchResult(name="Company / orgdata", url="http://example/org", ok=True, status=200)
        return IssueLookup(stock_code="01592", issue_id="26603", method="test", status="success", result=orgdata)

    def test_all_sections_fetched_when_serial_would_starve(self):
        # Each fake fetch sleeps 0.5s. Five serial fetches (2.5s) would blow a
        # 2.0s budget and starve the trailing section(s); concurrent fetches all
        # finish in ~0.5s.
        sleep = 0.5
        deadline = time.monotonic() + 2.0

        def fake_fetch(name: str, url: str, timeout: int) -> FetchResult:
            return make_ok_result(name, url, timeout, sleep_seconds=sleep)

        started = time.monotonic()
        results = api.fetch_compact_results(
            issue_id="26603",
            stock_code="01592",
            lookup=self._lookup(),
            timeout=30,
            deadline=deadline,
            fetch_fn=fake_fetch,
        )
        elapsed = time.monotonic() - started

        for section in api.SECTION_NAMES:
            self.assertIn(section, results, f"{section} missing from results")
            self.assertTrue(results[section].ok, f"{section} was not fetched successfully")
        # Price History is the section that used to starve.
        self.assertTrue(results["Price History"].ok)
        # Concurrency check: wall-clock must be far below the serial sum (2.5s).
        self.assertLess(elapsed, 1.5, f"sections did not run concurrently (elapsed {elapsed:.2f}s)")

    def test_canonical_section_order_preserved(self):
        def fake_fetch(name: str, url: str, timeout: int) -> FetchResult:
            return make_ok_result(name, url, timeout)

        results = api.fetch_compact_results(
            issue_id="26603",
            stock_code="01592",
            lookup=self._lookup(),
            timeout=30,
            deadline=time.monotonic() + 30,
            fetch_fn=fake_fetch,
        )
        section_keys = [key for key in results if key in api.SECTION_NAMES]
        self.assertEqual(section_keys, api.SECTION_NAMES)

    def test_exhausted_budget_marks_all_sections_failed(self):
        calls: list[str] = []

        def fake_fetch(name: str, url: str, timeout: int) -> FetchResult:
            calls.append(name)
            return make_ok_result(name, url, timeout)

        results = api.fetch_compact_results(
            issue_id="26603",
            stock_code="01592",
            lookup=self._lookup(),
            timeout=30,
            deadline=time.monotonic() - 1,  # already past
            fetch_fn=fake_fetch,
        )
        self.assertEqual(calls, [], "no section should be fetched once the budget is gone")
        for section in api.SECTION_NAMES:
            self.assertIn(section, results)
            self.assertFalse(results[section].ok)
            self.assertIn("Timeout budget exhausted", results[section].error_message)


if __name__ == "__main__":
    unittest.main()
