import unittest

import pandas as pd

from utils.parser import ParsedCCASS
from utils.report import build_report


class PartialReportTest(unittest.TestCase):
    def test_concentration_values_survive_failed_holdings(self):
        parsed = ParsedCCASS(
            stock_code="03301",
            stock_name="Ronshine China Holdings Limited",
            issue_id="18546",
            top5_cumulative_pct="71.55%",
            top10_cumulative_pct="83.18%",
            concentration_latest_date="2026-08-06",
            concentration_table=pd.DataFrame(
                [{"Date": "2026-08-06", "Top 5 %": "71.55%", "Top 10 %": "83.18%"}]
            ),
        )

        report = build_report(parsed, {})

        self.assertIn("* Top 5 %: 71.55%", report)
        self.assertIn("* Top 10 %: 83.18%", report)
        self.assertIn(
            "* Largest participant: not available because Holdings table parsing failed",
            report,
        )
        self.assertIn(
            "* Total in CCASS %: not available because Holdings table parsing failed",
            report,
        )


if __name__ == "__main__":
    unittest.main()
