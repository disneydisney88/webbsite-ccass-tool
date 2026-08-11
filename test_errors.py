"""Tests for structured error codes (handover 1.2)."""

import unittest
from unittest.mock import patch

import api
from utils.fetcher import body_head, extract_tables_from_html, fetch_with_requests, webb_database_cookie_value
from utils.errors import (
    classify_fetch_message,
    errors_from_fetch_summary,
    errors_from_warnings,
    structured_error,
)


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

    def test_local_snapshot_empty_is_distinct_from_no_upstream_data(self):
        errors = errors_from_warnings(
            ["LOCAL_SNAPSHOT_EMPTY: no SDW snapshot is available for this stock."]
        )
        self.assertEqual(errors[0]["error_code"], "LOCAL_SNAPSHOT_EMPTY")
        self.assertFalse(errors[0]["retry_recommended"])

    def test_mirror_blocked_warning_has_dedicated_code(self):
        errors = errors_from_warnings(
            ["MIRROR_BLOCKED: automatic mirror probe returned 403/human verification."]
        )
        self.assertEqual(errors[0]["error_code"], "MIRROR_BLOCKED")


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
    def test_body_head_keeps_first_2000_characters(self):
        self.assertEqual(len(body_head("x" * 2500)), 2000)

    def test_hidden_webb_table_is_still_parsed(self):
        tables = extract_tables_from_html(
            "<table style='display:none'><tr><th>Participant</th><th>Holding</th></tr>"
            "<tr><td>Broker A</td><td>1000</td></tr></table>"
        )
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].iloc[0]["Participant"], "Broker A")

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
        self.assertEqual(result.error_type, "SOURCE_CHALLENGE")
        self.assertIn("JavaScript cookie/reload challenge", result.error_message)
        self.assertIn("setCookie", result.response_snippet)


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
