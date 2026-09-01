"""Fixture tests for the MCP requests-only hybrid_light source path."""

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils.fetcher import FetchResult, IssueLookup
from utils.parser import ParsedCCASS, SectionParse, assess_completeness, build_fetch_summary
from utils.source_router import SourceBundle, fetch_hybrid_light_bundle


class HybridLightRoutingTest(unittest.TestCase):
    def setUp(self):
        self.lookup_result = FetchResult(
            name="Company / orgdata",
            url="local_db://stock_map/08245",
            method="cache",
            ok=True,
            tables=[pd.DataFrame([{"Code": "08245", "Name": "Canopy SkyFire"}])],
        )
        self.local = SourceBundle(
            lookup=IssueLookup(
                stock_code="08245",
                issue_id="15949",
                method="cache",
                status="success",
                result=self.lookup_result,
            ),
            results={"Company / orgdata": self.lookup_result},
            metadata={"source": "local_db"},
        )

    def test_requests_only_sections_and_skipped_browser_sections(self):
        def request_result(name, url, timeout=30, **kwargs):
            return FetchResult(
                name=name,
                url=url,
                method="requests",
                status=200,
                ok=True,
                tables=[pd.DataFrame([{"Date": "2026-08-01", "Value": 1}])],
            )

        with patch("utils.source_router.fetch_local_db_bundle", return_value=self.local), patch(
            "utils.source_router.fetch_with_requests", side_effect=request_result
        ) as requests_fetch, patch("utils.source_router.fetch_with_playwright") as browser_fetch:
            bundle = fetch_hybrid_light_bundle("08245", timeout=5)

        self.assertFalse(browser_fetch.called)
        self.assertEqual(requests_fetch.call_count, 3)
        self.assertTrue(bundle.results["Concentration"].ok)
        self.assertTrue(bundle.results["Big Changes"].ok)
        self.assertTrue(bundle.results["Price History"].ok)
        self.assertEqual(bundle.results["Holdings"].method, "skipped")
        self.assertTrue(bundle.results["Holdings"].skipped)

    def test_summary_exposes_skipped_without_calling_it_failed(self):
        skipped = FetchResult(
            name="Holdings",
            url="https://example.test/holdings",
            method="skipped",
            ok=False,
            skipped=True,
            error_type="BROWSER_REQUIRED",
            error_message=(
                "Requires browser; use source_preference='auto' via HTTP API "
                "(MCP wall-clock budget too short)"
            ),
        )
        parsed = ParsedCCASS(stock_code="08245", issue_id="15949")
        summary = build_fetch_summary(parsed, {"Holdings": skipped})
        row = summary.loc[summary["Section"] == "Holdings"].iloc[0]
        self.assertEqual(row["Status"], "skipped")
        self.assertIn("Requires browser", row["Error"])

    def test_skipped_critical_section_is_not_complete(self):
        parsed = ParsedCCASS(stock_code="08245", issue_id="15949")
        parsed.section_parses = {
            "Holdings": SectionParse("Holdings", status="skipped"),
            "Concentration": SectionParse("Concentration", status="success"),
            "Price History": SectionParse("Price History", status="success"),
            "Changes": SectionParse("Changes", status="skipped"),
            "Big Changes": SectionParse("Big Changes", status="success"),
        }
        parsed.concentration_table = pd.DataFrame([{"Date": "2026-08-01"}])
        parsed.price_history_table = pd.DataFrame([{"Date": "2026-08-01"}])
        assess_completeness(parsed)
        self.assertEqual(parsed.completeness_status, "partial")


class SnapshotWorkflowContractTest(unittest.TestCase):
    def test_workflow_reports_three_states_and_staleness(self):
        path = Path(__file__).parent / ".github" / "workflows" / "daily_snapshot.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn('"succeeded"', text)
        self.assertIn('"skipped"', text)
        self.assertIn('"failed"', text)
        self.assertIn("succeeded == 0", text)
        self.assertIn("SNAPSHOT_MAX_AGE_DAYS", text)
        self.assertIn("::warning::Latest CCASS snapshot is stale", text)


if __name__ == "__main__":
    unittest.main()
