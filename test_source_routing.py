import asyncio
import inspect
import unittest
from unittest.mock import patch

import api


class SharedSourceRoutingTest(unittest.TestCase):
    def test_compact_payload_defaults_to_auto_router(self):
        defaults = {
            name: parameter.default
            for name, parameter in inspect.signature(api.build_stock_payload).parameters.items()
        }
        self.assertEqual(defaults["source_preference"], "auto")

    def test_mcp_uses_same_auto_router_as_rest(self):
        captured = {}

        async def immediate(_tool, work, **_kwargs):
            return work()

        def fake_build(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "metadata": {"source": "hybrid_local_db_webb"}}

        with patch.object(api, "run_ccass_tool_with_budget", side_effect=immediate), patch.object(
            api, "build_stock_payload", side_effect=fake_build
        ):
            payload = asyncio.run(api.get_ccass_stock_data("02048"))

        self.assertTrue(payload["ok"])
        self.assertEqual(captured["source_preference"], "auto")

    def test_cache_key_separates_source_modes(self):
        with patch.object(api, "cache_get", return_value={"cached": True}) as cache_get:
            self.assertEqual(
                api.build_base_payload("03301", timeout=10, source_preference="mirror"),
                {"cached": True},
            )
        key = cache_get.call_args.args[0]
        self.assertIn(":mirror:", key)
        self.assertIn("03301", key)


if __name__ == "__main__":
    unittest.main()
