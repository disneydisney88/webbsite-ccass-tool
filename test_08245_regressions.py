"""Golden-case regression tests for 08245.

The fixture is deliberately synthetic and contains the published golden values;
it never calls Webb-site, SDW, Yahoo, or another live service.
"""

import unittest

import pandas as pd

from utils.fetcher import FetchResult
from utils.parser import parse_results


def _result(name: str, table: pd.DataFrame, raw_text: str = "") -> FetchResult:
    return FetchResult(
        name=name,
        url=f"https://webb-database.com/{name.lower().replace(' ', '-')}",
        final_url=f"https://webb-database.com/{name.lower().replace(' ', '-')}",
        status=200,
        fetched_time="2026-08-31T05:24:45+00:00",
        tables=[table],
        raw_text=raw_text,
        method="fixture",
        ok=True,
    )


def golden_08245_results() -> dict[str, FetchResult]:
    top = [
        ("B01816", "CHEONG LEE SECURITIES LTD", 122_473_272, 42.30, 42.30, "2026-07-30"),
        ("B01955", "FUTU SECURITIES INTERNATIONAL", 57_702_500, 19.93, 62.23, "2026-08-28"),
        ("C00019", "THE HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED", 10_409_700, 3.60, 65.82, "2026-08-28"),
        ("C00033", "BANK OF CHINA (HONG KONG) LTD", 8_684_093, 3.00, 68.82, "2026-08-27"),
        ("B01668", "BRIGHT SMART SECURITIES INTERNATIONAL (HK) LTD", 5_633_300, 1.95, 70.77, "2026-08-27"),
        ("B01993", "GRANSING SECURITIES CO., LIMITED", 5_400_000, 1.87, 72.63, "2026-05-29"),
        ("B02186", "FUTURE FINANCIAL LIMITED", 4_680_000, 1.62, 74.25, "2026-08-06"),
        ("B01284", "HANG SENG SECURITIES LTD", 4_074_700, 1.41, 75.66, "2026-08-27"),
        ("B01353", "CHINA INTERNATIONAL CAPITAL CORPORATION", 3_706_800, 1.28, 76.94, "2024-06-25"),
        ("B01086", "CHINA EVERBRIGHT SECURITIES INVESTMENT SERVICES (HK) LTD", 3_356_140, 1.16, 78.10, "2026-08-05"),
    ]
    rows = list(top)
    for number in range(11, 141):
        rows.append((f"B{number:05d}", f"TEST PARTICIPANT {number}", 0, 0.0, 78.10, "2026-08-28"))
    holdings = pd.DataFrame(rows, columns=["CCASS ID", "Name", "Holding", "Stake %", "Cumul Stake %", "Last change"])
    holdings["Row"] = range(1, len(holdings) + 1)
    holdings = holdings[["Row", "CCASS ID", "Name", "Holding", "Last change", "Stake %", "Cumul Stake %"]]

    concentration = pd.DataFrame({
        "Date": ["2026-08-28"],
        "Top 5 %": ["75.5048"],
        "Top 10 %": ["83.3233"],
        "Top 10 + NCIP %": ["83.3233"],
        "Stake in CCASS %": ["93.73"],
    })
    big_changes = pd.DataFrame({
        "Date": ["26-08-25", "26-08-25"],
        "CCASS ID": ["B01816", "B01955"],
        "Participant": ["CHEONG LEE SECURITIES LTD", "FUTU SECURITIES INTERNATIONAL"],
        "Change": ["0.39", "-0.35"],
    })
    changes = pd.DataFrame({
        "Participant": ["B01816 CHEONG LEE SECURITIES LTD"],
        "Change": ["100"],
        "Change %": ["0.03"],
        "Holding after": ["122473272"],
        "Stake after": ["42.30"],
    })
    price = pd.DataFrame({"Date": ["2026-08-28"], "Close": ["0.1"], "Volume": ["1000"], "Turnover": ["100"]})
    company = pd.DataFrame({"Exchange": ["HK Main"], "Code": ["08245"], "Listed": ["2014-06-30"]})
    raw = "CCASS holdings on 2026-08-28\nIssued securities: 289,541,272\nTotal in CCASS: 271,377,339 (93.73%)\nSecurities not in CCASS: 18,163,933"
    return {
        "Company / orgdata": _result("Company", company, "Canopy SkyFire Group Limited 烽翼集團有限公司"),
        "Holdings": _result("Holdings", holdings, raw),
        "Changes": _result("Changes", changes, "Trading date: 2026-08-28"),
        "Big Changes": _result("Big Changes", big_changes),
        "Concentration": _result("Concentration", concentration),
        "Price History": _result("Price History", price),
    }


class Golden08245Tests(unittest.TestCase):
    def test_golden_holdings_denominators_and_dates(self) -> None:
        parsed = parse_results("15949", golden_08245_results(), stock_code="08245")
        self.assertEqual(len(parsed.holdings_table), 140)
        row = parsed.holdings_table.iloc[0]
        self.assertEqual(row["CCASS ID"], "B01816")
        self.assertEqual(row["shares_held"], 122_473_272)
        self.assertEqual(row["last_change_date"], "2026-07-30")
        self.assertEqual(parsed.holdings_table.iloc[1]["last_change_date"], "2026-08-28")
        self.assertAlmostEqual(parsed.concentration_table.iloc[0]["top5_pct_of_issued"], 70.7681, delta=0.01)
        self.assertAlmostEqual(parsed.concentration_table.iloc[0]["top5_pct_of_ccass"], 75.5048, delta=0.01)
        self.assertAlmostEqual(parsed.concentration_table.iloc[0]["top10_pct_of_issued"], 78.0961, delta=0.01)
        self.assertAlmostEqual(parsed.concentration_table.iloc[0]["top10_pct_of_ccass"], 83.3233, delta=0.01)

    def test_golden_participant_join_and_absolute_share_fields(self) -> None:
        parsed = parse_results("15949", golden_08245_results(), stock_code="08245")
        self.assertTrue(parsed.holdings_table["ccass_id"].is_unique)
        self.assertTrue(set(parsed.big_changes_table["CCASS ID"]) <= set(parsed.holdings_table["CCASS ID"]))
        self.assertEqual(parsed.concentration_table.iloc[0]["issued_shares_at_date"], 289_541_272)
        self.assertEqual(parsed.concentration_table.iloc[0]["ccass_total_at_date"], 271_377_339)


if __name__ == "__main__":
    unittest.main()
