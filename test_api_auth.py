import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
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
        text = body.decode("utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"raw": text}
        return status, payload

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
        with patch.object(api, "probe_upstreams") as probe:
            status_code, payload = asgi_get("/health")
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], api.API_SERVICE)
        self.assertEqual(payload["version"], api.API_VERSION)
        self.assertGreaterEqual(payload["uptime_seconds"], 0)
        self.assertNotIn("upstreams", payload)
        probe.assert_not_called()

    def test_health_upstream_probe_is_opt_in(self) -> None:
        with patch.object(api, "probe_upstreams", return_value={"probes": []}) as probe:
            status_code, payload = asgi_get("/health?upstreams=true")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["upstreams"], {"probes": []})
        probe.assert_called_once_with(timeout=5)

    def test_openapi_without_token_returns_200_and_declares_bearer(self) -> None:
        status_code, schema = asgi_get("/openapi.json")
        self.assertEqual(status_code, 200)
        bearer = schema["components"]["securitySchemes"]["HTTPBearer"]
        self.assertEqual(bearer["type"], "http")
        self.assertEqual(bearer["scheme"], "bearer")
        self.assertNotIn("/api/stock/full", schema["paths"])

    def test_robots_txt_allows_api_fetching(self) -> None:
        status_code, payload = asgi_get("/robots.txt")
        self.assertEqual(status_code, 200)
        self.assertIn("Allow: /api/", payload["raw"])
        self.assertIn("Allow: /mcp", payload["raw"])
        self.assertNotIn("Disallow: /", payload["raw"])

    def test_mcp_route_is_mounted(self) -> None:
        mounted_paths = {getattr(route, "path", "") for route in api.app.routes}
        self.assertIn("/mcp", mounted_paths)

    def test_mcp_tool_is_registered_with_limits(self) -> None:
        async def list_tool_names() -> tuple[list[str], dict]:
            tools = await api.mcp_server.list_tools()
            tool = next(item for item in tools if item.name == "get_ccass_stock_data")
            return [item.name for item in tools], tool.inputSchema

        names, schema = asyncio.run(list_tool_names())
        self.assertIn("get_ccass_stock_data", names)
        self.assertIn("get_webbsite_price_history", names)
        self.assertIn("get_hkex_announcements", names)
        self.assertIn("fetch_announcement_pdf", names)
        self.assertEqual(schema["properties"]["code"]["pattern"], "^[0-9]{5}$")
        self.assertEqual(schema["properties"]["holdings_limit"]["maximum"], 100)
        self.assertEqual(schema["properties"]["concentration_limit"]["maximum"], 100)

    def test_price_history_payload_is_compact(self) -> None:
        base = fake_base_payload(row_count=5)
        base["exported"]["metadata"].update(
            {
                "price_history_latest_date": "2026-06-30",
                "latest_price": "0.500",
                "latest_price_volume": "1,000,000",
                "latest_price_turnover": "500,000",
                "latest_price_vwap": "0.500",
                "issued_securities": "10,000,000",
            }
        )
        yahoo_table = api.pd.DataFrame(
            [
                {
                    "Date": f"2026-06-{index:02d}",
                    "Close": index / 10 + 0.000000011920929,
                    "Volume": index * 1000,
                    "Turnover": index * index * 100 + 0.000000011920929,
                    "VWAP": None,
                    "price_source": "yahoo",
                    "turnover_est": index * index * 100 + 0.000000011920929,
                }
                for index in range(1, 6)
            ]
        )
        yahoo_result = SimpleNamespace(
            ok=True,
            ticker="1592.HK",
            table=yahoo_table,
            warning="Turnover is estimated as volume \u00d7 close, not actual turnover",
            error_type="",
            error_message="",
        )
        capital_payload = {
            "capital_summary": {
                "latest_share_capital": {
                    "shares_approx": 10000000,
                    "as_of": "2026-06-05",
                }
            },
            "data_quality_warnings": [],
        }
        with patch.object(api, "build_base_payload", return_value=base):
            with patch.object(api, "load_stock_meta", return_value={"name": "Mock Stock", "issued_shares": "10,000,000", "issued_shares_as_of": "2026-06-05"}):
                with patch.object(api, "build_capital_payload", return_value=capital_payload):
                    with patch.object(api, "fetch_yahoo_price_history", return_value=yahoo_result):
                        payload = api.build_price_history_payload("01592", limit=2)
        self.assertEqual(payload["metadata"]["code"], "01592")
        self.assertIn("source", payload)
        self.assertIn("data_as_of", payload)
        self.assertEqual(payload["source"], "yahoo")
        self.assertEqual(payload["data_as_of"], "2026-06-05")
        self.assertEqual(payload["price_summary"]["latest_date"], "2026-06-05")
        self.assertEqual(payload["price_summary"]["latest_market_cap"], 5000000.0)
        self.assertEqual(payload["price_summary"]["latest_turnover_to_market_cap_pct"], 0.05)
        self.assertEqual(payload["price_summary"]["issued_securities_source"], "10jqka F10")
        self.assertEqual(payload["price_summary"]["price_history_returned_count"], 2)
        self.assertIn("Turnover / Market Cap %", payload["price_history"][0])
        self.assertEqual(payload["price_history"][0]["Close"], 0.5)
        self.assertEqual(payload["price_history"][0]["turnover_est"], 2500.0)
        self.assertEqual(payload["price_history"][0]["vwap_est"], 0.5)
        self.assertIn("VWAP is estimated from estimated turnover", payload["data_quality_warnings"])
        self.assertTrue(payload["price_summary"]["truncated"])

    def test_screen_stocks_uses_snapshot_largest_participant_fallback(self) -> None:
        base = fake_base_payload(row_count=1)
        base["exported"]["holdings"] = [{"Participant": "Total in CCASS", "Stake %": "50.00%"}]
        stock_payload = {
            "metadata": {"name": "Mock Stock", "holdings_date": "2026-07-30"},
            "holdings": base["exported"]["holdings"],
            "holdings_summary": {"total_in_ccass_pct": "50.00%"},
            "concentration": {},
            "big_changes": [],
            "data_quality_warnings": [],
        }
        snapshot = api.pd.DataFrame(
            [
                {
                    "participant_id": "PARTICIPANT ID: C00019",
                    "participant_name": "Name of CCASS Participant (* for Consenting Investor Participants ): KINGSTON SECURITIES LTD",
                    "shares": 1000000,
                    "pct_of_issued": 12.3456,
                    "source": "sdw",
                    "fetched_at": "2026-07-30T21:30:00+08:00",
                }
            ]
        )
        with (
            patch.object(api, "build_stock_payload", return_value=stock_payload),
            patch.object(api, "latest_snapshot_date", return_value="2026-07-30"),
            patch.object(api, "load_snapshot", return_value=snapshot),
        ):
            payload = api.build_screen_payload(["03321"], timeout=1)
        largest = payload["results"][0]["largest_participant"]
        self.assertEqual(payload["source"], "local_db")
        self.assertEqual(payload["data_as_of"], "2026-07-30")
        self.assertEqual(largest["name"], "KINGSTON SECURITIES LTD")
        self.assertEqual(largest["participant_id"], "C00019")
        self.assertEqual(largest["category"], "bank")
        self.assertEqual(largest["stake_pct"], 12.3456)

    def test_hkex_announcements_payload_uses_fetcher(self) -> None:
        table = api.pd.DataFrame(
            [
                {
                    "Publish time": "2026-06-30 12:00",
                    "Stock code": "03321",
                    "Stock name": "Mock",
                    "Category": "公告及通告 - 股本重組",
                    "Title": "建議股份合併及更改公司名稱",
                    "URL": "https://example.com/mock.pdf",
                    "News ID": "1",
                }
            ]
        )
        result = SimpleNamespace(
            stock_code="03321",
            stock_name="Mock",
            hkex_stock_id="123",
            period_years=1,
            from_date="2025-07-02",
            to_date="2026-07-02",
            url="https://example.com/search",
            ok=True,
            error="",
            total_count=1,
            table=table,
        )
        with patch.object(api, "fetch_announcements", return_value=result):
            payload = api.build_hkex_announcements_payload("03321", period_years=1, limit=100)
        self.assertEqual(payload["metadata"]["code"], "03321")
        self.assertEqual(payload["source"], "HKEXnews")
        self.assertEqual(payload["data_as_of"], "2026-07-02")
        self.assertEqual(payload["announcements_summary"]["returned_count"], 1)
        self.assertIn("share_consolidation", payload["announcements"][0]["Event tags"])
        self.assertIn("change_company_name", payload["announcements_summary"]["event_tags_found"])

    def test_stock_without_token_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?code=01592")
        self.assertEqual(status_code, 401)
        self.assertEqual(payload["detail"]["error_code"], "AUTH_FAILED")
        self.assertFalse(payload["detail"]["retry_recommended"])


class AnnouncementPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        api._stock_cache.clear()

    def test_pdf_url_validation_returns_structured_error(self) -> None:
        from utils.hkex_announcement_pdf import fetch_announcement_pdf

        for url in (
            "http://www1.hkexnews.hk/2026/0730/test.pdf",
            "https://evil.example/2026/0730/test.pdf",
            "https://user:password@www1.hkexnews.hk/2026/0730/test.pdf",
        ):
            payload = fetch_announcement_pdf(url)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "URL_NOT_ALLOWED")

    def test_pdf_route_uses_structured_helper_response(self) -> None:
        expected = {
            "ok": False,
            "error_code": "URL_NOT_ALLOWED",
            "message": "Only https hkexnews.hk URLs without credentials are accepted.",
        }
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(api, "fetch_hkex_announcement_pdf", return_value=expected) as fetch:
                status_code, payload = asgi_get(
                    "/announcement/pdf?url=https%3A%2F%2Fwww1.hkexnews.hk%2Ftest.pdf&max_chars=100"
                )
        self.assertEqual(status_code, 200)
        self.assertEqual(payload, expected)
        fetch.assert_called_once_with(
            "https://www1.hkexnews.hk/test.pdf",
            max_chars=100,
            timeout=20.0,
        )

    def test_pdf_extracts_chinese_truncates_and_reuses_full_cache(self) -> None:
        import fitz

        from utils.hkex_announcement_pdf import fetch_announcement_pdf

        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "\u914d\u552e \u6388\u6b0a \u80a1\u4efd", fontname="china-s")
        pdf_bytes = document.tobytes()
        document.close()
        response = SimpleNamespace(
            status_code=200,
            reason="OK",
            url="https://www1.hkexnews.hk/2026/0730/test.pdf",
            content=pdf_bytes,
        )
        with tempfile.TemporaryDirectory() as cache_dir:
            with patch.dict(os.environ, {"HKEX_PDF_CACHE_DIR": cache_dir}):
                with patch("utils.hkex_announcement_pdf.requests.get", return_value=response) as get:
                    first = fetch_announcement_pdf(response.url, max_chars=4)
                    second = fetch_announcement_pdf(response.url, max_chars=100)

        self.assertTrue(first["ok"])
        self.assertEqual(first["data_as_of"], "2026-07-30")
        self.assertTrue(first["truncated"])
        self.assertEqual(first["chars_returned"], 4)
        self.assertIn("\u914d\u552e", first["text"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["cached"])
        self.assertFalse(second["truncated"])
        self.assertIn("\u914d\u552e", second["text"])
        self.assertIn("\u6388\u6b0a", second["text"])
        self.assertIn("\u80a1\u4efd", second["text"])
        get.assert_called_once()

    def test_non_pdf_upstream_returns_body_head_without_raising(self) -> None:
        from utils.hkex_announcement_pdf import fetch_announcement_pdf

        response = SimpleNamespace(
            status_code=200,
            reason="OK",
            url="https://www1.hkexnews.hk/2026/0730/test.pdf",
            content=b"<html><body>setCookie(); location.reload();</body></html>",
        )
        with patch("utils.hkex_announcement_pdf.requests.get", return_value=response):
            payload = fetch_announcement_pdf(response.url)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "UPSTREAM_NOT_PDF")
        self.assertIn("setCookie", payload["body_head"])

    def test_stock_with_wrong_token_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?code=01592&key=wrong-token")
        self.assertEqual(status_code, 401)
        self.assertEqual(payload["detail"]["error_code"], "AUTH_FAILED")
        self.assertFalse(payload["detail"]["retry_recommended"])

    def test_stock_with_malformed_authorization_returns_401(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592", headers={"Authorization": "Token correct-token"})
        self.assertEqual(status_code, 401)
        self.assertEqual(payload["detail"]["error_code"], "AUTH_FAILED")
        self.assertFalse(payload["detail"]["retry_recommended"])

    def test_stock_with_correct_token_returns_200(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?code=01592&key=correct-token")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["metadata"]["code"], "01592")

    def test_stock_with_api_token_query_alias_returns_200(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?code=01592&api_token=correct-token")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["metadata"]["code"], "01592")

    def test_stock_with_bearer_token_still_returns_200(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?stock_code=01592", headers=auth_headers())
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["metadata"]["code"], "01592")

    def test_mask_secret_does_not_expose_full_token(self) -> None:
        masked = api.mask_secret("33243e6e3580e484c8de165269ad3d7a")
        self.assertEqual(masked, "3324...3d7a (len=32)")
        self.assertNotIn("3e6e3580e484c8de165269ad", masked)

    def test_stock_without_configured_token_is_public_readonly(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?code=01592")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["metadata"]["code"], "01592")

    def test_timeout_maximum_cannot_exceed_35(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592&timeout=36", headers=auth_headers())
        self.assertEqual(status_code, 422)
        self.assertIn("detail", payload)

    def test_limit_over_range_returns_422(self) -> None:
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            status_code, payload = asgi_get("/api/stock?stock_code=01592&holdings_limit=101", headers=auth_headers())
        self.assertEqual(status_code, 422)
        self.assertIn("detail", payload)

    def test_compact_response_contains_core_fields(self) -> None:
        required = {
            "metadata",
            "holdings_summary",
            "holdings",
            "changes",
            "big_changes",
            "price_history",
            "concentration",
            "fetch_summary",
            "data_quality_warnings",
        }
        with patch.dict(os.environ, {"API_TOKEN": "correct-token"}, clear=True):
            with patch.object(api, "build_base_payload", return_value=fake_base_payload()):
                status_code, payload = asgi_get("/api/stock?code=01592&key=correct-token")
        self.assertEqual(status_code, 200)
        self.assertTrue(required.issubset(payload))
        self.assertIn("source", payload)
        self.assertIn("data_as_of", payload)
        self.assertEqual(payload["metadata"]["name"], "Mock Stock")
        self.assertEqual(payload["metadata"]["holdings_date"], "2026-06-26")
        self.assertEqual(payload["metadata"]["changes_date"], "2026-06-26")
        self.assertEqual(payload["holdings_summary"]["holdings_returned_count"], 15)
        self.assertEqual(payload["holdings_summary"]["changes_returned_count"], 20)
        self.assertEqual(payload["holdings_summary"]["big_changes_returned_count"], 10)
        self.assertEqual(payload["holdings_summary"]["concentration_returned_count"], 15)
        self.assertEqual(payload["concentration"]["top5_pct"], "25.00%")
        self.assertEqual(payload["concentration"]["top10_pct"], "35.00%")
        self.assertTrue(payload["holdings_summary"]["truncated"])

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
        bundle = SimpleNamespace(
            lookup=lookup,
            results={},
            metadata={"source": "mirror", "mirror_status": "mock", "history_depth_days": 0},
            warnings=[],
        )
        exported = fake_base_payload(row_count=5)["exported"]
        with patch.object(
            api,
            "probe_url",
            return_value={
                "error_type": "",
                "status_code": 200,
                "reason": "OK",
                "final_url": "https://webb-database.com/ccass/choldings.asp?i=12345",
                "url": "https://webb-database.com/ccass/choldings.asp?i=12345",
                "body_head": "",
                "attempts": 1,
                "error_message": "",
            },
        ):
            with patch.object(api, "fetch_source_bundle_for_stock", return_value=bundle) as fetch_source:
                with patch.object(api, "parse_results", return_value=object()):
                    with patch.object(api, "parsed_to_json_ready", return_value=exported):
                        first = api.build_base_payload("01592", timeout=30)
                        second = api.build_base_payload("01592", timeout=30)
        self.assertEqual(first, second)
        self.assertEqual(fetch_source.call_count, 1)


if __name__ == "__main__":
    unittest.main()
