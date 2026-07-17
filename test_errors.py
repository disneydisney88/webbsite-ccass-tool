"""Tests for structured error codes (handover 1.2)."""

import unittest

import api
from utils.errors import classify_fetch_message, errors_from_fetch_summary, structured_error


class ClassifyTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(classify_fetch_message("TimeoutBudgetExceeded", "budget exhausted"), "COLD_START")
        self.assertEqual(classify_fetch_message("ReadTimeout", "timed out"), "SOURCE_TIMEOUT")
        self.assertEqual(classify_fetch_message("ValueError", "no table found"), "SOURCE_CHANGED")
        self.assertEqual(classify_fetch_message("ConnectionError", "connection refused"), "SOURCE_FETCH_FAILED")
        self.assertEqual(classify_fetch_message("HTTPError", "403 Forbidden"), "SOURCE_FETCH_FAILED")

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


if __name__ == "__main__":
    unittest.main()
