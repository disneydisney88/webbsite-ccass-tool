import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

import api
from utils.longbridge import (
    LongbridgeData,
    LongbridgeMCPClient,
    active_token_payload,
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
        ), patch("utils.longbridge.requests.post", side_effect=responses):
            result = start_device_authorization(path=Path(directory) / "fixture.db")
        self.assertEqual(result["status"], "authorization_pending")
        self.assertEqual(result["user_code"], "ABCD-EFGH")
        self.assertNotIn("device_code", result)
        self.assertNotIn("private-device-code", json.dumps(result))

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
