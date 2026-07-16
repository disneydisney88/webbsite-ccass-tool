"""Tests for CCASS snapshot parsing and two-date diffs (handover 3.2)."""

import unittest

import pandas as pd

from utils.snapshot import diff_snapshots, parse_holdings_snapshot, snapshot_url


def snapshot_table(rows):
    return pd.DataFrame(rows, columns=["CCASS ID", "Name", "Holding", "Stake %"])


class ParseSnapshotTest(unittest.TestCase):
    def test_parses_participant_rows_and_skips_aggregates(self):
        table = snapshot_table(
            [
                ["C00028", "NANYANG COMMERCIAL BANK LTD", "445,643,533", 51.91],
                ["B01660", "GRANSING SECURITIES CO., LIMITED", "218,034,300", 25.40],
                ["", "Total in CCASS", "857,000,000", 99.85],
                [None, "Issued securities", "858,330,000", 100.0],
            ]
        )
        rows = parse_holdings_snapshot([table])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ccass_id"], "C00028")
        self.assertEqual(rows[0]["holding"], 445643533)
        self.assertEqual(rows[1]["stake_pct"], 25.40)

    def test_picks_largest_valid_table(self):
        small = snapshot_table([["B01955", "FUTU", "1,000", 0.1]])
        big = snapshot_table([["B01955", "FUTU", "1,000", 0.1], ["C00019", "HSBC", "2,000", 0.2]])
        rows = parse_holdings_snapshot([small, big])
        self.assertEqual(len(rows), 2)

    def test_snapshot_url(self):
        self.assertEqual(
            snapshot_url("27470", "2026-06-11"),
            "https://webbsite.0xmd.com/ccass/choldings.asp?d=2026-06-11&i=27470",
        )


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.rows_a = [
            {"ccass_id": "C00028", "name": "NANYANG COMMERCIAL BANK LTD", "holding": 445_000_000, "stake_pct": 60.0},
            {"ccass_id": "B01955", "name": "FUTU SECURITIES INTERNATIONAL", "holding": 25_000_000, "stake_pct": 3.4},
            {"ccass_id": "C00010", "name": "CITIBANK N.A.", "holding": 80_000_000, "stake_pct": 10.8},
        ]
        self.rows_b = [
            {"ccass_id": "C00028", "name": "NANYANG COMMERCIAL BANK LTD", "holding": 445_000_000, "stake_pct": 51.9},
            {"ccass_id": "B01955", "name": "FUTU SECURITIES INTERNATIONAL", "holding": 29_874_000, "stake_pct": 3.5},
            {"ccass_id": "B01660", "name": "GRANSING SECURITIES CO., LIMITED", "holding": 218_034_300, "stake_pct": 25.4},
        ]
        self.diff = diff_snapshots(self.rows_a, self.rows_b, "2026-06-11", "2026-07-14")

    def test_new_and_exited(self):
        self.assertEqual(self.diff["new_participants"], ["B01660"])
        self.assertEqual(self.diff["exited_participants"], ["C00010"])

    def test_changes_sorted_by_magnitude_and_unchanged_dropped(self):
        ids = [c["ccass_id"] for c in self.diff["changes"]]
        # Gransing +218M first, Citibank -80M second, Futu +4.87M third; Nanyang unchanged -> dropped
        self.assertEqual(ids, ["B01660", "C00010", "B01955"])
        gransing = self.diff["changes"][0]
        self.assertEqual(gransing["status"], "new")
        self.assertEqual(gransing["change_shares"], 218_034_300)
        self.assertEqual(gransing["category"], "boutique")

    def test_stake_points_change(self):
        futu = next(c for c in self.diff["changes"] if c["ccass_id"] == "B01955")
        self.assertAlmostEqual(futu["change_stake_points"], 0.1, places=4)
        self.assertEqual(futu["status"], "increased")

    def test_category_net_flow(self):
        net = self.diff["category_net_change_shares"]
        self.assertEqual(net["boutique"], 218_034_300)   # Gransing in
        self.assertEqual(net["intl_broker"], -80_000_000)  # Citibank out
        self.assertEqual(net["retail"], 4_874_000)         # Futu up
        self.assertEqual(net["bank"], 0)                   # Nanyang unchanged

    def test_top_concentration_both_dates(self):
        self.assertAlmostEqual(self.diff["top5_stake_a_pct"], 74.2, places=1)
        self.assertAlmostEqual(self.diff["top5_stake_b_pct"], 80.8, places=1)
        self.assertEqual(self.diff["participants_a"], 3)
        self.assertEqual(self.diff["participants_b"], 3)


if __name__ == "__main__":
    unittest.main()
