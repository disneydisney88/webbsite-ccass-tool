from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import api


class ApiAuthTests(unittest.TestCase):
    def test_missing_token_rejected_for_api_path(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "API_TOKEN is not set"):
                api.valid_api_auth("/api/stock", authorization="")

    def test_wrong_token_rejected(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            self.assertFalse(api.valid_api_auth("/api/stock", authorization="Bearer wrong-token"))

    def test_missing_authorization_rejected(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            self.assertFalse(api.valid_api_auth("/api/stock", authorization=""))

    def test_malformed_authorization_rejected(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            self.assertFalse(api.valid_api_auth("/api/stock", authorization="Token correct-token"))

    def test_correct_bearer_token_accepted(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            self.assertTrue(api.valid_api_auth("/api/stock", authorization="Bearer correct-token"))

    def test_correct_x_api_key_accepted(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            self.assertTrue(api.valid_api_auth("/api/stock", x_api_key="correct-token"))

    def test_openapi_is_public_without_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(api.valid_api_auth("/openapi.json"))

    def test_openapi_schema_has_bearer_security_for_stock_endpoint(self) -> None:
        schema = api.openapi_schema("https://example.com")
        self.assertEqual(schema["components"]["securitySchemes"]["bearerAuth"]["type"], "http")
        self.assertEqual(schema["components"]["securitySchemes"]["bearerAuth"]["scheme"], "bearer")
        stock_security = schema["paths"]["/api/stock"]["get"]["security"]
        self.assertIn({"bearerAuth": []}, stock_security)


if __name__ == "__main__":
    unittest.main()
