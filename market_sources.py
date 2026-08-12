from io import StringIO

import pandas as pd
import requests


NAVER_INVESTOR_URL = "https://finance.naver.com/item/frgn.naver?code={symbol}"


def _column_name(column):
    if isinstance(column, tuple):
        parts = [str(part).strip() for part in column if "Unnamed" not in str(part)]
        return " ".join(dict.fromkeys(parts))
    return str(column).strip()


def _number(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace("+", "", regex=False),
        errors="coerce",
    )


def parse_naver_investor_tables(tables, days=5):
    """Convert Naver's KRX investor table to estimated daily trading values.

    Naver exposes investor net trading as shares.  Multiplying each day's net
    shares by that day's close gives an explicitly labelled estimated value.
    """
    for raw in tables:
        names = {_column_name(column): column for column in raw.columns}
        date_name = next((name for name in names if "날짜" in name), None)
        close_name = next((name for name in names if "종가" in name), None)
        institution_name = next((name for name in names if "기관" in name), None)
        foreign_name = next((name for name in names if "외국인" in name and "보유" not in name), None)
        if not all((date_name, close_name, institution_name, foreign_name)):
            continue

        frame = pd.DataFrame({
            "날짜": pd.to_datetime(raw[names[date_name]], errors="coerce"),
            "종가": _number(raw[names[close_name]]),
            "기관순매매량": _number(raw[names[institution_name]]),
            "외국인순매매량": _number(raw[names[foreign_name]]),
        }).dropna(subset=["날짜", "종가"])
        if frame.empty:
            continue
        frame = frame.sort_values("날짜").tail(days).set_index("날짜")
        values = pd.DataFrame(index=frame.index)
        values["기관(추정금액)"] = frame["기관순매매량"] * frame["종가"]
        values["외국인(추정금액)"] = frame["외국인순매매량"] * frame["종가"]
        summary = {
            "기관5일순매수(억원)": round(values["기관(추정금액)"].sum() / 1e8, 1),
            "외국인5일순매수(억원)": round(values["외국인(추정금액)"].sum() / 1e8, 1),
            "수급출처": "Npay 증권(KRX 제공) · 순매매량×당일 종가 추정",
        }
        return summary, values
    return {}, pd.DataFrame()


def naver_investor_flow(symbol, days=5, timeout=12):
    response = requests.get(
        NAVER_INVESTOR_URL.format(symbol=str(symbol).zfill(6)),
        headers={"User-Agent": "Mozilla/5.0 (compatible; jongga-scanner/5.0)"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "euc-kr"
    return parse_naver_investor_tables(pd.read_html(StringIO(response.text)), days=days)
