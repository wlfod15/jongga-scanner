import unittest

import numpy as np
import pandas as pd

from daily_structure import analyze_daily_structure
from nxt_premarket import analyze_nxt_premarket
from structure_score import calculate_structure_score


def daily_frame(prices):
    idx = pd.bdate_range("2025-01-02", periods=len(prices))
    close = pd.Series(prices, index=idx, dtype=float)
    return pd.DataFrame({"Open": close.shift(1).fillna(close.iloc[0]), "High": close * 1.015,
                         "Low": close * .985, "Close": close, "Volume": 1000 + np.arange(len(close)) * 3})


class StructureAnalysisTest(unittest.TestCase):
    def test_returns_traceable_structure_without_changing_legacy_score(self):
        prices = np.linspace(60, 120, 180) + np.sin(np.arange(180) / 4) * 4
        result = analyze_daily_structure(daily_frame(prices))
        self.assertIn(result["차트구조"], {"상승추세", "상승 후 고가조정", "상승 후 시간조정", "바닥 반등 초기", "방향 불명확"})
        self.assertIn("구조원본값", result)
        self.assertIn("선행스팬1", result["구조원본값"])
        score = calculate_structure_score(result)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_insufficient_daily_data_is_not_estimated(self):
        result = analyze_daily_structure(daily_frame(np.linspace(10, 20, 80)))
        self.assertEqual(result["차트구조"], "데이터 부족")

    def test_nxt_requires_real_bars(self):
        result = analyze_nxt_premarket(pd.DataFrame(), 100)
        self.assertEqual(result["NXT분석상태"], "데이터 없음")

    def test_nxt_summary_uses_actual_bars(self):
        idx = pd.date_range("2026-08-14 08:00", periods=10, freq="5min")
        close = pd.Series([103, 104, 105, 104.5, 105, 105.5, 106, 106.2, 106.1, 106.5], index=idx)
        bars = pd.DataFrame({"Open": close.shift(1).fillna(102), "High": close + .3,
                             "Low": close - .3, "Close": close, "Volume": np.arange(10) * 100 + 500})
        result = analyze_nxt_premarket(bars, 100)
        self.assertEqual(result["NXT분석상태"], "산출")
        self.assertAlmostEqual(result["NXT 갭률%"], 6.5)
        self.assertTrue(0 <= result["NXT 프리장 점수"] <= 100)


if __name__ == "__main__":
    unittest.main()
