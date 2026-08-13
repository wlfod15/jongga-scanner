import unittest
from unittest.mock import patch
import sys
import types

import numpy as np
import pandas as pd

sys.modules.setdefault("requests", types.SimpleNamespace(post=None))
sys.modules.setdefault("yfinance", types.SimpleNamespace(download=None))

from global_price import after_hours_snapshot


class GlobalPriceTest(unittest.TestCase):
    def test_samsung_gdr_divides_by_twenty_five(self):
        products = pd.DataFrame([{
            "krx_code": "005930", "ticker": "SMSN.IL", "provider": "yahoo",
            "currency": "USD", "product_type": "gdr", "ratio": 25,
            "verified_ratio": True, "display_name": "Samsung GDR",
            "source_url": "official", "dex": "", "note": "verified",
        }])
        with patch("global_price._latest_yahoo", return_value=2000.0):
            result = after_hours_snapshot(
                "005930", 100_000, products, {"USD/KRW": 1400.0}, {},
            )
        self.assertEqual(result["해외24h 환산가"], 112_000)
        self.assertEqual(result["원주환산비율"], 25.0)

    def test_hynix_adr_multiplies_by_ten(self):
        products = pd.DataFrame([{
            "krx_code": "000660", "ticker": "SKHY", "provider": "yahoo",
            "currency": "USD", "product_type": "adr", "ratio": 0.1,
            "verified_ratio": True, "display_name": "SK hynix ADR",
            "source_url": "SEC", "dex": "", "note": "verified",
        }])
        with patch("global_price._latest_yahoo", return_value=150.0):
            result = after_hours_snapshot(
                "000660", 2_100_000, products, {"USD/KRW": 1400.0}, {},
            )
        self.assertEqual(result["해외24h 환산가"], 2_100_000)
        self.assertEqual(result["해외 괴리율%"], 0.0)

    def test_unverified_ratio_is_not_converted(self):
        products = pd.DataFrame([{
            "krx_code": "123456", "ticker": "UNKNOWN", "provider": "yahoo",
            "currency": "USD", "product_type": "adr", "ratio": 1,
            "verified_ratio": False, "display_name": "Unknown DR",
            "source_url": "", "dex": "", "note": "",
        }])
        result = after_hours_snapshot(
            "123456", 10_000, products, {"USD/KRW": 1400.0}, {},
        )
        self.assertTrue(np.isnan(result["해외24h 환산가"]))
        self.assertIn("환산비율 미검증", result["해외가격 신호"])


if __name__ == "__main__":
    unittest.main()

