import asyncio
import json
import os
import unittest
from unittest.mock import patch

import api


def asgi_get(path: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    async def run_request() -> tuple[int, dict]:
        response_messages = []
        raw_path, _, raw_query = path.partition("?")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": raw_path,
            "raw_path": raw_path.encode("ascii"),
            "query_string": raw_query.encode("ascii"),
            "headers": [(key.lower().encode("ascii"), value.encode("ascii")) for key, value in (headers or {}).items()],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            response_messages.append(message)

        await api.app(scope, receive, send)
        status = next(message["status"] for message in response_messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in response_messages if message["type"] == "http.response.body")
        return status, json.loads(body.decode("utf-8"))

    return asyncio.run(run_request())


class ApiAuthTests(unittest.TestCase):
    def test_health_without_token_returns_200(self) -> None:
        status_code, _ = asgi_get("/health")
        self.assertEqual(status_code, 200)

    def test_openapi_without_token_returns_200_and_declares_bearer(self) -> None:
        status_code, schema = asgi_get("/openapi.json")
        self.assertEqual(status_code, 200)
        bearer = schema["components"]["securitySchemes"]["HTTPBearer"]
        self.assertEqual(bearer["type"], "http")
        self.assertEqual(bearer["scheme"], "bearer")

    def test_stock_without_token_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592")
        self.assertEqual(status_code, 401)
        self.assertEqual(payload, {"detail": "Unauthorized"})

    def test_stock_with_wrong_token_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592", headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(status_code, 401)
        self.assertEqual(payload, {"detail": "Unauthorized"})

    def test_stock_with_malformed_authorization_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592", headers={"Authorization": "Token correct-token"})
        self.assertEqual(status_code, 401)
        self.assertEqual(payload, {"detail": "Unauthorized"})

    def test_stock_with_correct_token_returns_200(self) -> None:
        payload = {
            "stock_code": "01592",
            "stock_name": "Mock Stock",
            "issue_id": "12345",
            "holdings_latest_date": "2026-06-26",
            "changes_trading_date": "2026-06-26",
            "total_in_ccass_percent": "50.00%",
            "top_5_percent": "25.00%",
            "top_10_percent": "35.00%",
            "largest_participant": "Mock Participant",
            "holdings": [],
            "changes": [],
            "big_changes": [],
            "concentration": [],
            "fetch_summary": [],
            "data_quality_warnings": [],
        }
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_stock_payload", return_value=payload):
                status_code, response_payload = asgi_get(
                    "/api/stock?stock_code=01592",
                    headers={"Authorization": "Bearer correct-token"},
                )
        self.assertEqual(status_code, 200)
        self.assertEqual(response_payload, payload)


if __name__ == "__main__":
    unittest.main()
