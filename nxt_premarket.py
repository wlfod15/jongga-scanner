"""NXT premarket analytics from point-in-time intraday bars.

Fetching is deliberately separate: if actual 1/5-minute bars are unavailable,
callers must display 데이터 없음 rather than synthesising observations.
"""

import numpy as np
import pandas as pd

from daily_structure import StructureConfig, _swings


def _empty(reason="NXT 분봉 데이터 없음"):
    return {"NXT분석상태": "데이터 없음", "NXT데이터사유": reason, "NXT 프리장 점수": np.nan,
            "NXT갭": "데이터 없음", "NXT유지력": "데이터 없음", "NXT패턴": "데이터 없음"}


def five_minute_relative_volume(bars, historical_buckets=None):
    x = bars.copy()
    x.index = pd.to_datetime(x.index)
    x = x.between_time("08:00", "08:49")
    today = x["Volume"].resample("5min", origin="start_day").sum()
    if historical_buckets is None or len(historical_buckets) == 0:
        return pd.DataFrame({"거래량": today, "과거동시간평균": np.nan, "상대거래량": np.nan})
    history = historical_buckets.copy()
    history.index = pd.to_datetime(history.index)
    history["bucket"] = history.index.strftime("%H:%M")
    averages = history.groupby("bucket")["Volume"].mean()
    labels = today.index.strftime("%H:%M")
    mean = pd.Series(labels.map(averages), index=today.index, dtype=float)
    return pd.DataFrame({"거래량": today, "과거동시간평균": mean, "상대거래량": today / mean.replace(0, np.nan)})


def analyze_nxt_premarket(bars, previous_krx_close, historical_buckets=None, config=None):
    required = {"Open", "High", "Low", "Close", "Volume"}
    if bars is None or not required.issubset(getattr(bars, "columns", [])) or len(bars) < 3:
        return _empty()
    x = bars[list(required)].copy().apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    x.index = pd.to_datetime(x.index)
    x = x.between_time("08:00", "08:50")
    if x.empty or not previous_krx_close:
        return _empty("NXT 프리장 또는 전일 KRX 종가 없음")
    typical = (x["High"] + x["Low"] + x["Close"]) / 3
    cumulative_volume = x["Volume"].cumsum()
    vwap = (typical * x["Volume"]).cumsum() / cumulative_volume.replace(0, np.nan)
    current, high, low, open_price = float(x["Close"].iloc[-1]), float(x["High"].max()), float(x["Low"].min()), float(x["Open"].iloc[0])
    gap = (current / previous_krx_close - 1) * 100
    high_drop = (current / high - 1) * 100
    low_rebound = (current / low - 1) * 100
    retention = 100 if high == previous_krx_close else np.clip((current - previous_krx_close) / (high - previous_krx_close) * 100, 0, 120)
    swing_input = x.copy()
    prev = swing_input["Close"].shift(1)
    swing_input["ATR14"] = pd.concat([swing_input["High"] - swing_input["Low"], (swing_input["High"] - prev).abs(), (swing_input["Low"] - prev).abs()], axis=1).max(axis=1).rolling(3, min_periods=1).mean()
    cfg = StructureConfig(swing_window=1, swing_atr_multiple=.5, swing_min_change_pct=.3, **(config or {}))
    swings = _swings(swing_input, cfg)
    above_vwap = pd.notna(vwap.iloc[-1]) and current >= vwap.iloc[-1]
    after_830 = x.between_time("08:30", "08:50")
    after_840 = x.between_time("08:40", "08:50")
    hold830 = (current / after_830["Close"].iloc[0] - 1) * 100 if len(after_830) else np.nan
    hold840 = (current / after_840["Close"].iloc[0] - 1) * 100 if len(after_840) else np.nan
    rv = five_minute_relative_volume(x, historical_buckets)
    last_rv = rv["상대거래량"].dropna().iloc[-1] if rv["상대거래량"].notna().any() else np.nan
    score = 50 + np.clip(gap, -10, 10) * 2 + np.clip(high_drop, -10, 0) * 2
    score += 10 if above_vwap else -10
    score += 8 if swings["Higher Low"] else -4
    score += np.clip(hold830 if pd.notna(hold830) else 0, -5, 5) * 2
    score += 5 if pd.notna(last_rv) and last_rv >= 1 else 0
    score = round(float(np.clip(score, 0, 100)), 1)
    if high_drop <= -5 or gap <= 0:
        strength = "갭 반납"
    elif score >= 80:
        strength = "매우 강함"
    elif score >= 65:
        strength = "강함"
    elif score >= 48:
        strength = "보합 유지"
    else:
        strength = "약화"
    declining = x["Close"].diff().tail(3).mean() < 0
    volume_rising = x["Volume"].tail(3).mean() > x["Volume"].head(3).mean()
    if swings["Higher Low"] and above_vwap and high_drop > -3:
        pattern = "고가 시간조정"
    elif declining and not above_vwap and volume_rising:
        pattern = "가격조정"
    elif current >= high * .998 and above_vwap:
        pattern = "재상승"
    else:
        pattern = "방향 불명확"
    last10 = x.between_time("08:40", "08:50")
    close_strength = np.clip(50 + high_drop * 5 + (10 if above_vwap else -10) + (10 if swings["Higher Low"] else 0), 0, 100)
    return {
        "NXT분석상태": "산출", "전일 KRX 종가": previous_krx_close, "NXT 시가": open_price,
        "NXT 현재가": current, "NXT 고가": high, "NXT 저가": low, "NXT 거래량": float(x["Volume"].sum()),
        "NXT VWAP": round(float(vwap.iloc[-1]), 2) if pd.notna(vwap.iloc[-1]) else np.nan,
        "NXT 갭률%": round(gap, 2), "고점 대비 하락률%": round(high_drop, 2), "저점 대비 상승률%": round(low_rebound, 2),
        "NXT고점유지율%": round(float(retention), 1), "NXT Higher Low": swings["Higher Low"], "NXT Higher High": swings["Higher High"],
        "08:30이후유지율%": round(hold830, 2) if pd.notna(hold830) else np.nan,
        "08:40이후유지율%": round(hold840, 2) if pd.notna(hold840) else np.nan,
        "08:40가격": float(last10["Close"].iloc[0]) if len(last10) else np.nan,
        "08:45가격": float(last10.between_time("08:45", "08:50")["Close"].iloc[0]) if len(last10.between_time("08:45", "08:50")) else np.nan,
        "08:49또는마지막가격": current, "프리장 마감 강도": round(float(close_strength), 1),
        "NXT 프리장 점수": score, "NXT갭": f"{gap:+.2f}%", "NXT유지력": strength, "NXT패턴": pattern,
        "시간대별상대거래량": rv.reset_index().to_dict("records"),
    }
