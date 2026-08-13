"""Point-in-time daily chart structure analysis.

This module is intentionally independent from the legacy closing-trade score.
Every returned classification is accompanied by the raw values used to make it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StructureConfig:
    swing_window: int = 3
    swing_atr_multiple: float = 1.0
    swing_min_change_pct: float = 2.0
    slope_window: int = 5
    bb_period: int = 20
    bb_std: float = 2.0
    sideways_window: int = 10
    sideways_max_range_pct: float = 7.0
    cloud_large_gap_pct: float = 4.0


def _number(value, digits=2):
    return round(float(value), digits) if pd.notna(value) and np.isfinite(value) else np.nan


def _slope(series, window):
    return (series / series.shift(window) - 1) * 100


def _enrich(frame, cfg):
    x = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    x = x.apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    for period in (5, 20, 60, 120):
        x[f"MA{period}"] = x["Close"].rolling(period).mean()
        x[f"MA{period}_SLOPE"] = _slope(x[f"MA{period}"], cfg.slope_window)

    previous = x["Close"].shift(1)
    tr = pd.concat([
        x["High"] - x["Low"], (x["High"] - previous).abs(),
        (x["Low"] - previous).abs(),
    ], axis=1).max(axis=1)
    x["ATR14"] = tr.rolling(14).mean()
    delta = x["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    x["RSI14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    ema12, ema26 = x["Close"].ewm(span=12, adjust=False).mean(), x["Close"].ewm(span=26, adjust=False).mean()
    x["MACD"] = ema12 - ema26
    x["MACD_SIGNAL"] = x["MACD"].ewm(span=9, adjust=False).mean()
    x["MACD_HIST"] = x["MACD"] - x["MACD_SIGNAL"]

    tenkan = (x["High"].rolling(9).max() + x["Low"].rolling(9).min()) / 2
    kijun = (x["High"].rolling(26).max() + x["Low"].rolling(26).min()) / 2
    future_a = (tenkan + kijun) / 2
    future_b = (x["High"].rolling(52).max() + x["Low"].rolling(52).min()) / 2
    x["TENKAN"], x["KIJUN"] = tenkan, kijun
    x["SPAN_A"], x["SPAN_B"] = future_a.shift(26), future_b.shift(26)
    x["FUTURE_SPAN_A"], x["FUTURE_SPAN_B"] = future_a, future_b

    mid = x["Close"].rolling(cfg.bb_period).mean()
    std = x["Close"].rolling(cfg.bb_period).std(ddof=0)
    x["BB_MID"] = mid
    x["BB_UPPER"] = mid + cfg.bb_std * std
    x["BB_LOWER"] = mid - cfg.bb_std * std
    x["BB_WIDTH"] = (x["BB_UPPER"] - x["BB_LOWER"]) / mid.replace(0, np.nan) * 100
    x["BB_POSITION"] = (x["Close"] - x["BB_LOWER"]) / (x["BB_UPPER"] - x["BB_LOWER"]).replace(0, np.nan) * 100
    return x


def _swings(x, cfg):
    w = max(1, int(cfg.swing_window))
    high_mask = x["High"].eq(x["High"].rolling(2 * w + 1, center=True).max())
    low_mask = x["Low"].eq(x["Low"].rolling(2 * w + 1, center=True).min())
    candidates = [(i, "H", float(x["High"].iloc[i])) for i in np.flatnonzero(high_mask.fillna(False))]
    candidates += [(i, "L", float(x["Low"].iloc[i])) for i in np.flatnonzero(low_mask.fillna(False))]
    candidates.sort()
    accepted = []
    for pos, kind, price in candidates:
        if accepted and accepted[-1][1] == kind:
            if (kind == "H" and price > accepted[-1][2]) or (kind == "L" and price < accepted[-1][2]):
                accepted[-1] = (pos, kind, price)
            continue
        if accepted:
            prev_price = accepted[-1][2]
            atr = x["ATR14"].iloc[pos]
            move = abs(price - prev_price)
            required = max(
                prev_price * cfg.swing_min_change_pct / 100,
                (float(atr) * cfg.swing_atr_multiple) if pd.notna(atr) else 0,
            )
            if move < required:
                continue
        accepted.append((pos, kind, price))
    highs = [v for v in accepted if v[1] == "H"]
    lows = [v for v in accepted if v[1] == "L"]
    recent_h, previous_h = (highs[-1] if highs else None), (highs[-2] if len(highs) > 1 else None)
    recent_l, previous_l = (lows[-1] if lows else None), (lows[-2] if len(lows) > 1 else None)
    return {
        "최근고점": recent_h[2] if recent_h else np.nan,
        "이전고점": previous_h[2] if previous_h else np.nan,
        "최근고점봉": recent_h[0] if recent_h else None,
        "최근저점": recent_l[2] if recent_l else np.nan,
        "이전저점": previous_l[2] if previous_l else np.nan,
        "최근저점봉": recent_l[0] if recent_l else None,
        "Higher High": bool(recent_h and previous_h and recent_h[2] > previous_h[2]),
        "Lower High": bool(recent_h and previous_h and recent_h[2] < previous_h[2]),
        "Higher Low": bool(recent_l and previous_l and recent_l[2] > previous_l[2]),
        "Lower Low": bool(recent_l and previous_l and recent_l[2] < previous_l[2]),
    }


def _bb_rebound(x, swings):
    recent = x.tail(40)
    touch = recent["Low"] <= recent["BB_LOWER"] * 1.01
    touches = np.flatnonzero(touch.fillna(False))
    if not len(touches):
        return "신호 없음", False
    start = int(touches[-1])
    after = recent.iloc[start:]
    reentry = np.flatnonzero((after["Close"] > after["BB_LOWER"]).fillna(False))
    if not len(reentry):
        return "BB하한 접근", False
    recovered = after.iloc[int(reentry[0]):]
    mid_recovery = bool((recovered["Close"] > recovered["BB_MID"]).any())
    if not mid_recovery:
        return "밴드 내부 재진입", False
    if swings["Higher Low"]:
        return "BB하한 반등 확인", True
    return "BB중간 회복", False


def analyze_daily_structure(frame, config=None):
    cfg = config if isinstance(config, StructureConfig) else StructureConfig(**(config or {}))
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame is None or not required.issubset(frame.columns) or len(frame) < 120:
        return {"차트구조": "데이터 부족", "차트 구조 점수": np.nan, "구조판정근거": "일봉 120개 이상 필요"}
    x = _enrich(frame, cfg)
    r = x.iloc[-1]
    swings = _swings(x, cfg)
    close = float(r["Close"])
    cloud_top, cloud_bottom = max(r["SPAN_A"], r["SPAN_B"]), min(r["SPAN_A"], r["SPAN_B"])
    if pd.isna(cloud_top):
        cloud_position, cloud_gap = "데이터 없음", np.nan
    elif close > cloud_top:
        cloud_position, cloud_gap = "구름 위", (close / cloud_top - 1) * 100
    elif close < cloud_bottom:
        cloud_position, cloud_gap = "구름 아래", (close / cloud_bottom - 1) * 100
    else:
        cloud_position, cloud_gap = "구름 내부", 0.0
    above_cloud = x["Close"] > x[["SPAN_A", "SPAN_B"]].max(axis=1)
    transitions = above_cloud & ~above_cloud.shift(1, fill_value=False)
    breakout_positions = np.flatnonzero(transitions.fillna(False))
    bars_since_breakout = len(x) - 1 - int(breakout_positions[-1]) if len(breakout_positions) else np.nan

    returns = x["Close"].pct_change()
    recent20 = x.tail(20)
    up_avg = recent20.loc[returns.reindex(recent20.index) > 0, "Volume"].mean()
    down_avg = recent20.loc[returns.reindex(recent20.index) < 0, "Volume"].mean()
    vol5, vol20 = x["Volume"].tail(5).mean(), x["Volume"].tail(20).mean()
    healthy_volume = bool(pd.notna(up_avg) and pd.notna(down_avg) and up_avg > down_avg and vol5 <= vol20 * 1.35)
    bb_state, bb_confirmed = _bb_rebound(x, swings)
    range_pct = (x["High"].tail(cfg.sideways_window).max() / x["Low"].tail(cfg.sideways_window).min() - 1) * 100
    ma5_up, ma20_up = r["MA5_SLOPE"] > 0, r["MA20_SLOPE"] > 0
    short_break = close < r["MA5"] and close < r["MA20"]
    large_gap = cloud_position == "구름 위" and cloud_gap >= cfg.cloud_large_gap_pct
    if large_gap and swings["Higher Low"] and ma5_up and ma20_up and healthy_volume:
        cloud_state = "강한 상승 지속형"
    elif cloud_position == "구름 위" and swings["Higher Low"] and range_pct <= cfg.sideways_max_range_pct:
        cloud_state = "시간조정형"
    elif large_gap and (not swings["Higher Low"] or short_break) and down_avg > up_avg:
        cloud_state = "가격조정형"
    else:
        cloud_state = "해당 없음"

    bullish_ma = close > r["MA20"] > r["MA60"] and ma20_up
    bearish_ma = close < r["MA20"] < r["MA60"] and r["MA20_SLOPE"] < 0
    near_high = pd.notna(swings["최근고점"]) and close >= swings["최근고점"] * .9
    if bullish_ma and swings["Higher High"] and swings["Higher Low"]:
        structure = "상승추세"
    elif bullish_ma and near_high and short_break and swings["Higher Low"]:
        structure = "상승 후 고가조정"
    elif bullish_ma and near_high and range_pct <= cfg.sideways_max_range_pct:
        structure = "상승 후 시간조정"
    elif bearish_ma and swings["Lower High"] and swings["Lower Low"] and close > r["MA5"]:
        structure = "하락추세 속 기술적 반등"
    elif bearish_ma and swings["Lower Low"]:
        structure = "하락추세"
    elif bb_confirmed or (swings["Higher Low"] and close > r["MA20"] and r["MA5_SLOPE"] > 0):
        structure = "바닥 반등 초기"
    elif not swings["Lower Low"] and abs(r["MA20_SLOPE"]) < 1 and r["RSI14"] < 50:
        structure = "바닥 형성"
    else:
        structure = "방향 불명확"

    raw = {
        "종가": _number(close), "최근고점": _number(swings["최근고점"]), "이전고점": _number(swings["이전고점"]),
        "최근저점": _number(swings["최근저점"]), "이전저점": _number(swings["이전저점"]),
        **{k: swings[k] for k in ("Higher High", "Higher Low", "Lower High", "Lower Low")},
        **{f"MA{n}": _number(r[f"MA{n}"]) for n in (5, 20, 60, 120)},
        **{f"MA{n}기울기%": _number(r[f"MA{n}_SLOPE"]) for n in (5, 20, 60, 120)},
        "RSI14": _number(r["RSI14"]), "MACD": _number(r["MACD"], 4),
        "MACD시그널": _number(r["MACD_SIGNAL"], 4), "MACD히스토그램": _number(r["MACD_HIST"], 4),
        "선행스팬1": _number(r["SPAN_A"]), "선행스팬2": _number(r["SPAN_B"]),
        "구름상단": _number(cloud_top), "구름하단": _number(cloud_bottom),
        "구름두께%": _number((cloud_top / cloud_bottom - 1) * 100 if pd.notna(cloud_bottom) and cloud_bottom else np.nan),
        "구름이격률%": _number(cloud_gap), "미래구름": "상승" if r["FUTURE_SPAN_A"] >= r["FUTURE_SPAN_B"] else "하락",
        "최근구름돌파": bool(pd.notna(bars_since_breakout) and bars_since_breakout <= 20), "구름돌파후봉수": bars_since_breakout,
        "BB상한": _number(r["BB_UPPER"]), "BB중간": _number(r["BB_MID"]), "BB하한": _number(r["BB_LOWER"]),
        "BB폭%": _number(r["BB_WIDTH"]), "BB내위치%": _number(r["BB_POSITION"]),
        "상승일평균거래량": _number(up_avg, 0), "하락일평균거래량": _number(down_avg, 0),
        "5일평균거래량": _number(vol5, 0), "20일평균거래량": _number(vol20, 0),
        "거래량증가율%": _number((vol5 / vol20 - 1) * 100 if vol20 else np.nan),
        "건전한매물소화": healthy_volume,
    }
    reasons = (
        f"HH={swings['Higher High']}, HL={swings['Higher Low']}, LH={swings['Lower High']}, LL={swings['Lower Low']}; "
        f"종가/MA20/MA60={close:.2f}/{r['MA20']:.2f}/{r['MA60']:.2f}; "
        f"MA5·20기울기={r['MA5_SLOPE']:.2f}%/{r['MA20_SLOPE']:.2f}%; "
        f"구름={cloud_position}({cloud_gap:.2f}%); BB={bb_state}; 거래량구조={'건전' if healthy_volume else '미확인'}"
    )
    return {
        "차트구조": structure, "구름위치": cloud_position, "구름이격상태": cloud_state,
        "HL/HH": "/".join(label for label, yes in (("HL", swings["Higher Low"]), ("HH", swings["Higher High"])) if yes) or "없음",
        "BB상태": bb_state, "거래량구조": "상승증가·조정감소" if healthy_volume else "확인 안 됨",
        "구조판정근거": reasons, "구조원본값": raw, "구조설정": asdict(cfg),
        **raw,
    }
