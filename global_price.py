"""After-hours reference prices for KRX stocks.

Depositary receipts are converted only when the underlying-share ratio is
verified from an issuer, depositary or regulatory filing. Hyperliquid HIP-3
perpetuals are supported as a secondary reference only when an exact product
mapping is configured; they are never presented as a cash-equity price.
"""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf


MAP_COLUMNS = [
    "krx_code", "ticker", "provider", "currency", "product_type", "ratio",
    "verified_ratio", "display_name", "source_url", "dex", "note",
]


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
            frame = yf.download(
                ticker, period=period, interval=interval, progress=False,
                auto_adjust=False, threads=False,
            )
            close = frame["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close):
                return float(close.iloc[-1])
        except Exception:
            continue
    return np.nan


def _hyperliquid_mark(ticker, dex=""):
    """Return the exact HIP-3 perpetual mark price, never a fuzzy match."""
    try:
        body = {"type": "metaAndAssetCtxs"}
        if dex:
            body["dex"] = dex
        response = requests.post(
            "https://api.hyperliquid.xyz/info", json=body, timeout=8,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        meta, contexts = response.json()
        for asset, context in zip(meta.get("universe", []), contexts):
            if str(asset.get("name", "")).upper() == str(ticker).upper():
                return float(context["markPx"])
    except Exception:
        pass
    return np.nan


def usdkrw_quote():
    value = _latest_yahoo("KRW=X")
    return {
        "USD/KRW": value,
        "환율시각UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def nxt_delayed_quotes():
    """Official NXT delayed quote snapshot from its market-data JSON endpoint."""
    session = requests.Session()
    try:
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://nextrade.co.kr/menu/marketData/menuList.do",
            "X-Requested-With": "XMLHttpRequest",
        })
        session.get("https://nextrade.co.kr/menu/marketData/menuList.do", timeout=8)
        response = session.post(
            "https://nextrade.co.kr/brdinfoTime/brdinfoTimeList.do",
            data={"pageIndex": 1, "pageUnit": 1000}, timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}

    result = {}
    for item in payload.get("brdinfoTimeList", []):
        code = "".join(ch for ch in str(item.get("isuSrdCd", "")) if ch.isdigit()).zfill(6)
        price = pd.to_numeric(str(item.get("curPrc", "")).replace(",", ""), errors="coerce")
        if len(code) == 6 and pd.notna(price) and float(price) > 0:
            result[code] = float(price)
    return result


def _empty_snapshot(code, close, fx_quote, nxt_quotes):
    nxt = nxt_quotes.get(code, np.nan)
    return {
        "KRX 종가": round(close),
        "NXT 현재가": nxt,
        "해외24h 환산가": np.nan,
        "해외 괴리율%": np.nan,
        "NXT 프리미엄%": np.nan,
        "해외가격 신호": "데이터 없음",
        "해외상품 유형": "매핑 없음",
        "해외상품명": "매핑 없음",
        "해외가격 출처": "데이터 없음",
        "해외 원가격": np.nan,
        "해외 통화": None,
        "원주환산비율": np.nan,
        "USD/KRW": fx_quote.get("USD/KRW", np.nan),
        "해외가격 시각UTC": None,
    }


def after_hours_snapshot(krx_code, krx_close, product_map, fx_quote, nxt_quotes):
    code = str(krx_code).zfill(6)
    close = float(krx_close)
    row = _empty_snapshot(code, close, fx_quote, nxt_quotes)
    matches = product_map[product_map["krx_code"] == code]
    if matches.empty:
        row["해외가격 신호"] = "해외 대응상품 매핑 없음"
        return row

    # CSV order is the priority: official DR first, exact HIP-3 perp second.
    failures = []
    for _, product in matches.iterrows():
        verified = str(product.get("verified_ratio", "false")).lower() in {"1", "true", "yes", "y"}
        ratio = pd.to_numeric(product.get("ratio", np.nan), errors="coerce")
        kind = str(product.get("product_type", "unverified")).lower()
        name = str(product.get("display_name", product.get("ticker", "해외상품")))
        provider = str(product.get("provider", "")).lower()
        if not verified or pd.isna(ratio) or ratio <= 0:
            failures.append(f"{name}: 환산비율 미검증")
            continue

        ticker = str(product.get("ticker", ""))
        if provider == "hyperliquid":
            raw = _hyperliquid_mark(ticker, str(product.get("dex", "") or ""))
            source = "Hyperliquid 무기한선물 마크가격"
        elif provider == "yahoo":
            raw = _latest_yahoo(ticker)
            source = "해외 거래소 DR 시세(Yahoo Finance 수신)"
        else:
            failures.append(f"{name}: 지원하지 않는 공급자")
            continue

        currency = str(product.get("currency", "USD")).upper()
        fx = float(fx_quote.get("USD/KRW", np.nan))
        if pd.isna(raw):
            failures.append(f"{name}: 시세 수신 실패")
            continue
        if currency == "USD" and pd.isna(fx):
            failures.append(f"{name}: 원/달러 환율 수신 실패")
            continue
        currency_value = raw * fx if currency == "USD" else raw if currency == "KRW" else np.nan
        if pd.isna(currency_value):
            failures.append(f"{name}: {currency} 환율 미지원")
            continue

        # ratio = original KRX shares represented by one overseas unit.
        converted = currency_value / float(ratio)
        gap = (converted / close - 1) * 100
        nxt = row["NXT 현재가"]
        premium = (nxt / converted - 1) * 100 if pd.notna(nxt) else np.nan
        signal = "강한 해외가격 강세" if gap >= 1 else "해외가격 강세" if gap >= .5 else "해외가격 약세" if gap <= 0 else "중립"
        row.update({
            "해외24h 환산가": round(converted),
            "해외 괴리율%": round(gap, 2),
            "NXT 프리미엄%": round(premium, 2) if pd.notna(premium) else np.nan,
            "해외가격 신호": signal,
            "해외상품 유형": kind,
            "해외상품명": name,
            "해외가격 출처": source,
            "해외 원가격": raw,
            "해외 통화": currency,
            "원주환산비율": float(ratio),
            "해외가격 시각UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        return row

    row["해외가격 신호"] = " / ".join(failures) if failures else "데이터 없음"
    row["해외상품명"] = str(matches.iloc[0].get("display_name", "해외상품"))
    return row

