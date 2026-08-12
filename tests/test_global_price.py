import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from global_price import after_hours_snapshot


class AfterHoursSnapshotTest(unittest.TestCase):
    def test_verified_common_share_calculates_gaps(self):
        products = pd.DataFrame([{
            "krx_code": "005930", "ticker": "TEST", "provider": "yahoo", "currency": "USD",
            "product_type": "common_stock", "ratio": 1, "verified_1to1": True, "note": "verified",
        }])
        with patch("global_price._latest_yahoo", return_value=180):
            result = after_hours_snapshot("005930", 255500, products, {"USD/KRW": 1410}, {"005930": 258000})
        self.assertEqual(result["해외24h 환산가"], 253800)
        self.assertAlmostEqual(result["해외 괴리율%"], -0.67, places=2)
        self.assertAlmostEqual(result["NXT 프리미엄%"], 1.65, places=2)
        self.assertEqual(result["해외가격 신호"], "해외가격 약세")

    def test_tokenized_product_is_not_converted(self):
        products = pd.DataFrame([{
            "krx_code": "005930", "ticker": "SAMSUNG", "provider": "example", "currency": "USD",
            "product_type": "tokenized", "ratio": 1, "verified_1to1": False, "note": "not fungible",
        }])
        result = after_hours_snapshot("005930", 255500, products, {"USD/KRW": 1410}, {})
        self.assertTrue(np.isnan(result["해외24h 환산가"]))
        self.assertTrue(result["해외가격 신호"].startswith("비교 제외"))


if __name__ == "__main__":
    unittest.main()
