import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

import api
from utils.longbridge import (
    LongbridgeAuthError,
    LongbridgeData,
    LongbridgeMCPClient,
    active_token_payload,
    call_longbridge_read_only_tool,
    list_longbridge_tools,
    load_token_payload,
    normalize_holdings,
    normalize_changes,
    poll_device_authorization,
    save_token_payload,
    start_device_authorization,
    to_longbridge_symbol,
)
from utils.snapshot_db import load_longbridge_holdings, upsert_longbridge_holdings


class LongbridgeSymbolTest(unittest.TestCase):
    def test_hong_kong_symbol_conversion(self):
        self.assertEqual(to_longbridge_symbol("00001"), "1.HK")
        self.assertEqual(to_longbridge_symbol("06182"), "6182.HK")
        self.assertEqual(to_longbridge_symbol("08489"), "8489.HK")
        self.assertEqual(to_longbridge_symbol("09978"), "9978.HK")

    def test_tools_list_uses_stored_token_and_filters_to_read_only_schema(self):
        tool_rows = [
            {"name": "broker_holding_detail", "inputSchema": {"type": "object"}},
            {"name": "static_info", "inputSchema": {"type": "object"}},
            {"name": "not_read_only", "inputSchema": {"type": "object"}},
        ]
        with patch(
            "utils.longbridge.active_token_payload",
            return_value={"access_token": "fixture-token"},
        ), patch.object(LongbridgeMCPClient, "list_tools", return_value=tool_rows):
            result = list_longbridge_tools()

        self.assertEqual(result["endpoint"], "https://mcp.longbridge.com")
        self.assertEqual(result["method"], "tools/list")
        self.assertEqual(result["tool_count"], 3)
        self.assertEqual(
            [tool["name"] for tool in result["read_only_tools"]],
            ["broker_holding_detail", "static_info"],
        )
        self.assertNotIn("not_read_only", str(result["read_only_tools"]))

    def test_raw_tool_call_rejects_non_whitelisted_name(self):
        with self.assertRaises(PermissionError):
            call_longbridge_read_only_tool("not_read_only", {})


class LongbridgeSecurityTest(unittest.TestCase):
    @staticmethod
    def response(status: int, payload: dict) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        response.url = "https://openapi.longbridge.com/oauth2/test"
        return response

    def test_main_client_rejects_non_whitelisted_tool(self):
        client = LongbridgeMCPClient(endpoint="https://example.test", token="example")
        with self.assertRaises(PermissionError):
            client.call_tool("write_operation", {})

    def test_token_is_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LONGBRIDGE_TOKEN_KEY": "fixture-encryption-key"}, clear=False
        ):
            path = Path(directory) / "fixture.db"
            save_token_payload({"access_token": "secret-token-value"}, path=path)
            self.assertNotIn(b"secret-token-value", path.read_bytes())
            self.assertEqual(load_token_payload(path=path)["access_token"], "secret-token-value")

    def test_device_start_registers_client_without_exposing_device_code(self):
        responses = [
            self.response(
                201,
                {
                    "client_id": "fixture-client",
                    "registration_access_token": "registration-secret",
                    "registration_client_uri": "https://openapi.longbridge.com/oauth2/register/fixture",
                },
            ),
            self.response(
                200,
                {
                    "device_code": "private-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri_complete": "https://open.longbridge.com/device?code=ABCD-EFGH",
                    "expires_in": 300,
                    "interval": 5,
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LONGBRIDGE_TOKEN_KEY": "fixture-encryption-key"}, clear=False
        ), patch("utils.longbridge.requests.post", side_effect=responses) as request:
            result = start_device_authorization(path=Path(directory) / "fixture.db")
        self.assertEqual(result["status"], "authorization_pending")
        self.assertEqual(result["user_code"], "ABCD-EFGH")
        self.assertNotIn("device_code", result)
        self.assertNotIn("private-device-code", json.dumps(result))
        registration_call = request.call_args_list[0]
        self.assertEqual(registration_call.kwargs["timeout"], 10.0)
        self.assertEqual(registration_call.kwargs["json"]["token_endpoint_auth_method"], "none")
        self.assertIn("urn:ietf:params:oauth:grant-type:device_code", registration_call.kwargs["json"]["grant_types"])
        self.assertEqual(request.call_args_list[1].kwargs["timeout"], 10.0)

    def test_device_start_reuses_encrypted_registration(self):
        responses = [
            self.response(201, {"client_id": "fixture-client", "registration_access_token": "secret"}),
            self.response(200, {"device_code": "first-code", "user_code": "FIRST", "verification_uri": "https://example.test/device", "expires_in": 300, "interval": 5}),
            self.response(200, {"device_code": "second-code", "user_code": "SECOND", "verification_uri": "https://example.test/device", "expires_in": 300, "interval": 5}),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LONGBRIDGE_TOKEN_KEY": "fixture-encryption-key"}, clear=False
        ), patch("utils.longbridge.requests.post", side_effect=responses) as request:
            path = Path(directory) / "fixture.db"
            first = start_device_authorization(path=path)
            second = start_device_authorization(path=path)

        self.assertEqual(first["user_code"], "FIRST")
        self.assertEqual(second["user_code"], "SECOND")
        self.assertEqual(request.call_count, 3)
        self.assertTrue(request.call_args_list[0].args[0].endswith("/oauth2/register"))
        self.assertTrue(request.call_args_list[1].args[0].endswith("/oauth2/device/authorize"))
        self.assertTrue(request.call_args_list[2].args[0].endswith("/oauth2/device/authorize"))

    def test_device_poll_pending_then_success_persists_refreshable_token(self):
        responses = [
            self.response(201, {"client_id": "fixture-client", "registration_access_token": "secret", "registration_client_uri": "https://example.test/reg"}),
            self.response(200, {"device_code": "private-code", "user_code": "ABCD", "verification_uri": "https://example.test/device", "expires_in": 300, "interval": 1}),
            self.response(400, {"error": "authorization_pending"}),
            self.response(200, {"access_token": "access-one", "refresh_token": "refresh-one", "expires_in": 3600}),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LONGBRIDGE_TOKEN_KEY": "fixture-encryption-key"}, clear=False
        ), patch("utils.longbridge.requests.post", side_effect=responses):
            path = Path(directory) / "fixture.db"
            started = start_device_authorization(path=path)
            pending = poll_device_authorization(started["session_id"], path=path)
            self.assertEqual(pending["status"], "authorization_pending")
            with patch("utils.longbridge.datetime") as mocked_datetime:
                mocked_datetime.now.return_value = datetime.now(timezone.utc) + timedelta(seconds=2)
                mocked_datetime.fromisoformat.side_effect = datetime.fromisoformat
                mocked_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
                authenticated = poll_device_authorization(started["session_id"], path=path)
            stored = load_token_payload(path=path)
        self.assertEqual(authenticated["status"], "authenticated")
        self.assertEqual(stored["access_token"], "access-one")
        self.assertEqual(stored["client_id"], "fixture-client")
        self.assertEqual(stored["auth_method"], "oauth_device_flow")

    def test_expired_access_token_is_refreshed_before_use(self):
        refreshed_response = self.response(
            200,
            {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200},
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LONGBRIDGE_TOKEN_KEY": "fixture-encryption-key"}, clear=False
        ):
            path = Path(directory) / "fixture.db"
            save_token_payload(
                {
                    "client_id": "fixture-client",
                    "access_token": "old-access",
                    "refresh_token": "old-refresh",
                    "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
                },
                path=path,
            )
            with patch("utils.longbridge.requests.post", return_value=refreshed_response) as request:
                payload = active_token_payload(path=path)
        self.assertEqual(payload["access_token"], "new-access")
        self.assertEqual(payload["refresh_token"], "new-refresh")
        self.assertEqual(request.call_args.kwargs["data"]["grant_type"], "refresh_token")

    def test_device_admin_routes_and_health_lifecycle_fields_are_documented(self):
        schema = api.app.openapi()
        self.assertIn("/admin/longbridge/device_start", schema["paths"])
        self.assertIn("/admin/longbridge/device_poll", schema["paths"])
        health_fields = schema["components"]["schemas"]["HealthResponse"]["properties"]
        self.assertIn("longbridge_token_expires_in_seconds", health_fields)
        self.assertIn("longbridge_refresh_available", health_fields)
        self.assertIn("longbridge_auth_method", health_fields)
        self.assertIn("api_token_configured", health_fields)
        self.assertIn("api_token_length", health_fields)
        self.assertIn("longbridge_token_key_configured", health_fields)
        self.assertIn("render_service_id", health_fields)

    def test_same_api_token_authenticates_mcp_and_device_start(self):
        token = "fixture-shared-api-token"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "auth-test", "version": "1"},
            },
        }
        with patch.dict(os.environ, {"API_TOKEN": token}, clear=False), patch(
            "api.start_device_authorization",
            return_value={"session_id": "fixture-session", "status": "authorization_pending"},
        ) as start_mock, TestClient(api.app, base_url="http://localhost:8000") as client:
            admin_response = client.post("/admin/longbridge/device_start", headers=headers)
            mcp_response = client.post(
                "/mcp/",
                headers={**headers, "Content-Type": "application/json"},
                json=initialize,
            )
            admin_query_response = client.post(f"/admin/longbridge/device_start?key={token}")
            mcp_query_response = client.post(
                f"/mcp/?key={token}",
                headers={"Accept": "application/json, text/event-stream"},
                json=initialize,
            )
            wrong_headers = {"Authorization": "Bearer wrong-token"}
            rejected_admin_response = client.post("/admin/longbridge/device_start", headers=wrong_headers)
            rejected_mcp_response = client.post("/mcp/", headers=wrong_headers)
            start_mock.side_effect = LongbridgeAuthError("fixture upstream failure")
            failed_admin_response = client.post("/admin/longbridge/device_start", headers=headers)

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["session_id"], "fixture-session")
        self.assertEqual(mcp_response.status_code, 200)
        self.assertEqual(admin_query_response.status_code, 200)
        self.assertEqual(mcp_query_response.status_code, 200)
        self.assertEqual(rejected_admin_response.status_code, 401)
        self.assertEqual(rejected_mcp_response.status_code, 401)
        self.assertEqual(failed_admin_response.status_code, 200)
        self.assertFalse(failed_admin_response.json()["ok"])
        self.assertIn("fixture upstream failure", failed_admin_response.json()["error"])


class LongbridgeNormalizationTest(unittest.TestCase):
    def fixture(self):
        return {
            "structuredContent": {
                "data": [
                    {"broker_id": "B01438", "broker_name": "KINGSTON SECURITIES", "holding": "540928000", "holding_percent": "67.616", "date": "2026-09-03"},
                    {"broker_id": "B00001", "broker_name": "BROKER TWO", "holding": "65544000", "holding_percent": "8.193", "date": "2026-09-03"},
                    {"broker_id": "B01955", "broker_name": "FUTU", "holding": "44184250", "holding_percent": "5.523", "date": "2026-09-03"},
                    {"broker_id": "B00002", "broker_name": "IMAGI", "holding": "41000000", "holding_percent": "5.125", "date": "2026-09-03"},
                    {"broker_id": "B00003", "broker_name": "DINPE", "holding": "29180000", "holding_percent": "3.648", "date": "2026-09-03"},
                    {"broker_id": "B00004", "broker_name": "OTHER", "holding": "77723750", "holding_percent": "9.715", "date": "2026-09-03"},
                ]
            }
        }

    def test_live_schema_holding_quantity_is_normalized(self):
        raw = {
            "structuredContent": {
                "items": [
                    {
                        "broker_id": "B01438",
                        "broker_name": "KINGSTON SECURITIES",
                        "holding_quantity": "540928000",
                        "holding_ratio": "67.616",
                        "holding_change": "-90000000",
                        "date": "2026-09-03",
                    }
                ]
            }
        }
        data = normalize_holdings("06182", raw)
        changes = normalize_changes(raw, data.data_date, data.issued_shares)

        self.assertEqual(data.holdings[0]["holding_shares"], 540928000)
        self.assertEqual(data.holdings[0]["stake_pct_of_issued"], 67.616)
        self.assertEqual(changes[0]["change_shares"], -90000000)
        self.assertEqual(changes[0]["holding_after"], 540928000)

    def test_dual_denominator_holdings_and_concentration(self):
        data = normalize_holdings("06182", self.fixture())
        self.assertEqual(data.data_date, "2026-09-03")
        self.assertEqual(data.issued_shares, 800000000)
        self.assertAlmostEqual(data.holdings[0]["stake_pct_of_issued"], 67.616, places=3)
        self.assertAlmostEqual(data.holdings[0]["stake_pct_of_ccass"], 67.738, places=2)
        self.assertNotEqual(
            data.concentration[0]["top5_pct_of_issued"],
            data.concentration[0]["top5_pct_of_ccass"],
        )

    def test_snapshot_is_persisted_by_date_and_participant(self):
        data = normalize_holdings("06182", self.fixture())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.db"
            count = upsert_longbridge_holdings("06182", data.data_date, data.holdings, path=path)
            rows = load_longbridge_holdings("06182", path=path)
        self.assertEqual(count, 6)
        self.assertEqual(rows[0]["ccass_id"], "B01438")

    def test_one_day_change_rows_are_normalized_on_first_snapshot(self):
        raw = {
            "structuredContent": {
                "data": [
                    {
                        "broker_id": "B01438",
                        "broker_name": "KINGSTON SECURITIES",
                        "change_shares": "-90000000",
                        "holding": "540928000",
                        "date": "2026-08-31",
                    }
                ]
            }
        }
        rows = normalize_changes(raw, "2026-08-31", 800000000)
        self.assertEqual(rows[0]["change_shares"], -90000000)
        self.assertEqual(rows[0]["change_pct_of_issued"], -11.25)

    def test_cross_check_does_not_compare_different_dates(self):
        data = normalize_holdings("06182", self.fixture())
        cross_check = api.build_longbridge_cross_check(
            [{"data_date": "2026-09-02", "CCASS ID": "B01438", "Holding": "540,928,000"}],
            data,
        )
        self.assertFalse(cross_check["date_aligned"])
        self.assertIsNone(cross_check["ccass_total_diff_shares"])
        self.assertEqual(cross_check["note"], "dates differ; no numeric comparison performed")

    def test_auth_failure_preserves_existing_payload(self):
        payload = {"holdings": [{"Participant": "Webb"}], "changes": [], "data_quality_warnings": []}
        with patch("api.fetch_longbridge_stock", side_effect=api.LongbridgeAuthError("expired")):
            result = api.apply_longbridge_payload(payload, "06182", "auto", 15, 20, 10, 20)
        self.assertEqual(result["holdings"][0]["Participant"], "Webb")
        self.assertTrue(any("LONGBRIDGE_AUTH_EXPIRED" in item for item in result["data_quality_warnings"]))

    def test_hybrid_keeps_successful_webb_concentration(self):
        data = normalize_holdings("06182", self.fixture())
        payload = {
            "holdings": [],
            "changes": [],
            "concentration": {"records": [{"Date": "2026-09-02", "source": "webb"}]},
            "holdings_summary": {"truncated": False},
            "fetch_summary": [],
            "metadata": {"holdings_date": ""},
            "source_trace": {},
            "data_quality_warnings": [],
            "errors": [],
        }
        with patch("api.fetch_longbridge_stock", return_value=data):
            result = api.apply_longbridge_payload(payload, "06182", "hybrid_light", 15, 20, 10, 20)
        self.assertEqual(result["concentration"]["records"][0]["source"], "webb")
        self.assertEqual(result["source_trace"]["Holdings"], "longbridge")


if __name__ == "__main__":
    unittest.main()
