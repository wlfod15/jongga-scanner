"""Validated after-hours reference prices for KRX stocks.

Only a verified, directly fungible 1:1 common share is converted. ADRs,
tokenized securities, futures and unverified products are deliberately shown
as non-comparable instead of producing a misleading premium.
"""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


MAP_COLUMNS = ["krx_code", "ticker", "provider", "currency", "product_type", "ratio", "verified_1to1", "note"]


def load_product_map(path="overseas_products.csv"):
    file = Path(path)
    if not file.exists():
        return pd.DataFrame(columns=MAP_COLUMNS)
    data = pd.read_csv(file, dtype={"krx_code": str}).reindex(columns=MAP_COLUMNS)
    data["krx_code"] = data["krx_code"].str.zfill(6)
    return data


def _latest_yahoo(ticker):
    for period, interval in (("1d", "1m"), ("5d", "15m"), ("5d", "1d")):
        try:
            frame = yf.download(ticker, period=period, interval=interval, progress=False,
                                auto_adjust=False, threads=False)
            close = frame["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close):
                return float(close.iloc[-1])
        except Exception:
            continue
    return np.nan


def usdkrw_quote():
    value = _latest_yahoo("KRW=X")
    return {"USD/KRW": value, "환율시각UTC": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def nxt_delayed_quotes():
    """Best-effort official NXT page snapshot (the site labels it 20-minute delayed)."""
    try:
        tables = pd.read_html("https://www.nextrade.co.kr/main.do")
    except Exception:
        return {}
    result = {}
    for table in tables:
        table.columns = [str(c).strip() for c in table.columns]
        code_col = next((c for c in table.columns if "종목코드" in c or c.lower() in {"code", "symbol"}), None)
        price_col = next((c for c in table.columns if "현재가" in c), None)
        if not code_col or not price_col:
            continue
        for _, row in table.iterrows():
            code = "".join(ch for ch in str(row[code_col]) if ch.isdigit()).zfill(6)
            price = pd.to_numeric(str(row[price_col]).replace(",", ""), errors="coerce")
            if len(code) == 6 and pd.notna(price):
                result[code] = float(price)
    return result


def after_hours_snapshot(krx_code, krx_close, product_map, fx_quote, nxt_quotes):
    code = str(krx_code).zfill(6)
    close = float(krx_close)
    nxt = nxt_quotes.get(code, np.nan)
    row = {
        "KRX 종가": round(close), "NXT 현재가": nxt, "해외24h 환산가": np.nan,
        "해외 괴리율%": np.nan, "NXT 프리미엄%": np.nan, "해외가격 신호": "데이터 없음",
        "해외상품 유형": "매핑 없음", "해외 원가격": np.nan, "해외 통화": None,
        "USD/KRW": fx_quote.get("USD/KRW", np.nan), "해외가격 시각UTC": None,
    }
    match = product_map[product_map["krx_code"] == code]
    if match.empty:
        return row
    product = match.iloc[0]
    kind = str(product.get("product_type", "unverified")).lower()
    verified = str(product.get("verified_1to1", "false")).lower() in {"1", "true", "yes", "y"}
    ratio = pd.to_numeric(product.get("ratio", np.nan), errors="coerce")
    row["해외상품 유형"] = kind
    row["해외가격 신호"] = "비교 제외: " + (str(product.get("note", "상품 구조 확인 필요")) or "상품 구조 확인 필요")
    # Fail closed: a token/ADR/derivative is never treated as a KRX common share.
    if kind != "common_stock" or not verified or ratio != 1:
        return row
    raw = _latest_yahoo(str(product["ticker"]))
    currency = str(product.get("currency", "USD")).upper()
    fx = float(fx_quote.get("USD/KRW", np.nan))
    row.update({"해외 원가격": raw, "해외 통화": currency,
                "해외가격 시각UTC": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if pd.isna(raw) or (currency == "USD" and pd.isna(fx)):
        row["해외가격 신호"] = "데이터 없음"
        return row
    converted = raw * fx if currency == "USD" else raw if currency == "KRW" else np.nan
    if pd.isna(converted):
        row["해외가격 신호"] = f"비교 제외: {currency} 환율 미지원"
        return row
    gap = (converted / close - 1) * 100
    premium = (nxt / converted - 1) * 100 if pd.notna(nxt) else np.nan
    signal = "강한 해외가격 강세" if gap >= 1 else "해외가격 강세" if gap >= .5 else "해외가격 약세" if gap <= 0 else "중립"
    row.update({"해외24h 환산가": round(converted), "해외 괴리율%": round(gap, 2),
                "NXT 프리미엄%": round(premium, 2) if pd.notna(premium) else np.nan, "해외가격 신호": signal})
    return row
