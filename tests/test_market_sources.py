import unittest

import pandas as pd

from market_sources import parse_naver_investor_tables


class NaverInvestorParserTests(unittest.TestCase):
    def test_converts_recent_net_shares_to_estimated_values(self):
        table = pd.DataFrame({
            "날짜": ["2026.08.11", "2026.08.12"],
            "종가": ["250,000", "255,000"],
            "기관": ["+100", "-20"],
            "외국인": ["-10", "+200"],
            "외국인 보유주수": [1, 1],
        })
        summary, flow = parse_naver_investor_tables([table])
        self.assertEqual(summary["기관5일순매수(억원)"], 0.2)
        self.assertEqual(summary["외국인5일순매수(억원)"], 0.5)
        self.assertIn("KRX 제공", summary["수급출처"])
        self.assertEqual(list(flow.columns), ["기관(추정금액)", "외국인(추정금액)"])

    def test_returns_empty_when_required_columns_are_missing(self):
        summary, flow = parse_naver_investor_tables([pd.DataFrame({"날짜": ["2026.08.12"]})])
        self.assertEqual(summary, {})
        self.assertTrue(flow.empty)


if __name__ == "__main__":
    unittest.main()
