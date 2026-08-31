from __future__ import annotations

from io import BytesIO
import re
import unittest

import pandas as pd

from utils.exporters import combined_stock_csv
from utils.fetcher import FetchResult
from utils.parser import DateSanityError, ParsedCCASS, parse_results, validate_date_sanity


def fetched(section: str, tables: list[pd.DataFrame], raw_text: str = "", html: str = "") -> FetchResult:
    return FetchResult(
        name=section,
        url=f"https://webb-database.com/{section.lower().replace(' ', '-')}",
        final_url=f"https://webb-database.com/{section.lower().replace(' ', '-')}",
        status=200,
        fetched_time="2026-08-26T05:00:00+00:00",
        raw_text=raw_text,
        html=html,
        tables=tables,
        method="requests",
        ok=True,
    )


def stock_03301_results() -> dict[str, FetchResult]:
    company = pd.DataFrame(
        {
            "Exchange": ["HK Main"],
            "Code": ["03301"],
            "Listed": ["2016-01-13"],
        }
    )
    summary = pd.DataFrame(
        {
            "Type of holder": ["Total in CCASS", "Securities not in CCASS", "Issued securities"],
            "Holding": ["1,278,769,238", "404,660,762", "1,683,430,000"],
            "Stake %": ["75.96", "24.04", "100.00"],
        }
    )
    holdings = pd.DataFrame(
        {
            "Row": [1, 2, 3, 4, 5],
            "CCASS ID": ["Participant ID: C00019", "B00001", "B00002", "B00003", "B00004"],
            "Name": [
                "Name of CCASS Participant (* for Consenting Investor Participants ): THE HONGKONG AND SHANGHAI BANKING",
                "SECOND SECURITIES LTD",
                "THIRD SECURITIES LTD",
                "FOURTH SECURITIES LTD",
                "FIFTH SECURITIES LTD",
            ],
            "Holding": ["625,875,289", "93,857,000", "68,392,000", "67,325,000", "59,796,500"],
            "Stake %": ["37.18%", "5.58%", "4.06%", "4.00%", "3.52%"],
            "Cumul. Stake %": ["37.18%", "42.76%", "46.82%", "50.82%", "54.34%"],
        }
    )
    participant_directory = pd.DataFrame(
        {
            "Count": [1, 1, 1, 1, 1],
            "CCASS ID": ["C00019", "B00001", "B00002", "B00003", "B00004"],
            "Name": [
                "HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED (THE)",
                "SECOND SECURITIES LTD",
                "THIRD SECURITIES LTD",
                "FOURTH SECURITIES LTD",
                "FIFTH SECURITIES LTD",
            ],
        }
    )
    big_changes = pd.DataFrame(
        {
            "Date Y-M-D": [
                "26-08-07",
                "",
                "25-06-16",
                "",
                "24-03-19",
                "",
                "24-03-14",
                "16-01-13",
            ],
            "Participant": [
                "ABN AMRO CLEARING HONG KONG",
                "BANK OF CHINA (HONG KONG)",
                "THE HONGKONG AND SHANGHAI BANKING",
                "DEUTSCHE BANK AG",
                "THE HONGKONG AND SHANGHAI BANKING",
                "STANDARD CHARTERED BANK (HK)",
                "MORGAN STANLEY HONG KONG SECURITIES",
                "THE HONGKONG AND SHANGHAI BANKING",
            ],
            "Change": ["0.48", "-0.42", "3.97", "-3.97", "4.45", "-4.45", "-4.28", "4.28"],
        }
    )
    concentration = pd.DataFrame(
        {
            "Date": ["2026-08-25", "2026-08-04"],
            "Top 5 %": ["71.55", "71.55"],
            "Top 10 %": ["83.15", "83.18"],
            "Top 10 + NCIP %": ["83.15", "83.18"],
            "Stake in CCASS %": ["75.96", "75.96"],
        }
    )
    return {
        "Company / orgdata": fetched(
            "Company / orgdata",
            [company],
            html="<html><h1>Ronshine China Holdings Limited 融信中國控股有限公司</h1></html>",
        ),
        "Holdings": fetched(
            "Holdings",
            [summary, holdings],
            raw_text=(
                "CCASS holdings on 2026-08-25 Total in CCASS 1,278,769,238 75.96% "
                "Securities not in CCASS 404,660,762 Issued securities 1,683,430,000"
            ),
        ),
        "Big Changes": fetched(
            "Big Changes",
            [big_changes],
            raw_text="This table shows daily movements larger than 0.25% of outstanding shares.",
        ),
        "Concentration": fetched("Concentration", [concentration]),
        "Participants": fetched("Participants", [participant_directory]),
    }


class Stock03301RegressionTests(unittest.TestCase):
    def test_big_changes_dates_fields_and_required_pairs(self) -> None:
        parsed = parse_results("18546", stock_03301_results(), stock_code="3301")

        self.assertEqual(parsed.stock_code, "03301")
        self.assertEqual(parsed.listing_date, "2016-01-13")
        self.assertEqual(parsed.big_changes_table["Date"].min(), "2016-01-13")
        self.assertEqual(parsed.big_changes_table["Date"].max(), "2026-08-07")
        self.assertTrue(parsed.big_changes_table["Date"].map(lambda value: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))).all())
        self.assertTrue(parsed.big_changes_table["change_shares"].notna().all())
        self.assertTrue(parsed.big_changes_table["change_shares_is_estimate"].all())
        self.assertTrue(parsed.big_changes_table["change_pct_basis"].eq("issued_shares").all())
        self.assertTrue(parsed.big_changes_table["threshold_used"].eq(0.25).all())

        required = {
            ("2026-08-07", "ABN AMRO CLEARING HONG KONG", 0.48),
            ("2026-08-07", "BANK OF CHINA (HONG KONG)", -0.42),
            ("2025-06-16", "THE HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED", 3.97),
            ("2025-06-16", "DEUTSCHE BANK AG", -3.97),
            ("2024-03-19", "THE HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED", 4.45),
            ("2024-03-19", "STANDARD CHARTERED BANK (HK)", -4.45),
            ("2024-03-14", "MORGAN STANLEY HONG KONG SECURITIES", -4.28),
        }
        actual = {
            (row["Date"], row["Participant"], float(row["change_pct"]))
            for _, row in parsed.big_changes_table.iterrows()
        }
        self.assertTrue(required.issubset(actual))

    def test_participant_hygiene_and_holdings_date_aliases(self) -> None:
        parsed = parse_results("18546", stock_03301_results(), stock_code="03301")
        hsbc = parsed.holdings_table.iloc[0]

        self.assertEqual(hsbc["Participant"], "THE HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED")
        self.assertEqual(hsbc["CCASS ID"], "C00019")
        self.assertEqual(hsbc["Date"], "2026-08-25")
        self.assertEqual(hsbc["holding_shares"], 625875289)
        self.assertEqual(hsbc["stake_pct_of_issued"], 37.18)
        self.assertEqual(parsed.big_changes_table.iloc[2]["CCASS ID"], "C00019")

    def test_section_asof_warns_when_big_changes_lag(self) -> None:
        parsed = parse_results("18546", stock_03301_results(), stock_code="03301")

        self.assertEqual(parsed.section_asof["Big Changes"]["latest_date"], "2026-08-07")
        self.assertEqual(parsed.section_asof["Big Changes"]["row_count"], 8)
        self.assertEqual(parsed.section_asof["Concentration"]["latest_date"], "2026-08-25")
        self.assertEqual(parsed.section_asof["Big Changes"]["lag_trading_days"], 12)
        self.assertTrue(
            any(
                "Big Changes coverage ends 2026-08-07" in warning
                and "12 XHKG trading sessions behind Concentration (2026-08-25)" in warning
                for warning in parsed.analysis_warnings
            )
        )

    def test_date_sanity_guard_rejects_pre_listing_date(self) -> None:
        parsed = ParsedCCASS(
            listing_date="2016-01-13",
            fetched_time="2026-08-26T05:00:00+00:00",
            big_changes_table=pd.DataFrame({"Date": ["2001-08-07"]}),
        )
        with self.assertRaisesRegex(DateSanityError, "earlier than listing date 2016-01-13"):
            validate_date_sanity(parsed)

    def test_combined_csv_has_bom_and_iso_date_columns(self) -> None:
        results = stock_03301_results()
        parsed = parse_results("18546", results, stock_code="03301")
        payload = combined_stock_csv(parsed, results)

        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        frame = pd.read_csv(BytesIO(payload), comment="#", dtype=str).fillna("")
        for column in frame.columns:
            if column in {"issued_shares_at_date", "ccass_total_at_date"}:
                continue
            if not (column == "Date" or column.lower().endswith("_date")):
                continue
            for value in frame[column]:
                if not value or value.startswith("not available"):
                    continue
                self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}$", msg=f"{column}={value}")

    def test_concentration_holdings_denominator_identity(self) -> None:
        parsed = parse_results("18546", stock_03301_results(), stock_code="03301")
        row = parsed.concentration_table.iloc[0]

        self.assertEqual(row["top5_identity_check"], "pass")
        self.assertAlmostEqual(row["top5_pct_of_ccass"], 71.55)
        self.assertAlmostEqual(row["stake_pct_of_issued"], 75.96)
        self.assertLess(row["top5_identity_difference_pp"], 0.15)

    def test_critical_section_failure_is_marked_partial(self) -> None:
        results = stock_03301_results()
        results["Holdings"] = fetched("Holdings", [], "")
        results["Holdings"].ok = False
        results["Holdings"].error_type = "SOURCE_EMPTY"
        results["Holdings"].error_message = "No holdings table returned"
        parsed = parse_results("18546", results, stock_code="03301")

        self.assertEqual(parsed.completeness_status, "partial")
        self.assertIn("Holdings", parsed.critical_sections_failed)
        self.assertTrue(any("Critical sections failed" in item for item in parsed.analysis_warnings))


if __name__ == "__main__":
    unittest.main()
