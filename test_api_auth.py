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


def auth_headers(token: str = "correct-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def fake_base_payload(row_count: int = 100, warning: str | None = None) -> dict:
    warnings = [warning] if warning else []
    return {
        "issue_id": "12345",
        "exported": {
            "metadata": {
                "stock_code": "01592",
                "stock_name": "Mock Stock",
                "issue_id": "12345",
                "holdings_data_date": "2026-06-26",
                "changes_trading_date": "2026-06-26",
                "total_in_ccass_pct": "50.00%",
                "top5_cumulative_pct": "25.00%",
                "top10_cumulative_pct": "35.00%",
                "largest_participant": "Participant 100",
            },
            "holdings": [
                {
                    "Participant": f"Participant {index}",
                    "Holding": f"{index:,}",
                    "Stake %": f"{index / 10:.2f}%",
                    "Cumulative %": f"{index / 5:.2f}%",
                }
                for index in range(1, row_count + 1)
            ],
            "changes": [
                {
                    "Participant": f"Changer {index}",
                    "Change": f"{'-' if index % 2 else '+'}{index * 10:,}",
                    "Change %": f"{'-' if index % 2 else '+'}{index / 10:.2f}%",
                    "Holding after": f"{index * 100:,}",
                    "Stake after": f"{index / 10:.2f}%",
                }
                for index in range(1, row_count + 1)
            ],
            "bigchanges": [
                {"Date": f"2026-06-{(index % 28) + 1:02d}", "Participant": f"Big {index}", "Change %": f"{index}.00%"}
                for index in range(1, row_count + 1)
            ],
            "concentration": [
                {
                    "Date": f"2026-06-{(index % 28) + 1:02d}",
                    "Top 5 %": f"{index}.00%",
                    "Top 10 %": f"{index + 5}.00%",
                    "Stake in CCASS %": f"{index + 10}.00%",
                }
                for index in range(1, row_count + 1)
            ],
            "fetch_summary": [
                {
                    "Section": "Holdings",
                    "Status": "success",
                    "Tables found": 1,
                    "Selected table index": 1,
                    "Latest date / data date": "2026-06-26",
                    "Error": "",
                },
                {
                    "Section": "Changes",
                    "Status": "failed" if warning else "success",
                    "Tables found": 0 if warning else 1,
                    "Selected table index": "",
                    "Latest date / data date": "",
                    "Error": warning or "",
                },
            ],
            "analysis_warnings": warnings,
        },
    }


class ApiAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        api._stock_cache.clear()

    def test_health_without_token_returns_200(self) -> None:
        status_code, _ = asgi_get("/health")
        self.assertEqual(status_code, 200)

    def test_openapi_without_token_returns_200_and_declares_bearer(self) -> None:
        status_code, schema = asgi_get("/openapi.json")
        self.assertEqual(status_code, 200)
        bearer = schema["components"]["securitySchemes"]["HTTPBearer"]
        self.assertEqual(bearer["type"], "http")
        self.assertEqual(bearer["scheme"], "bearer")
        self.assertNotIn("/api/stock/full", schema["paths"])

    def test_stock_without_token_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592")
        self.assertEqual(status_code, 401)
        self.assertEqual(payload, {"detail": "Unauthorized"})

    def test_stock_with_wrong_token_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592", headers=auth_headers("wrong-token"))
        self.assertEqual(status_code, 401)
        self.assertEqual(payload, {"detail": "Unauthorized"})

    def test_stock_with_malformed_authorization_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592", headers={"Authorization": "Token correct-token"})
        self.assertEqual(status_code, 401)
        self.assertEqual(payload, {"detail": "Unauthorized"})

    def test_stock_with_correct_token_returns_200(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?stock_code=01592", headers=auth_headers())
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["stock_code"], "01592")

    def test_timeout_maximum_cannot_exceed_35(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592&timeout=36", headers=auth_headers())
        self.assertEqual(status_code, 422)
        self.assertIn("detail", payload)

    def test_limit_over_range_returns_422(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592&holdings_limit=51", headers=auth_headers())
        self.assertEqual(status_code, 422)
        self.assertIn("detail", payload)

    def test_compact_response_contains_core_fields(self) -> None:
        required = {
            "stock_code",
            "stock_name",
            "issue_id",
            "holdings_latest_date",
            "changes_trading_date",
            "total_in_ccass_percent",
            "top_5_percent",
            "top_10_percent",
            "largest_participant",
            "holdings",
            "changes",
            "big_changes",
            "concentration",
            "fetch_summary",
            "data_quality_warnings",
            "holdings_total_count",
            "holdings_returned_count",
            "changes_total_count",
            "changes_returned_count",
            "big_changes_total_count",
            "big_changes_returned_count",
            "concentration_total_count",
            "concentration_returned_count",
            "truncated",
        }
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?stock_code=01592", headers=auth_headers())
        self.assertEqual(status_code, 200)
        self.assertTrue(required.issubset(payload))
        self.assertEqual(payload["holdings_returned_count"], 20)
        self.assertEqual(payload["changes_returned_count"], 30)
        self.assertEqual(payload["big_changes_returned_count"], 30)
        self.assertEqual(payload["concentration_returned_count"], 30)
        self.assertTrue(payload["truncated"])

    def test_json_serialized_length_is_less_than_90000_characters(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload(row_count=400)):
                status_code, payload = asgi_get("/api/stock?stock_code=01592", headers=auth_headers())
        self.assertEqual(status_code, 200)
        self.assertLess(len(json.dumps(payload)), 90000)

    def test_partial_section_failure_still_returns_200_with_warning(self) -> None:
        warning = "Timeout budget exhausted before this section was fetched."
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload(warning=warning)):
                status_code, payload = asgi_get("/api/stock?stock_code=01592", headers=auth_headers())
        self.assertEqual(status_code, 200)
        self.assertTrue(any(warning in item for item in payload["data_quality_warnings"]))

    def test_cache_second_request_uses_cached_base_payload(self) -> None:
        lookup = api.IssueLookup(stock_code="01592", issue_id="12345", method="mock", status="success")
        exported = fake_base_payload(row_count=5)["exported"]
        with patch.object(api, "resolve_lookup", return_value=lookup) as resolve:
            with patch.object(api, "fetch_compact_results", return_value={}) as fetch:
                with patch.object(api, "parse_results", return_value=object()):
                    with patch.object(api, "parsed_to_json_ready", return_value=exported):
                        first = api.build_base_payload("01592", timeout=30)
                        second = api.build_base_payload("01592", timeout=30)
        self.assertEqual(first, second)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
