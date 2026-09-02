"""Fixture tests for the MCP requests-only hybrid_light source path."""

import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pandas as pd

import api
from utils.exporters import parsed_to_json_ready
from utils.fetcher import FetchResult, IssueLookup
from utils.parser import (
    ParsedCCASS,
    SectionParse,
    add_cross_section_warnings,
    assess_completeness,
    build_fetch_summary,
    parse_results,
)
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
        concurrent_requests = Barrier(2, timeout=2)

        def request_result(name, url, timeout=30, **kwargs):
            concurrent_requests.wait()
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
        self.assertEqual(requests_fetch.call_count, 2)
        self.assertTrue(bundle.results["Concentration"].ok)
        self.assertTrue(bundle.results["Big Changes"].ok)
        self.assertTrue(bundle.results["Price History"].skipped)
        self.assertIn("get_webbsite_price_history", bundle.results["Price History"].error_message)
        self.assertEqual(bundle.results["Holdings"].method, "skipped")
        self.assertTrue(bundle.results["Holdings"].skipped)

    def test_include_price_history_fetches_the_opt_in_section(self):
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
        ) as requests_fetch:
            bundle = fetch_hybrid_light_bundle(
                "08245",
                timeout=5,
                include_price_history=True,
            )

        self.assertEqual(requests_fetch.call_count, 3)
        self.assertTrue(bundle.results["Price History"].ok)
        self.assertEqual(bundle.results["Price History"].method, "requests")

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
            "Holdings": SectionParse(
                "Holdings",
                status="skipped",
                error="Requires browser; use source_preference='auto' via HTTP API",
            ),
            "Concentration": SectionParse("Concentration", status="success"),
            "Price History": SectionParse(
                "Price History",
                status="skipped",
                error="Not fetched in hybrid_light; use get_webbsite_price_history",
            ),
            "Changes": SectionParse(
                "Changes",
                status="skipped",
                error="Requires browser; use source_preference='auto' via HTTP API",
            ),
            "Big Changes": SectionParse("Big Changes", status="success"),
        }
        parsed.concentration_table = pd.DataFrame([{"Date": "2026-08-01"}])
        parsed.big_changes_table = pd.DataFrame([{"Date": "2026-08-01"}])
        add_cross_section_warnings(parsed)
        assess_completeness(parsed)

        self.assertEqual(parsed.completeness_status, "partial")
        self.assertEqual(parsed.critical_sections_failed, [])
        self.assertIn(
            "Holdings not fetched in hybrid_light (requires browser); broker-level analysis unavailable.",
            parsed.analysis_warnings,
        )
        self.assertIn(
            "Daily Changes not fetched in hybrid_light (requires browser); recent daily movement cannot be confirmed.",
            parsed.analysis_warnings,
        )
        self.assertFalse(any("Critical sections failed" in item for item in parsed.analysis_warnings))
        self.assertFalse(any("Price History" in item for item in parsed.analysis_warnings))

    def test_not_requested_price_history_does_not_reduce_completeness(self):
        parsed = ParsedCCASS(stock_code="08245", issue_id="15949")
        parsed.section_parses = {
            "Holdings": SectionParse("Holdings", status="success"),
            "Concentration": SectionParse("Concentration", status="success"),
            "Changes": SectionParse("Changes", status="success"),
            "Big Changes": SectionParse("Big Changes", status="success"),
            "Price History": SectionParse(
                "Price History",
                status="skipped",
                error="Not fetched in hybrid_light; use get_webbsite_price_history",
            ),
        }
        parsed.holdings_table = pd.DataFrame([{"Participant": "Example"}])
        parsed.concentration_table = pd.DataFrame([{"Date": "2026-08-01"}])
        parsed.changes_table = pd.DataFrame([{"Date": "2026-08-01"}])
        parsed.big_changes_table = pd.DataFrame([{"Date": "2026-08-01"}])

        add_cross_section_warnings(parsed)
        assess_completeness(parsed)

        self.assertEqual(parsed.completeness_status, "complete")
        self.assertEqual(parsed.critical_sections_failed, [])
        self.assertEqual(parsed.analysis_warnings, [])


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


class CompactParseLimitTest(unittest.TestCase):
    def test_concentration_is_truncated_before_records_conversion(self):
        dates = pd.date_range(end="2026-08-31", periods=1802, freq="D")[::-1]
        table = pd.DataFrame(
            {
                "Date": [value.strftime("%Y-%m-%d") for value in dates],
                "Top 5 %": [70.0] * 1802,
                "Top 10 %": [80.0] * 1802,
                "Stake in CCASS %": [90.0] * 1802,
            }
        )
        result = FetchResult(
            name="Concentration",
            url="https://example.test/concentration",
            fetched_time="2026-09-01T00:00:00+00:00",
            method="requests",
            status=200,
            ok=True,
            tables=[table],
        )

        parsed = parse_results(
            "15949",
            {"Concentration": result},
            stock_code="08245",
            section_limits={"Concentration": 10},
        )
        exported = parsed_to_json_ready(parsed, {"Concentration": result})

        self.assertEqual(len(parsed.concentration_table), 10)
        self.assertEqual(len(exported["concentration"]), 10)
        self.assertEqual(exported["metadata"]["section_total_counts"]["Concentration"], 1802)

    def test_compact_summary_preserves_the_full_concentration_count(self):
        records = [
            {"Date": f"2026-08-{day:02d}", "Top 5 %": 70.0, "Top 10 %": 80.0}
            for day in range(1, 11)
        ]
        base = {
            "issue_id": "15949",
            "exported": {
                "metadata": {
                    "stock_code": "08245",
                    "issue_id": "15949",
                    "id_lookup_status": "success",
                    "section_total_counts": {"Concentration": 1802},
                },
                "holdings": [],
                "changes": [],
                "bigchanges": [],
                "concentration": records,
                "price_history": [],
                "fetch_summary": [],
                "analysis_warnings": [],
            },
        }

        with patch.object(api, "build_base_payload", return_value=base):
            payload = api.build_stock_payload("08245", concentration_limit=10)

        self.assertEqual(payload["holdings_summary"]["concentration_total_count"], 1802)
        self.assertEqual(payload["holdings_summary"]["concentration_returned_count"], 10)

    def test_budget_is_selected_by_source_preference(self):
        self.assertEqual(api.stock_tool_budget("local_db"), 15)
        self.assertEqual(api.stock_tool_budget("hybrid_light"), 30)
        self.assertEqual(api.stock_tool_budget("auto"), 45)


if __name__ == "__main__":
    unittest.main()
