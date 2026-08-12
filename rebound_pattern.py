import numpy as np
import pandas as pd


LEVEL_RATIOS = {"33": 1.33, "50": 1.50, "100": 2.00}


def krx_tick_size(price):
    price = float(price)
    if price < 2_000: return 1
    if price < 5_000: return 5
    if price < 20_000: return 10
    if price < 50_000: return 50
    if price < 200_000: return 100
    if price < 500_000: return 500
    return 1_000


def round_to_tick(price):
    tick = krx_tick_size(price)
    return int(round(float(price) / tick) * tick)


def normalize_intraday(frame):
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [c[0] if c[0] in ("Open", "High", "Low", "Close", "Volume") else c[-1] for c in data.columns]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(column in data.columns for column in required):
        return pd.DataFrame()
    data = data[required].apply(pd.to_numeric, errors="coerce").dropna(subset=["High", "Low", "Close"])
    return data[~data.index.duplicated(keep="last")].sort_index()


def swing_points(frame, window=3):
    data = normalize_intraday(frame)
    if len(data) < window * 2 + 20:
        return [], []
    high_roll = data["High"].rolling(window * 2 + 1, center=True).max()
    low_roll = data["Low"].rolling(window * 2 + 1, center=True).min()
    highs = [(i, data.index[i], float(data["High"].iloc[i])) for i in range(window, len(data) - window)
             if data["High"].iloc[i] == high_roll.iloc[i]]
    lows = [(i, data.index[i], float(data["Low"].iloc[i])) for i in range(window, len(data) - window)
            if data["Low"].iloc[i] == low_roll.iloc[i]]
    return highs, lows


def _first_hit(data, start_pos, level):
    future = data.iloc[start_pos + 1:]
    hits = future[future["High"] >= level]
    return None if hits.empty else hits.index[0]


def _excursions_before(data, start_pos, end_time, base):
    future = data.iloc[start_pos + 1:]
    if end_time is not None:
        future = future.loc[:end_time]
    if future.empty:
        return np.nan, np.nan
    mae = (float(future["Low"].min()) / base - 1) * 100
    mfe = (float(future["High"].max()) / base - 1) * 100
    return round(mae, 2), round(mfe, 2)


def pattern_stage(current, levels, approach_pct=3.0):
    base, l33, l50, l100 = levels["base"], levels["33"], levels["50"], levels["100"]
    near = lambda price: abs(current / price - 1) * 100 <= approach_pct
    if current < base * (1 - approach_pct / 100): return "반등 기준가 이탈"
    if abs(current / base - 1) <= .5 / 100: return "반등 기준가 터치"
    if near(base): return "반등 기준가 접근"
    if current < l33:
        return "+33% 목표 접근" if near(l33) else "반등가 상향 돌파"
    if current < l50:
        return "+50% 목표 접근" if near(l50) else "+33% 돌파"
    if current < l100:
        return "+100% 목표 접근" if near(l100) else "+50% 돌파"
    return "+100% 돌파"


def analyze_rebound_pattern(frame, approach_pct=3.0, swing_window=3, min_swing_pct=15.0):
    data = normalize_intraday(frame)
    highs, lows = swing_points(data, swing_window)
    if not highs or not lows:
        return {"추정 패턴 상태": "60분봉 표본 부족"}

    tr = pd.concat([(data["High"] - data["Low"]),
                    (data["High"] - data["Close"].shift()).abs(),
                    (data["Low"] - data["Close"].shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if pd.notna(tr.rolling(14).mean().iloc[-1]) else 0
    candidates = []
    for high_pos, high_time, high in highs:
        later_lows = [item for item in lows if item[0] > high_pos]
        if not later_lows: continue
        low_pos, low_time, low = min(later_lows, key=lambda item: item[2])
        decline_pct = (high / low - 1) * 100
        if decline_pct < min_swing_pct or high - low < atr * 3: continue
        symmetry_error = abs(low / (high * .5) - 1) * 100
        recency = low_pos / len(data)
        score = min(100, 35 + min(decline_pct, 60) * .6 + max(0, 25 - symmetry_error) + recency * 10)
        candidates.append((low_pos, score, high_pos, high_time, high, low_time, low, symmetry_error, decline_pct))
    if not candidates:
        return {"추정 패턴 상태": "의미 있는 스윙 없음"}

    low_pos, score, high_pos, high_time, high, low_time, base, symmetry_error, decline_pct = max(
        candidates, key=lambda item: (item[0], item[1]))
    levels = {"base": round_to_tick(base), **{key: round_to_tick(base * ratio) for key, ratio in LEVEL_RATIOS.items()}}
    current = float(data["Close"].iloc[-1])
    targets = [("33", levels["33"]), ("50", levels["50"]), ("100", levels["100"])]
    next_level = next((price for _, price in targets if current < price), levels["100"])
    result = {
        "추정 패턴 상태": "산출", "추정 패턴 점수": round(score, 1),
        "기준 스윙 고점": round_to_tick(high), "기준 스윙 저점": round_to_tick(base),
        "기준 스윙 고점시간": str(high_time), "기준가 결정시간": str(low_time),
        "고점-50% 대응오차%": round(symmetry_error, 2), "기준 파동 하락폭%": round(decline_pct, 2),
        "추정 반등가": levels["base"], "+33% 가격": levels["33"], "+50% 가격": levels["50"], "+100% 가격": levels["100"],
        "반등가 거리(%)": round((current / levels["base"] - 1) * 100, 1),
        "현재 패턴 단계": pattern_stage(current, levels, approach_pct),
        "다음 목표가": next_level, "다음 목표까지(%)": round((next_level / current - 1) * 100, 1),
        "기준가 이탈 여부": bool(float(data["Low"].iloc[low_pos + 1:].min()) < levels["base"]) if low_pos + 1 < len(data) else False,
        "패턴 거래량 증가": bool(data["Volume"].iloc[-1] >= data["Volume"].rolling(20).mean().iloc[-1] * 1.5),
    }
    for label, level in targets:
        hit_time = _first_hit(data, low_pos, level)
        mae, mfe = _excursions_before(data, low_pos, hit_time, levels["base"])
        result.update({f"+{label}% 도달 여부": hit_time is not None,
                       f"+{label}% 최초 도달시간": None if hit_time is None else str(hit_time),
                       f"+{label}% 도달전 MAE%": mae, f"+{label}% 도달전 MFE%": mfe})
    return result


def pattern_filter_mask(frame, choice, approach_pct=3.0):
    if choice == "전체": return pd.Series(True, index=frame.index)
    numeric = lambda column: pd.to_numeric(frame[column], errors="coerce") if column in frame else pd.Series(np.nan, index=frame.index)
    distance = numeric("반등가 거리(%)")
    stage = frame.get("현재 패턴 단계", pd.Series("", index=frame.index)).astype(str)
    upside = numeric("다음 목표까지(%)")
    volume = frame.get("패턴 거래량 증가", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    if choice == "반등가 ±3% 종목": return distance.abs() <= approach_pct
    if choice == "반등가 상향 돌파 종목": return distance > 0
    if choice == "+33%선 돌파 종목": return stage.isin(["+33% 돌파", "+50% 목표 접근", "+50% 돌파", "+100% 목표 접근", "+100% 돌파"])
    if choice == "+50%선 돌파 종목": return stage.isin(["+50% 돌파", "+100% 목표 접근", "+100% 돌파"])
    if choice == "다음 목표가까지 상승여력 10% 이상": return upside >= 10
    if choice == "거래량 증가 + 반등선 돌파": return volume & (distance > 0)
    if choice == "거래량 증가 + 33%선 돌파": return volume & stage.isin(["+33% 돌파", "+50% 목표 접근", "+50% 돌파", "+100% 목표 접근", "+100% 돌파"])
    return pd.Series(True, index=frame.index)
