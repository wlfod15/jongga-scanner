import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from night_futures import is_night_signal_window, parse_kospi200_night_html


class NightFuturesTests(unittest.TestCase):
    def test_parses_quote_and_volume(self):
        html = """<div>KOSPI200_NIGHT <b>1,219.00</b><span>-9.15</span>
                  <span>-0.75%</span><i>거래량 15,431</i></div>"""
        quote = parse_kospi200_night_html(html)
        self.assertEqual(quote["현재가"], 1219.0)
        self.assertEqual(quote["변동률%"], -0.75)
        self.assertEqual(quote["거래량"], 15431)

    def test_signal_window_covers_night_and_preopen_only(self):
        tz = ZoneInfo("Asia/Seoul")
        self.assertTrue(is_night_signal_window(datetime(2026, 8, 12, 21, 0, tzinfo=tz)))
        self.assertTrue(is_night_signal_window(datetime(2026, 8, 13, 7, 0, tzinfo=tz)))
        self.assertFalse(is_night_signal_window(datetime(2026, 8, 13, 10, 0, tzinfo=tz)))
        self.assertFalse(is_night_signal_window(datetime(2026, 8, 15, 2, 0, tzinfo=tz)))


if __name__ == "__main__":
    unittest.main()
