import unittest
from io import StringIO

import pandas as pd

from utils.fetcher import FetchResult
from utils.exporters import combined_stock_csv
from utils.parser import ParsedCCASS, parse_price_history


class Stock06182BlockTradeRegressionTest(unittest.TestCase):
    def price_table(self) -> pd.DataFrame:
        quiet_dates = pd.bdate_range("2026-08-03", periods=18)
        rows = [
            {
                "Date": "2026-07-15",
                "Close": 0.720,
                "Volume": 8_304_000,
                "Turnover": 5_813_760,
                "VWAP": 0.700,
            }
        ]
        rows.extend(
            {
                "Date": value.strftime("%Y-%m-%d"),
                "Close": 0.600,
                "Volume": 1_000_000,
                "Turnover": 600_000,
                "VWAP": 0.600,
            }
            for value in quiet_dates
        )
        rows.extend(
            [
                {
                    "Date": "2026-09-01",
                    "Close": 0.500,
                    "Volume": 43_752_000,
                    "Turnover": 25_093_400,
                    "VWAP": 0.574,
                },
                {
                    "Date": "2026-09-02",
                    "Close": 0.650,
                    "Volume": 9_184_000,
                    "Turnover": 5_969_600,
                    "VWAP": 0.650,
                },
                {
                    "Date": "2026-09-03",
                    "Close": 0.650,
                    "Volume": 528_536_000,
                    "Turnover": 132_050_520,
                    "VWAP": 0.250,
                },
            ]
        )
        return pd.DataFrame(rows)

    def parsed_prices(self, issued_shares: str = "800000000") -> ParsedCCASS:
        table = self.price_table()
        table["price_source"] = "mirror"
        table["turnover_est"] = table["Close"] * table["Volume"]
        result = FetchResult(
            name="Price History",
            url="fixture://06182/prices",
            ok=True,
            tables=[table],
            raw_text=table.to_string(index=False),
        )
        parsed = ParsedCCASS(
            stock_code="06182",
            issue_id="25809",
            issued_securities=issued_shares,
        )
        parse_price_history(result, parsed, None)
        return parsed

    def test_06182_block_trade_thresholds(self):
        parsed = self.parsed_prices()
        prices = parsed.price_history_table.set_index("Date")

        self.assertFalse(bool(prices.at["2026-07-15", "BLOCK_TRADE_SUSPECT"]))
        self.assertFalse(bool(prices.at["2026-09-01", "BLOCK_TRADE_SUSPECT"]))
        self.assertTrue(bool(prices.at["2026-09-03", "BLOCK_TRADE_SUSPECT"]))
        self.assertAlmostEqual(prices.at["2026-09-03", "volume_pct_issued"], 66.067, places=3)
        self.assertAlmostEqual(
            prices.at["2026-09-03", "vwap_close_divergence_pct"],
            61.5385,
            places=3,
        )
        self.assertGreaterEqual(prices.at["2026-09-03", "implied_block_price_est"], 0.21)
        self.assertLessEqual(prices.at["2026-09-03", "implied_block_price_est"], 0.26)
        self.assertEqual(
            prices.at["2026-09-03", "implied_block_price_method"],
            "daily_ohlcv_residual",
        )
        self.assertNotIn(
            "Turnover is estimated as volume \u00d7 close, not actual turnover",
            parsed.analysis_warnings,
        )

    def test_block_trade_warning_points_to_t_plus_two_snapshot(self):
        parsed = self.parsed_prices()
        warning = next(item for item in parsed.analysis_warnings if item.startswith("BLOCK_TRADE_SUSPECT:"))
        self.assertIn("2026-09-03", warning)
        self.assertIn("Check T+2 Holdings Diff on 2026-09-07", warning)
        self.assertIn("exact block price requires tick or special-trade data", warning)

    def test_missing_issued_shares_fails_closed(self):
        parsed = self.parsed_prices(issued_shares="")
        prices = parsed.price_history_table
        self.assertFalse(prices["BLOCK_TRADE_SUSPECT"].any())
        self.assertTrue(prices["volume_pct_issued"].isna().all())

    def test_block_trade_metrics_and_warning_are_exported_to_csv(self):
        parsed = self.parsed_prices()
        result = FetchResult(
            name="Price History",
            url="fixture://06182/prices",
            ok=True,
            method="requests",
            tables=[self.price_table()],
        )
        text = combined_stock_csv(parsed, {"Price History": result}).decode("utf-8-sig")
        frame = pd.read_csv(StringIO(text), comment="#")
        price_rows = frame[
            (frame["section"] == "Price History") & (frame["record_type"] == "data")
        ]
        target = price_rows[price_rows["Date"] == "2026-09-03"].iloc[0]
        for column in (
            "volume_pct_issued",
            "vwap_close_divergence_pct",
            "BLOCK_TRADE_SUSPECT",
            "implied_block_price_est",
            "implied_block_price_method",
        ):
            self.assertIn(column, frame.columns)
        self.assertTrue(bool(target["BLOCK_TRADE_SUSPECT"]))
        self.assertEqual(target["implied_block_price_method"], "daily_ohlcv_residual")
        warnings = frame[frame["section"] == "Data Quality Warnings"]["error_message"].astype(str)
        self.assertTrue(warnings.str.contains("BLOCK_TRADE_SUSPECT: 2026-09-03", regex=False).any())


if __name__ == "__main__":
    unittest.main()
