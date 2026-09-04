from io import StringIO
import unittest

import pandas as pd

import api
from utils.date_semantics import ANALYSIS_DATE_NOTICE
from utils.exporters import combined_stock_csv
from utils.fetcher import FetchResult
from utils.parser import ParsedCCASS, apply_date_semantics
from utils.report import build_report


class ExportDateSemanticsTest(unittest.TestCase):
    def make_parsed(self) -> ParsedCCASS:
        parsed = ParsedCCASS(
            stock_code="03301",
            stock_name="Ronshine China Holdings Limited",
            issue_id="18546",
            holdings_data_date="2026-08-11",
            changes_trading_date="2026-08-10",
            big_changes_latest_date="2026-08-12",
            concentration_latest_date="2026-08-12",
            holdings_table=pd.DataFrame(
                [{"Rank": 1, "Participant": "Broker A", "CCASS ID": "B00001"}]
            ),
            changes_table=pd.DataFrame([{"Participant": "Broker A", "Change": 1000}]),
            big_changes_table=pd.DataFrame(
                [{"Date": "2026-08-12", "Participant": "Broker A", "Change %": 1.0}]
            ),
            concentration_table=pd.DataFrame(
                [{"Date": "2026-08-12", "Top 5 %": 60.0, "Top 10 %": 70.0}]
            ),
        )
        apply_date_semantics(parsed)
        return parsed

    def test_csv_starts_with_semantic_comments_and_keeps_row_dates(self):
        parsed = self.make_parsed()
        results = {
            "Holdings": FetchResult(name="Holdings", url="https://example.test", ok=True),
        }
        text = combined_stock_csv(parsed, results).decode("utf-8-sig")

        self.assertTrue(text.startswith("# 日期基準：Holdings/Big Changes/Concentration"))
        frame = pd.read_csv(StringIO(text), comment="#")
        holdings = frame[(frame["section"] == "Holdings") & (frame["record_type"] == "data")]
        self.assertEqual(holdings.iloc[0]["ccass_date"], "2026-08-11")
        self.assertEqual(holdings.iloc[0]["settlement_date"], "2026-08-11")
        self.assertEqual(holdings.iloc[0]["trade_date"], "2026-08-07")
        self.assertEqual(holdings.iloc[0]["implied_trade_date"], "2026-08-07")
        self.assertEqual(holdings.iloc[0]["date_basis"], "settlement")
        self.assertEqual(parsed.data_as_of_trading_date, "2026-08-10")

        metadata = frame[(frame["section"] == "Metadata") & (frame["record_type"] == "metadata")]
        self.assertEqual(metadata.iloc[0]["date_basis"], "settlement")

    def test_streamlit_and_api_markdown_start_with_semantic_notice(self):
        parsed = self.make_parsed()
        report = build_report(parsed, {})
        self.assertIn("> 日期基準：Holdings/Big Changes/Concentration", report.split("## AI Analysis")[0])

        payload = {
            "metadata": {"code": "03301", "name": parsed.stock_name},
            "holdings_summary": {},
            "concentration": {"records": []},
            "holdings": [],
            "changes": [],
            "big_changes": [],
            "data_quality_warnings": [],
            "errors": [],
        }
        api_markdown = api.compact_payload_to_markdown(payload)
        self.assertIn(f"> {ANALYSIS_DATE_NOTICE}", api_markdown)


if __name__ == "__main__":
    unittest.main()
