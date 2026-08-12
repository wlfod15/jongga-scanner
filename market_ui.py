"""Market dashboard calculations and investor-friendly display helpers."""
import numpy as np
import pandas as pd


# Daily percentage thresholds. Kept separate so they can be calibrated later.
MARKET_IMPACT_THRESHOLDS = {
    "VIX": 3.0,
    "원/달러": 0.3,
    "나스닥100 선물": 0.3,
    "미국10년물": 1.0,
    "WTI": 3.0,
    "SOX": 0.3,
    "KOSPI": 0.3,
    "KOSDAQ": 0.3,
}

INVERSE_INDICATORS = {"VIX", "원/달러", "미국10년물"}
DIRECTIONAL_INDICATORS = {"나스닥100 선물", "SOX", "KOSPI", "KOSDAQ"}


def extract_close(frame, ticker=None):
    """Return the intended Close series from flat or yfinance MultiIndex data."""
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    if not isinstance(frame.columns, pd.MultiIndex):
        return pd.to_numeric(frame["Close"], errors="coerce") if "Close" in frame.columns else pd.Series(dtype=float)
    close = None
    for level in range(frame.columns.nlevels):
        if "Close" in frame.columns.get_level_values(level):
            close = frame.xs("Close", axis=1, level=level)
            break
    if close is None:
        return pd.Series(dtype=float)
    if isinstance(close, pd.Series):
        return pd.to_numeric(close, errors="coerce")
    if ticker and ticker in close.columns:
        return pd.to_numeric(close[ticker], errors="coerce")
    if close.shape[1] == 1:
        return pd.to_numeric(close.iloc[:, 0], errors="coerce")
    return pd.Series(dtype=float)  # Ambiguous data must not silently pick a ticker.


def last_metrics(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return {"현재": np.nan, "전일대비": np.nan, "5일 누적": np.nan}
    five_day = (s.iloc[-1] / s.iloc[-6] - 1) * 100 if len(s) >= 6 else np.nan
    return {"현재": float(s.iloc[-1]),
            "전일대비": float((s.iloc[-1] / s.iloc[-2] - 1) * 100),
            "5일 누적": float(five_day)}


def market_impact(indicator, daily_change):
    if pd.isna(daily_change):
        return "데이터 없음"
    threshold = MARKET_IMPACT_THRESHOLDS[indicator]
    change = float(daily_change)
    if indicator == "WTI":
        return "부정" if abs(change) >= threshold else "중립"
    if abs(change) < threshold:
        return "중립"
    if indicator in INVERSE_INDICATORS:
        return "긍정" if change < 0 else "부정"
    if indicator in DIRECTIONAL_INDICATORS:
        return "긍정" if change > 0 else "부정"
    return "중립"


def format_change(value):
    if pd.isna(value):
        return "데이터 없음"
    value = float(value)
    return f"▲ {value:+.2f}%" if value > 0 else f"▼ {value:+.2f}%" if value < 0 else "- 0.00%"


def market_table(data):
    return pd.DataFrame([{
        "지표": indicator,
        "현재": values.get("현재", np.nan),
        "전일대비": format_change(values.get("전일대비", np.nan)),
        "5일 누적": format_change(values.get("5일 누적", np.nan)),
        "증시영향": market_impact(indicator, values.get("전일대비", np.nan)),
    } for indicator, values in data.items()])


def impact_groups(data):
    groups = {"긍정": [], "부정": [], "중립": [], "데이터 없음": []}
    for indicator, values in data.items():
        impact = market_impact(indicator, values.get("전일대비", np.nan))
        change = values.get("전일대비", np.nan)
        text = indicator if pd.isna(change) else f"{indicator} {float(change):+.2f}%"
        groups[impact].append(text)
    return groups


def legacy_market_score(data):
    """Preserve the v5 scoring model while the presentation layer evolves."""
    score, reasons = 50, []
    vix = data["VIX"]
    if pd.notna(vix["현재"]):
        if vix["현재"] < 18: score += 12; reasons.append("VIX 안정")
        elif vix["현재"] < 25: score += 5
        elif vix["현재"] < 30: score -= 8; reasons.append("VIX 경계")
        else: score -= 15; reasons.append("VIX 고위험")
        if pd.notna(vix["전일대비"]) and vix["전일대비"] >= 10: score -= 10; reasons.append("VIX 급등")
    fx = data["원/달러"]
    if pd.notna(fx["전일대비"]):
        if fx["전일대비"] <= -.3: score += 6; reasons.append("원화 강세")
        elif fx["전일대비"] >= 1: score -= 10; reasons.append("원/달러 급등")
        elif fx["전일대비"] >= .5: score -= 5
    nq = data["나스닥100 선물"]
    if pd.notna(nq["전일대비"]):
        if nq["전일대비"] >= 1: score += 12; reasons.append("나스닥 선물 강세")
        elif nq["전일대비"] >= .3: score += 7
        elif nq["전일대비"] <= -1: score -= 15; reasons.append("나스닥 선물 약세")
        elif nq["전일대비"] <= -.3: score -= 7
    tnx = data["미국10년물"]
    if pd.notna(tnx["전일대비"]):
        if tnx["전일대비"] >= 3: score -= 7; reasons.append("미국 10년물 급등")
        elif tnx["전일대비"] <= -3: score += 4
    for key in ("KOSPI", "KOSDAQ"):
        item = data[key]
        if pd.notna(item["전일대비"]):
            score += 5 if item["전일대비"] >= 1 else (-5 if item["전일대비"] <= -1 else 0)
        score += 3 if item.get("20일선상") is True else (-3 if item.get("20일선상") is False else 0)
    if pd.notna(data["WTI"]["전일대비"]) and abs(data["WTI"]["전일대비"]) >= 5:
        score -= 3; reasons.append("유가 변동성 확대")
    score = int(np.clip(score, 0, 100))
    label = "우호" if score >= 75 else "보통" if score >= 55 else "주의" if score >= 40 else "고위험"
    return score, label, reasons
