import unittest

import numpy as np
import pandas as pd

from market_ui import extract_close, format_change, last_metrics, legacy_market_score, market_impact


class MarketUiTest(unittest.TestCase):
    def test_returns_use_previous_and_five_trading_sessions(self):
        result = last_metrics(pd.Series([100, 101, 102, 103, 104, 110]))
        self.assertAlmostEqual(result["전일대비"], (110 / 104 - 1) * 100)
        self.assertAlmostEqual(result["5일 누적"], 10.0)

    def test_short_history_does_not_fake_five_day_return(self):
        result = last_metrics(pd.Series([100, 101]))
        self.assertTrue(np.isnan(result["5일 누적"]))

    def test_multiindex_close_selects_requested_ticker(self):
        columns = pd.MultiIndex.from_tuples([("Close", "NQ=F"), ("Close", "OTHER")])
        frame = pd.DataFrame([[10, 99], [11, 98]], columns=columns)
        self.assertEqual(extract_close(frame, "NQ=F").tolist(), [10, 11])

    def test_market_impact_thresholds_and_inverse_direction(self):
        self.assertEqual(market_impact("나스닥100 선물", .3), "긍정")
        self.assertEqual(market_impact("나스닥100 선물", .29), "중립")
        self.assertEqual(market_impact("VIX", -3), "긍정")
        self.assertEqual(market_impact("원/달러", .3), "부정")
        self.assertEqual(market_impact("미국10년물", .9), "중립")
        self.assertEqual(market_impact("WTI", -3), "부정")

    def test_change_format_keeps_symbol_and_percent(self):
        self.assertEqual(format_change(.62), "▲ +0.62%")
        self.assertEqual(format_change(-.83), "▼ -0.83%")
        self.assertEqual(format_change(0), "- 0.00%")

    def test_existing_market_score_model_is_preserved(self):
        data = {
            "VIX": {"현재": 17, "전일대비": 0},
            "원/달러": {"전일대비": -.4},
            "나스닥100 선물": {"전일대비": .5},
            "미국10년물": {"전일대비": 0},
            "WTI": {"전일대비": 0},
            "KOSPI": {"전일대비": 1.1, "20일선상": True},
            "KOSDAQ": {"전일대비": -1.1, "20일선상": False},
        }
        score, label, reasons = legacy_market_score(data)
        self.assertEqual(score, 75)
        self.assertEqual(label, "우호")
        self.assertIn("VIX 안정", reasons)


if __name__ == "__main__":
    unittest.main()
