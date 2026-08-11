import unittest

from utils.date_semantics import (
    annotate_records,
    derive_dates,
    shift_trading_date,
)


class HKEXTradingCalendarTest(unittest.TestCase):
    def test_required_settlement_to_trade_regressions(self):
        cases = {
            "2026-05-11": "2026-05-07",
            "2026-05-12": "2026-05-08",
            "2026-05-19": "2026-05-15",
            "2026-08-11": "2026-08-07",
            "2026-08-12": "2026-08-10",
        }
        for settlement_date, expected_trade_date in cases.items():
            with self.subTest(settlement_date=settlement_date):
                actual, _warning = shift_trading_date(settlement_date, -2)
                self.assertEqual(actual, expected_trade_date)

    def test_08529_falsification_regressions(self):
        cases = {
            "2026-05-11": "2026-05-07",
            "2026-05-19": "2026-05-15",
            "2026-05-22": "2026-05-20",
        }
        for ccass_date, expected_trade_date in cases.items():
            with self.subTest(ccass_date=ccass_date):
                derived = derive_dates(ccass_date, "settlement")
                self.assertEqual(derived.ccass_date, ccass_date)
                self.assertEqual(derived.implied_trade_date, expected_trade_date)
                self.assertEqual(derived.date_basis, "settlement")

    def test_trade_basis_keeps_trade_date_and_derives_settlement(self):
        derived = derive_dates("2026-08-07", "trade")
        self.assertEqual(derived.ccass_date, "2026-08-07")
        self.assertEqual(derived.implied_trade_date, "2026-08-07")
        self.assertEqual(derived.implied_settlement_date, "2026-08-11")
        self.assertEqual(derived.date_basis, "trade")

    def test_outside_supported_calendar_fails_loud(self):
        derived = derive_dates("1980-01-02", "settlement")
        self.assertEqual(derived.implied_trade_date, "")
        self.assertIn("coverage", derived.warning)


class RowDateSemanticsTest(unittest.TestCase):
    def test_big_changes_rows_receive_dual_dates(self):
        rows, warnings = annotate_records(
            [{"Date": "26-05-11", "Participant": "Broker A", "Change %": 2.88}],
            "Big Changes",
        )
        self.assertEqual(rows[0]["ccass_date"], "2026-05-11")
        self.assertEqual(rows[0]["implied_trade_date"], "2026-05-07")
        self.assertEqual(rows[0]["implied_settlement_date"], "2026-05-11")
        self.assertEqual(rows[0]["date_basis"], "settlement")
        self.assertTrue(isinstance(warnings, list))

    def test_changes_rows_use_explicit_trade_date(self):
        rows, _warnings = annotate_records(
            [{"Participant": "Broker A", "Change": 1000}],
            "Changes",
            default_date="2026-08-07",
        )
        self.assertEqual(rows[0]["ccass_date"], "2026-08-07")
        self.assertEqual(rows[0]["implied_trade_date"], "2026-08-07")
        self.assertEqual(rows[0]["implied_settlement_date"], "2026-08-11")
        self.assertEqual(rows[0]["date_basis"], "trade")


if __name__ == "__main__":
    unittest.main()
