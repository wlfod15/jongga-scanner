import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NIGHT_FUTURES_URL = "https://sonmul.co.kr/"


def parse_kospi200_night_html(html):
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = re.search(
        r"KOSPI200_NIGHT\s*([\d,]+(?:\.\d+)?)\s*([+-][\d,]+(?:\.\d+)?)\s*([+-][\d.]+)%\s*거래량\s*([\d,]+)",
        text,
    )
    if not match:
        return None
    price, change, change_pct, volume = match.groups()
    return {
        "현재가": float(price.replace(",", "")),
        "변동": float(change.replace(",", "")),
        "변동률%": float(change_pct),
        "거래량": int(volume.replace(",", "")),
    }


def is_night_signal_window(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if now.weekday() >= 5:
        return False
    current = now.timetz().replace(tzinfo=None)
    return current >= time(18, 0) or current < time(8, 45)


def kospi200_night_quote(timeout=10, now=None):
    if not is_night_signal_window(now):
        return None
    response = requests.get(
        NIGHT_FUTURES_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; jongga-scanner/5.0)"},
        timeout=timeout,
    )
    response.raise_for_status()
    quote = parse_kospi200_night_html(response.text)
    if not quote or quote["거래량"] <= 0:
        return None
    quote.update({
        "출처": "sonmul.co.kr (KIS 시세 기반)",
        "조회시각": (now or datetime.now(ZoneInfo("Asia/Seoul"))).strftime("%Y-%m-%d %H:%M KST"),
    })
    return quote
