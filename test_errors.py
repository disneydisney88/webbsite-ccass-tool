"""Tests for structured error codes (handover 1.2)."""

import unittest
from unittest.mock import patch

import api
from utils.fetcher import fetch_with_requests
from utils.errors import classify_fetch_message, errors_from_fetch_summary, structured_error


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


class UnauthorizedStructuredTest(unittest.TestCase):
    def test_401_detail_is_structured(self):
        exc = api.unauthorized()
        self.assertEqual(exc.status_code, 401)
        self.assertEqual(exc.detail["error_code"], "AUTH_FAILED")
        self.assertFalse(exc.detail["retry_recommended"])


class FetchDiagnosticsTest(unittest.TestCase):
    def test_js_cookie_challenge_is_reported_with_body_head(self):
        class FakeResponse:
            status_code = 200
            reason = "OK"
            url = "https://webb-database.com/ccass/choldings.asp?i=26603"
            apparent_encoding = "utf-8"
            text = "<script>setCookie(); location.reload();</script>"

        with patch("requests.get", return_value=FakeResponse()):
            result = fetch_with_requests("Holdings", FakeResponse.url, timeout=1)
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
