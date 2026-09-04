import unittest

import api
from utils.date_semantics import (
    align_event_date,
    annotate_records,
    build_date_query_plan,
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
            "2026-09-03": "2026-09-01",
            "2026-09-07": "2026-09-03",
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
        self.assertEqual(rows[0]["settlement_date"], "2026-05-11")
        self.assertEqual(rows[0]["trade_date"], "2026-05-07")
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
        self.assertEqual(rows[0]["trade_date"], "2026-08-07")
        self.assertEqual(rows[0]["settlement_date"], "2026-08-11")
        self.assertEqual(rows[0]["implied_trade_date"], "2026-08-07")
        self.assertEqual(rows[0]["implied_settlement_date"], "2026-08-11")
        self.assertEqual(rows[0]["date_basis"], "trade")


class DateQueryPlanTest(unittest.TestCase):
    def test_event_alignment_reports_target_snapshot_and_remaining_sessions(self):
        aligned, _warning = align_event_date(
            "2026-09-03",
            "trade",
            latest_snapshot_date="2026-09-03",
        )
        self.assertEqual(aligned["trade_date"], "2026-09-03")
        self.assertEqual(aligned["settlement_date"], "2026-09-07")
        self.assertEqual(aligned["latest_snapshot_date"], "2026-09-03")
        self.assertEqual(aligned["remaining_trading_sessions"], 2)
        self.assertEqual(aligned["status"], "pending")

    def test_api_event_alignment_helper_exposes_the_same_dates(self):
        payload = api.build_event_alignment_payload(
            "2026-09-03",
            "trade",
            "2026-09-03",
        )
        self.assertEqual(payload["event_alignment"]["settlement_date"], "2026-09-07")
        self.assertEqual(payload["event_alignment"]["remaining_trading_sessions"], 2)

    def test_trade_range_converts_to_required_settlement_dates(self):
        plan, _warning = build_date_query_plan("2026-08-07", "2026-08-10", "trade")
        self.assertEqual(
            plan,
            [
                {
                    "input_date": "2026-08-07",
                    "trade_date": "2026-08-07",
                    "settlement_date": "2026-08-11",
                },
                {
                    "input_date": "2026-08-10",
                    "trade_date": "2026-08-10",
                    "settlement_date": "2026-08-12",
                },
            ],
        )

    def test_settlement_input_keeps_settlement_bounds(self):
        plan, _warning = build_date_query_plan("2026-08-11", "2026-08-12", "settlement")
        self.assertEqual([row["trade_date"] for row in plan], ["2026-08-07", "2026-08-10"])

    def test_weekend_bound_fails_loud(self):
        with self.assertRaisesRegex(ValueError, "XHKG trading sessions"):
            build_date_query_plan("2026-08-08", "2026-08-10", "trade")


if __name__ == "__main__":
    unittest.main()
