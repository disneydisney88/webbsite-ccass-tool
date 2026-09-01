"""Tests for structured error codes (handover 1.2)."""

import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd
import api
import utils.source_router as source_router
from utils.fetcher import FetchResult, IssueLookup, fetch_with_requests, webb_database_cookie_value
from utils.errors import classify_fetch_message, errors_from_fetch_summary, structured_error
from utils.parser import ParsedCCASS, build_fetch_summary
from utils.exporters import _section_context, combined_stock_csv
from utils.source_router import SourceBundle, fetch_hybrid_bundle


class ClassifyTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(classify_fetch_message("TimeoutBudgetExceeded", "budget exhausted"), "COLD_START")
        self.assertEqual(classify_fetch_message("ReadTimeout", "timed out"), "SOURCE_TIMEOUT")
        self.assertEqual(classify_fetch_message("ValueError", "no table found"), "SOURCE_CHANGED")
        self.assertEqual(classify_fetch_message("ConnectionError", "connection refused"), "SOURCE_FETCH_FAILED")
        self.assertEqual(classify_fetch_message("HTTPError", "403 Forbidden"), "SOURCE_FETCH_FAILED")
        self.assertEqual(classify_fetch_message("SOURCE_CHALLENGE", "cookie/reload challenge"), "SOURCE_CHALLENGE")
        self.assertEqual(
            classify_fetch_message("", "Issue ID unresolved: no resolver result was retained"),
            "ISSUE_ID_UNRESOLVED",
        )

    def test_structured_error_retry_flag(self):
        self.assertTrue(structured_error("COLD_START", "x")["retry_recommended"])
        self.assertFalse(structured_error("PARSE_ERROR", "x")["retry_recommended"])
        self.assertFalse(structured_error("AUTH_FAILED", "x")["retry_recommended"])
        self.assertTrue(structured_error("ISSUE_ID_UNRESOLVED", "x")["retry_recommended"])


class FetchSummaryErrorsTest(unittest.TestCase):
    def test_only_failed_rows_become_errors(self):
        summary = [
            {"Section": "Holdings", "Status": "success", "Error": ""},
            {"Section": "Price History", "Status": "failed", "Error": "Timeout budget exhausted before this section"},
        ]
        errors = errors_from_fetch_summary(summary)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["error_code"], "COLD_START")
        self.assertTrue(errors[0]["retry_recommended"])
        self.assertIn("Price History", errors[0]["message"])

    def test_big_changes_local_history_gap_is_treated_as_warning(self):
        summary = [
            {
                "Section": "Big Changes",
                "Status": "failed",
                "Error": "Big Changes require local history or mirror historical data; not enough SDW snapshots are available yet.",
            }
        ]
        errors = errors_from_fetch_summary(summary)
        self.assertEqual(errors, [])


class UnauthorizedStructuredTest(unittest.TestCase):
    def test_401_detail_is_structured(self):
        exc = api.unauthorized()
        self.assertEqual(exc.status_code, 401)
        self.assertEqual(exc.detail["error_code"], "AUTH_FAILED")
        self.assertFalse(exc.detail["retry_recommended"])


class FetchDiagnosticsTest(unittest.TestCase):
    def test_webb_database_cookie_value_matches_the_challenge_formula(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self.assertEqual(
            webb_database_cookie_value(ua, now_ms=1722470400000),
            "fb9d6ebaead0c96585660a30a89cffbe",
        )

    def test_js_cookie_challenge_can_be_solved_with_session_retry(self):
        class FakeResponse:
            def __init__(self, text: str, url: str = "https://webb-database.com/ccass/choldings.asp?i=26603"):
                self.status_code = 200
                self.reason = "OK"
                self.url = url
                self.apparent_encoding = "utf-8"
                self.text = text
                self.encoding = None

        class FakeCookies:
            def __init__(self):
                self.items = []

            def set(self, name, value, domain=None, path="/"):
                self.items.append((name, value, domain, path))

        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.cookies = FakeCookies()
                self.calls = 0

            def get(self, url, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse("<script src='/432182.js'></script><script>setCookie();location.reload();</script>")
                return FakeResponse("<table><tr><th>A</th></tr><tr><td>1</td></tr></table>")

        fake_session = FakeSession()

        with patch("requests.Session", return_value=fake_session):
            result = fetch_with_requests("Holdings", "https://webb-database.com/ccass/choldings.asp?i=26603", timeout=1)

        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.fallback_method_used, "webb-database challenge cookie")
        self.assertEqual(len(result.tables), 1)
        self.assertGreaterEqual(len(fake_session.cookies.items), 1)

    def test_js_cookie_challenge_is_reported_with_body_head(self):
        class FakeResponse:
            def __init__(self):
                self.status_code = 200
                self.reason = "OK"
                self.url = "https://example.com"
                self.apparent_encoding = "utf-8"
                self.text = "<script>setCookie(); location.reload();</script>"
                self.encoding = None

        class FakeCookies:
            def set(self, *args, **kwargs):
                pass

        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.cookies = FakeCookies()

            def get(self, url, timeout=None):
                return FakeResponse()

        with patch("requests.Session", return_value=FakeSession()):
            result = fetch_with_requests("Holdings", "https://example.com", timeout=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "JS_CHALLENGE")
        self.assertIn("JavaScript cookie/reload challenge", result.error_message)
        self.assertIn("setCookie", result.response_snippet)

    def test_hybrid_keeps_js_challenge_result_and_parser_error(self):
        challenge = "<html><head><script src='/432182.js'></script><script>setCookie();location.reload();</script></head><body></body></html>"
        failed = FetchResult(
            name="Holdings",
            url="https://webb-database.com/ccass/choldings.asp?i=15949",
            final_url="https://webb-database.com/ccass/choldings.asp?i=15949",
            status=200,
            html=challenge,
            response_snippet=challenge[:500],
            method="requests",
            ok=False,
            error_type="JS_CHALLENGE",
            error_message="JavaScript cookie/reload challenge",
        )
        empty = SourceBundle(
            lookup=IssueLookup(stock_code="08245", issue_id="15949", status="success"),
            results={},
        )
        with patch("utils.source_router.fetch_local_db_bundle", return_value=empty), patch(
            "utils.source_router.fetch_with_requests", return_value=failed
        ):
            bundle = fetch_hybrid_bundle("08245", timeout=1, headless=True)
        self.assertIs(bundle.results["Holdings"], failed)
        self.assertEqual(bundle.results["Holdings"].error_type, "JS_CHALLENGE")
        summary = build_fetch_summary(ParsedCCASS(stock_code="08245", issue_id="15949"), bundle.results)
        self.assertIn(failed.error_type, summary.loc[summary["Section"] == "Holdings", "Error"].iloc[0])
        self.assertIn(failed.error_message, summary.loc[summary["Section"] == "Holdings", "Error"].iloc[0])
        self.assertNotIn("Parsed table unavailable", summary.loc[summary["Section"] == "Holdings", "Error"].iloc[0])

    def test_hybrid_keeps_local_success_and_reports_remote_failure(self):
        local = FetchResult(name="Holdings", url="local_db://ccass_snapshots", ok=True, method="sdw+local_db")
        local_bundle = SourceBundle(
            lookup=IssueLookup(stock_code="08245", issue_id="15949", status="success"),
            results={"Holdings": local},
        )

        def failed_result(section, url, timeout, **kwargs):
            return FetchResult(
                name=section,
                url=url,
                method="requests",
                ok=False,
                status=200,
                error_type="JS_CHALLENGE",
                error_message="JavaScript cookie/reload challenge",
            )

        with patch("utils.source_router.fetch_local_db_bundle", return_value=local_bundle), patch(
            "utils.source_router.fetch_with_requests", side_effect=failed_result
        ):
            bundle = fetch_hybrid_bundle("08245", timeout=1)
        self.assertIs(bundle.results["Holdings"], local)
        self.assertTrue(bundle.results["Holdings"].ok)
        self.assertEqual(bundle.results["Holdings"].attempted_sources[0]["error_type"], "JS_CHALLENGE")
        summary = build_fetch_summary(ParsedCCASS(stock_code="08245", issue_id="15949"), bundle.results)
        error = summary.loc[summary["Section"] == "Holdings", "Error"].iloc[0]
        self.assertIn("local data retained", error)
        self.assertIn("JS_CHALLENGE", error)

    def test_hybrid_remote_success_replaces_local_snapshot(self):
        local = FetchResult(name="Holdings", url="local_db://ccass_snapshots", ok=True, method="sdw+local_db")
        local_bundle = SourceBundle(
            lookup=IssueLookup(stock_code="08245", issue_id="15949", status="success"),
            results={"Holdings": local},
        )

        def successful_result(section, url, timeout, **kwargs):
            return FetchResult(name=section, url=url, method="requests", ok=True, tables=[])

        with patch("utils.source_router.fetch_local_db_bundle", return_value=local_bundle), patch(
            "utils.source_router.fetch_with_requests", side_effect=successful_result
        ):
            bundle = fetch_hybrid_bundle("08245", timeout=1)
        # Remote success is newer than a local snapshot, so it is authoritative.
        self.assertEqual(bundle.results["Holdings"].method, "requests")
        self.assertNotEqual(bundle.results["Holdings"].url, local.url)

    def test_hybrid_resolver_failure_is_retained_when_local_db_is_empty(self):
        failed_company = FetchResult(
            name="Company / orgdata",
            url="https://webb-database.com/dbpub/orgdata.asp?code=08245&Submit=current",
            ok=False,
            error_type="JS_CHALLENGE",
            error_message="JavaScript cookie/reload challenge",
        )
        local_bundle = SourceBundle(
            lookup=IssueLookup(stock_code="08245", issue_id="", status="failed"),
            results={},
            metadata={"source": "local_db"},
        )
        resolved = IssueLookup(stock_code="08245", status="failed", result=failed_company)
        with patch("utils.source_router.fetch_local_db_bundle", return_value=local_bundle), patch(
            "utils.source_router.resolve_issue_id", return_value=resolved
        ):
            bundle = fetch_hybrid_bundle("08245", timeout=1)
        self.assertIs(bundle.results["Company / orgdata"], failed_company)
        summary = build_fetch_summary(ParsedCCASS(stock_code="08245"), bundle.results)
        errors = summary["Error"].tolist()
        self.assertIn("JS_CHALLENGE", errors[0])
        self.assertTrue(all(error for error in errors))
        self.assertTrue(all("Issue ID unresolved" in error for error in errors[1:]))

    def test_fetch_summary_empty_results_still_has_nonempty_errors(self):
        summary = build_fetch_summary(ParsedCCASS(stock_code="08245"), {})
        self.assertEqual(len(summary), 6)
        self.assertTrue(all(summary["Error"].astype(bool)))

    def test_local_lookup_without_issue_id_is_not_success(self):
        with patch("utils.source_router.load_stock_map", return_value={"issue_id": "", "name": ""}), patch(
            "utils.source_router.build_results_from_db",
            return_value=type("Built", (), {"results": {}, "warnings": [], "stock_name": "", "history_depth_days": 0, "latest_date": ""})(),
        ):
            bundle = source_router.fetch_local_db_bundle("08245")
        self.assertEqual(bundle.lookup.status, "failed")

    def test_local_stock_map_lookup_retains_success_result(self):
        built = type("Built", (), {
            "results": {}, "warnings": [], "stock_name": "", "history_depth_days": 0,
            "latest_date": "",
        })()
        with patch("utils.source_router.load_stock_map", return_value={"issue_id": "15949", "name": "Canopy SkyFire"}), patch(
            "utils.source_router.build_results_from_db", return_value=built,
        ):
            bundle = source_router.fetch_local_db_bundle("08245")
        self.assertEqual(bundle.lookup.status, "success")
        self.assertEqual(bundle.lookup.issue_id, "15949")
        self.assertIsNotNone(bundle.lookup.result)
        self.assertTrue(bundle.lookup.result.ok)
        self.assertEqual(bundle.lookup.result.method, "cache")
        self.assertIn("Company / orgdata", bundle.results)
        self.assertEqual(bundle.results["Company / orgdata"].url, "local_db://stock_map/08245")

    def test_failed_payload_is_not_cached(self):
        api._stock_cache.clear()
        failed = {
            "exported": {
                "metadata": {"issue_id": "15949"},
                "fetch_summary": [{"Section": "Holdings", "Status": "failed"}],
            }
        }
        api.cache_set("08245", failed)
        self.assertIsNone(api.cache_get("08245"))

    def test_cache_metadata_records_served_state(self):
        payload = api.minimal_base_payload("08245", "15949", [], {})
        self.assertFalse(payload["exported"]["metadata"]["served_from_cache"])
        api._set_served_from_cache(payload, True)
        self.assertTrue(payload["exported"]["metadata"]["served_from_cache"])

    def test_unresolved_summary_does_not_use_parse_placeholder_for_company(self):
        summary = build_fetch_summary(ParsedCCASS(stock_code="08245"), {})
        self.assertTrue(all("Issue ID unresolved:" in error for error in summary["Error"]))
        self.assertNotIn("Parsed table unavailable", " ".join(summary["Error"].tolist()))

    def test_local_snapshot_can_continue_without_issue_id(self):
        local_holdings = FetchResult(
            name="Holdings", url="local_db://ccass_snapshots", ok=True,
            tables=[pd.DataFrame({"Participant": ["B00001"]})],
        )
        local_concentration = FetchResult(
            name="Concentration", url="local_db://ccass_concentration", ok=True,
            tables=[pd.DataFrame({"Date": ["2026-08-28"]})],
        )
        local_bundle = SourceBundle(
            lookup=IssueLookup(stock_code="01592", issue_id="", status="failed"),
            results={"Holdings": local_holdings, "Concentration": local_concentration},
            metadata={"source": "local_db"},
        )
        dummy = ParsedCCASS(stock_code="01592", issue_id="")
        with patch("api.cache_get", return_value=None), patch("api.cache_set"), patch(
            "api.fetch_source_bundle_for_stock", return_value=local_bundle
        ), patch(
            "api.resolve_issue_id", return_value=IssueLookup(stock_code="01592", issue_id="26603", status="success")
        ), patch("api.parse_results", return_value=dummy) as parse_results, patch(
            "api.parsed_to_json_ready", return_value={"metadata": {}, "holdings": [], "concentration": []}
        ):
            api.build_base_payload("01592", timeout=10)
        parse_results.assert_called_once()

    def test_minimal_resolver_failure_preserves_fetch_summary(self):
        failed_company = FetchResult(
            name="Company / orgdata",
            url="https://webb-database.com/dbpub/orgdata.asp?code=08245&Submit=current",
            ok=False,
            status=200,
            error_type="JS_CHALLENGE",
            error_message="Upstream returned a JavaScript cookie/reload challenge",
        )
        lookup = IssueLookup(stock_code="08245", status="failed", result=failed_company)
        with patch("api.cache_get", return_value=None), patch("api.cache_set"), patch(
            "api.resolve_issue_id", return_value=lookup
        ):
            payload = api.build_base_payload("08245", timeout=10)
        summary = payload["exported"]["fetch_summary"]
        self.assertTrue(summary)
        self.assertEqual(summary[0]["section"], "Company / orgdata")
        self.assertEqual(summary[0]["error_type"], "JS_CHALLENGE")
        self.assertEqual(summary[0]["status_code"], 200)
        self.assertIn("JavaScript cookie/reload challenge", summary[0]["error_message"])
        self.assertEqual(summary[0]["url"], failed_company.url)

    def test_minimal_payload_always_has_fetch_summary_key(self):
        payload = api.minimal_base_payload("08245", None, ["unresolved"])
        self.assertIn("fetch_summary", payload["exported"])
        self.assertEqual(payload["exported"]["fetch_summary"], [])

    def test_legacy_result_without_attempted_sources_is_safe_in_exporters(self):
        parsed = ParsedCCASS(stock_code="08245", issue_id="15949")
        legacy_result = SimpleNamespace(
            url="https://example.test/holdings",
            final_url="",
            ok=True,
            error_message="",
            method="fixture",
            status=200,
            error_type="",
            fallback_method_used="",
        )
        context = _section_context(
            parsed,
            "Holdings",
            "fixture holdings",
            "2026-08-28",
            legacy_result,
            0,
        )
        self.assertEqual(context["fetch_status"], "no_matching_table")
        output = combined_stock_csv(parsed, {"Holdings": legacy_result})
        self.assertIsInstance(output, bytes)


class HealthUpstreamsTest(unittest.TestCase):
    def test_health_can_include_upstream_probes(self):
        with patch.object(api, "probe_upstreams", return_value={"probes": [{"source": "webbsite", "ok": False}]}):
            payload = api.health(upstreams=True)
        self.assertTrue(payload["ok"])
        self.assertIn("commit", payload)
        self.assertEqual(payload["commit"], api.GIT_SHA)
        self.assertEqual(payload["upstreams"]["probes"][0]["source"], "webbsite")


class PayloadStatusTest(unittest.TestCase):
    def test_payload_status_fields_surface_fetch_details(self):
        errors = [structured_error("SOURCE_CHALLENGE", "Holdings: SOURCE_CHALLENGE")]
        summary = [
            {
                "section": "Holdings",
                "ok": False,
                "status_code": 200,
                "status_reason": "OK",
                "final_url": "https://webb-database.com/ccass/choldings.asp?i=26603",
                "attempts": 1,
                "response_snippet": "setCookie(); location.reload();",
                "error_type": "SOURCE_CHALLENGE",
                "error_message": "cookie/reload challenge",
            }
        ]
        fields = api.payload_status_fields(errors, summary)
        self.assertFalse(fields["ok"])
        self.assertEqual(fields["error_code"], "SOURCE_CHALLENGE")
        self.assertEqual(fields["status_code"], 200)
        self.assertEqual(fields["attempts"], 1)
        self.assertIn("setCookie", fields["body_head"])

    def test_mcp_exception_payload_is_return_value_shape(self):
        payload = api.mcp_exception_payload("get_ccass_stock_data", RuntimeError("boom"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["tool"], "get_ccass_stock_data")
        self.assertEqual(payload["error_code"], "TOOL_EXECUTION_FAILED")


if __name__ == "__main__":
    unittest.main()
