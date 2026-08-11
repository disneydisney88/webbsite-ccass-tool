import unittest
from unittest.mock import patch

from fastapi import HTTPException

import api


def base_payload() -> dict:
    return {
        "issue_id": "18546",
        "exported": {
            "metadata": {
                "stock_code": "03301",
                "stock_name": "Ronshine China Holdings Limited",
                "issue_id": "18546",
                "holdings_data_date": "2026-08-12",
                "changes_trading_date": "2026-08-10",
                "big_changes_latest_date": "2026-08-13",
                "concentration_latest_date": "2026-08-12",
            },
            "holdings": [],
            "changes": [],
            "bigchanges": [
                {"Date": "2026-08-11", "Participant": "A", "Change %": 1.0},
                {"Date": "2026-08-12", "Participant": "B", "Change %": -1.0},
                {"Date": "2026-08-13", "Participant": "C", "Change %": 2.0},
            ],
            "concentration": [],
            "price_history": [],
            "fetch_summary": [],
            "analysis_warnings": [],
        },
    }


class StockDateRangeTest(unittest.TestCase):
    def test_trade_input_fetches_expected_settlement_dates(self):
        queried_rows = [
            {"Participant": "Broker A", "Change": 1000, "implied_trade_date": "2026-08-07"},
            {"Participant": "Broker B", "Change": -500, "implied_trade_date": "2026-08-10"},
        ]
        with patch.object(api, "build_base_payload", return_value=base_payload()), patch.object(
            api,
            "fetch_changes_for_date_plan",
            return_value=(queried_rows, [], []),
        ) as fetch_range:
            payload = api.build_stock_payload(
                "03301",
                source_preference="auto",
                changes_from="2026-08-07",
                changes_to="2026-08-10",
                big_changes_from="2026-08-07",
                big_changes_to="2026-08-10",
                date_input_basis="trade",
                changes_limit=100,
                big_changes_limit=100,
            )

        plan = fetch_range.call_args.kwargs["plan"]
        self.assertEqual(
            [row["settlement_date"] for row in plan],
            ["2026-08-11", "2026-08-12"],
        )
        metadata = payload["metadata"]
        self.assertEqual(metadata["changes_queried_settlement_from"], "2026-08-11")
        self.assertEqual(metadata["changes_queried_settlement_to"], "2026-08-12")
        self.assertEqual(metadata["big_changes_queried_settlement_from"], "2026-08-11")
        self.assertEqual(metadata["big_changes_queried_settlement_to"], "2026-08-12")
        self.assertEqual(len(payload["changes"]), 2)
        self.assertEqual(
            {row["ccass_date"] for row in payload["big_changes"]},
            {"2026-08-11", "2026-08-12"},
        )

    def test_invalid_weekend_range_returns_structured_422(self):
        with patch.object(api, "build_base_payload", return_value=base_payload()):
            with self.assertRaises(HTTPException) as raised:
                api.build_stock_payload(
                    "03301",
                    changes_from="2026-08-08",
                    changes_to="2026-08-10",
                    source_preference="local_db",
                )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["error_code"], "INVALID_DATE_RANGE")


if __name__ == "__main__":
    unittest.main()
