import unittest
from unittest.mock import patch

import api


class PersistentStockCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        api._stock_cache.clear()

    def test_light_route_serves_turso_payload_without_upstream_fetch(self) -> None:
        cached = {
            "ok": True,
            "metadata": {"code": "01825", "served_from_cache": False, "cache_status": "memory"},
            "source": "hybrid_light",
        }
        with patch.object(api, "get_api_stock_cache", return_value=cached), patch.object(
            api, "build_stock_payload", side_effect=AssertionError("cache miss must not fetch")
        ):
            payload = api.get_stock(
                code="01825", light=True, bypass_cache=False, format="json",
                timeout=30, holdings_limit=15, changes_limit=20,
                big_changes_limit=10, concentration_limit=15, include_price_history=False,
            )
        self.assertTrue(payload["metadata"]["served_from_cache"])
        self.assertEqual(payload["metadata"]["cache_status"], "persistent_turso")

    def test_light_route_persists_a_successful_fresh_payload(self) -> None:
        fresh = {"ok": True, "errors": [], "metadata": {"code": "01825"}}
        with patch.object(api, "get_api_stock_cache", return_value=None), patch.object(
            api, "build_stock_payload", return_value=fresh
        ), patch.object(api, "put_api_stock_cache") as put:
            payload = api.get_stock(
                code="01825", light=True, bypass_cache=False, format="json",
                timeout=30, holdings_limit=15, changes_limit=20,
                big_changes_limit=10, concentration_limit=15, include_price_history=False,
            )
        self.assertIs(payload, fresh)
        put.assert_called_once()
        self.assertEqual(put.call_args.args[0], "stock:01825:light:v1")


if __name__ == "__main__":
    unittest.main()
