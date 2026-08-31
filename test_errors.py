"""Tests for structured error codes (handover 1.2)."""

import unittest
from unittest.mock import patch

import api
from utils.fetcher import FetchResult, IssueLookup, fetch_with_requests, webb_database_cookie_value
from utils.errors import classify_fetch_message, errors_from_fetch_summary, structured_error
from utils.parser import ParsedCCASS, build_fetch_summary
from utils.source_router import SourceBundle, fetch_hybrid_bundle


class ClassifyTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(classify_fetch_message("TimeoutBudgetExceeded", "budget exhausted"), "COLD_START")
        self.assertEqual(classify_fetch_message("ReadTimeout", "timed out"), "SOURCE_TIMEOUT")
        self.assertEqual(classify_fetch_message("ValueError", "no table found"), "SOURCE_CHANGED")
        self.assertEqual(classify_fetch_message("ConnectionError", "connection refused"), "SOURCE_FETCH_FAILED")
        self.assertEqual(classify_fetch_message("HTTPError", "403 Forbidden"), "SOURCE_FETCH_FAILED")
        self.assertEqual(classify_fetch_message("SOURCE_CHALLENGE", "cookie/reload challenge"), "SOURCE_CHALLENGE")

    def test_structured_error_retry_flag(self):
        self.assertTrue(structured_error("COLD_START", "x")["retry_recommended"])
        self.assertFalse(structured_error("PARSE_ERROR", "x")["retry_recommended"])
        self.assertFalse(structured_error("AUTH_FAILED", "x")["retry_recommended"])


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
        self.assertEqual(summary.loc[summary["Section"] == "Holdings", "Error"].iloc[0], failed.error_message)
        self.assertNotIn("Parsed table unavailable", summary.loc[summary["Section"] == "Holdings", "Error"].iloc[0])


class HealthUpstreamsTest(unittest.TestCase):
    def test_health_can_include_upstream_probes(self):
        with patch.object(api, "probe_upstreams", return_value={"probes": [{"source": "webbsite", "ok": False}]}):
            payload = api.health(upstreams=True)
        self.assertTrue(payload["ok"])
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
