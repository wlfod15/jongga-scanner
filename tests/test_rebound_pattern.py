import unittest
import numpy as np
import pandas as pd

from rebound_pattern import analyze_rebound_pattern, pattern_filter_mask, round_to_tick


class ReboundPatternTests(unittest.TestCase):
    def synthetic(self):
        index = pd.date_range("2026-01-01", periods=100, freq="h")
        close = np.r_[np.linspace(100, 200, 35), np.linspace(195, 100, 35), np.linspace(102, 140, 30)]
        return pd.DataFrame({"Open": close, "High": close + 2, "Low": close - 2,
                             "Close": close, "Volume": np.r_[np.ones(99) * 100, 300]}, index=index)

    def test_detects_swing_and_builds_independent_levels(self):
        result = analyze_rebound_pattern(self.synthetic(), min_swing_pct=10)
        self.assertEqual(result["추정 패턴 상태"], "산출")
        self.assertAlmostEqual(result["+33% 가격"] / result["추정 반등가"], 1.33, delta=.02)
        self.assertIn("+33% 도달전 MAE%", result)
        self.assertTrue(result["패턴 거래량 증가"])

    def test_tick_rounding(self):
        self.assertEqual(round_to_tick(255360), 255500)

    def test_filter_keeps_requested_stage(self):
        frame = pd.DataFrame({"현재 패턴 단계": ["+33% 돌파", "반등가 상향 돌파"],
                              "반등가 거리(%)": [33, 5], "다음 목표까지(%)": [12, 8],
                              "패턴 거래량 증가": [True, False]})
        self.assertEqual(pattern_filter_mask(frame, "+33%선 돌파 종목").tolist(), [True, False])


if __name__ == "__main__":
    unittest.main()
