"""Independent chart-structure score; never merged into the legacy buy score."""

import numpy as np


DEFAULT_WEIGHTS = {
    "higher_low": 14, "higher_high": 10, "cloud": 12, "cloud_breakout": 6,
    "moving_average": 16, "slopes": 10, "bb_rebound": 10, "macd": 8,
    "rsi": 5, "volume": 9,
}


def calculate_structure_score(features, weights=None):
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    score = 0.0
    score += w["higher_low"] if features.get("Higher Low") else 0
    score += w["higher_high"] if features.get("Higher High") else 0
    score += w["cloud"] if features.get("구름위치") == "구름 위" else w["cloud"] * .45 if features.get("구름위치") == "구름 내부" else 0
    score += w["cloud_breakout"] if features.get("최근구름돌파") else 0
    close = features.get("종가", np.nan)
    ma5, ma20, ma60, ma120 = (features.get(f"MA{n}", np.nan) for n in (5, 20, 60, 120))
    if all(np.isfinite(v) for v in (close, ma5, ma20, ma60, ma120)):
        score += w["moving_average"] * np.mean([close > ma5, ma5 > ma20, ma20 > ma60, ma60 > ma120])
    slopes = [features.get(f"MA{n}기울기%", np.nan) for n in (5, 20, 60, 120)]
    valid_slopes = [v for v in slopes if np.isfinite(v)]
    if valid_slopes:
        score += w["slopes"] * np.mean([v > 0 for v in valid_slopes])
    score += w["bb_rebound"] if features.get("BB상태") == "BB하한 반등 확인" else w["bb_rebound"] * .45 if features.get("BB상태") == "BB중간 회복" else 0
    macd, signal = features.get("MACD", np.nan), features.get("MACD시그널", np.nan)
    if np.isfinite(macd) and np.isfinite(signal) and macd > signal:
        score += w["macd"]
    rsi = features.get("RSI14", np.nan)
    if np.isfinite(rsi) and 45 <= rsi <= 70:
        score += w["rsi"]
    score += w["volume"] if features.get("건전한매물소화") else 0
    return round(float(np.clip(score, 0, sum(w.values()))) / sum(w.values()) * 100, 1)
