from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from global_price import after_hours_snapshot, load_product_map, nxt_delayed_quotes, usdkrw_quote
from market_sources import naver_investor_flow
from market_ui import extract_close, impact_groups, last_metrics, legacy_market_score, market_table
from night_futures import kospi200_night_quote

try:
    from pykrx import stock as krx_stock
    PYKRX_OK = True
except Exception:
    PYKRX_OK = False

try:
    import yfinance as yf
    YF_OK = True
except Exception:
    YF_OK = False


st.set_page_config(page_title="KRX 종가매매 스캐너 v5", layout="wide")
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] { background: #f6f8fb; }
.block-container { max-width: 1120px; }
/* Metric text must wrap instead of being clipped on narrow phone screens. */
[data-testid="stMetric"] {
  min-width: 0; background: #fff; border: 1px solid #e7eaf0;
  border-radius: 1rem; padding: 1rem 1.1rem; box-shadow: 0 2px 10px rgba(25,35,55,.035);
}
[data-testid="stMetricLabel"] p { white-space: normal; line-height: 1.25; }
[data-testid="stMetricValue"] { font-size: clamp(1.25rem, 4.5vw, 2.35rem); }
[data-testid="stMetricValue"] > div { white-space: normal; overflow-wrap: anywhere; line-height: 1.15; }
[data-testid="stMetricDelta"] { font-size: clamp(.78rem, 2.8vw, 1rem); }
.validation-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem; }
.validation-card { min-width: 0; background: #fff; border: 1px solid #e7eaf0; border-radius: 1rem; padding: 1rem 1.1rem; }
.validation-label { color: #555d6d; font-size: .95rem; margin-bottom: .45rem; }
.validation-value { font-size: clamp(1.35rem, 3vw, 2.15rem); font-weight: 650; line-height: 1.15; overflow-wrap: anywhere; }
.validation-sub { color: #697386; font-size: .9rem; margin-top: .4rem; }
@media (max-width: 640px) {
  .block-container { padding: 1rem .75rem 3rem; }
  h1 { font-size: 1.75rem !important; line-height: 1.2 !important; }
  h2, h3 { font-size: 1.25rem !important; line-height: 1.3 !important; }
  [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: .65rem; }
  [data-testid="column"] { flex: 1 1 100% !important; min-width: 0 !important; }
  [data-testid="stMetric"] { padding: .85rem 1rem; border-radius: .85rem; }
  [data-testid="stDataFrame"] { font-size: .78rem; }
  .validation-grid { grid-template-columns: 1fr; }
}
</style>
""", unsafe_allow_html=True)
st.title("KRX 종가매매 종목 스캐너 v5")
st.caption("시장·업종·수급·공매도·기술 신호와 과거 동일신호 통계를 한 화면에서 확인합니다.")


# ── 지표와 가격 데이터 ──────────────────────────────────────────────
def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def prep(df):
    required = ["Open", "High", "Low", "Close", "Volume"]
    if df is None or len(df) < 80 or not all(c in df.columns for c in required):
        return pd.DataFrame()
    x = df[required].dropna(subset=["Close"]).copy()
    for n in (5, 10, 20, 60):
        x[f"MA{n}"] = x["Close"].rolling(n).mean()
    x["VOL20"] = x["Volume"].rolling(20).mean()
    x["VALUE20"] = (x["Close"] * x["Volume"]).rolling(20).mean()
    x["RSI"] = calc_rsi(x["Close"])
    x["OBV"] = (np.sign(x["Close"].diff()).fillna(0) * x["Volume"]).cumsum()
    x["OBV10"] = x["OBV"].rolling(10).mean()
    spread = (x["High"] - x["Low"]).replace(0, np.nan)
    clv = (((x["Close"] - x["Low"]) - (x["High"] - x["Close"])) / spread).fillna(0).clip(-1, 1)
    x["CVDP"] = (clv * x["Volume"]).cumsum()
    x["CVDP10"] = x["CVDP"].rolling(10).mean()
    x["HIGH20"] = x["High"].shift(1).rolling(20).max()
    x["LOW10"] = x["Low"].shift(1).rolling(10).min()
    x["RET1"] = x["Close"].pct_change() * 100
    x["CLOSE_POS"] = ((x["Close"] - x["Low"]) / spread * 100).fillna(50).clip(0, 100)
    x["UPPER_WICK"] = ((x["High"] - np.maximum(x["Open"], x["Close"])) / spread * 100).fillna(0).clip(0, 100)
    prev_close = x["Close"].shift(1)
    true_range = pd.concat([(x["High"] - x["Low"]), (x["High"] - prev_close).abs(),
                            (x["Low"] - prev_close).abs()], axis=1).max(axis=1)
    x["ATR14"] = true_range.rolling(14).mean()
    x["ATR_PCT"] = x["ATR14"] / x["Close"] * 100
    x["VOL_RATIO"] = x["Volume"] / x["VOL20"]
    x["MA20_GAP"] = (x["Close"] / x["MA20"] - 1) * 100
    x["OBV_SLOPE"] = x["OBV"].diff(5) / x["Volume"].rolling(20).mean().replace(0, np.nan)
    x["CVDP_SLOPE"] = x["CVDP"].diff(5) / x["Volume"].rolling(20).mean().replace(0, np.nan)
    return x


@st.cache_data(ttl=900, show_spinner=False)
def yahoo_metric(ticker):
    if not YF_OK:
        return {"현재": np.nan, "전일대비": np.nan, "5일 누적": np.nan}
    try:
        x = yf.download(ticker, period="12d", interval="1d", progress=False,
                        auto_adjust=False, threads=False)
        return last_metrics(extract_close(x, ticker))
    except Exception:
        return {"현재": np.nan, "전일대비": np.nan, "5일 누적": np.nan}


@st.cache_data(ttl=900, show_spinner=False)
def index_metric(code):
    try:
        x = fdr.DataReader(code, (date.today() - timedelta(days=45)).isoformat())
        out = last_metrics(extract_close(x))
        out["20일선상"] = bool(x["Close"].iloc[-1] >= x["Close"].rolling(20).mean().iloc[-1]) if len(x) >= 20 else None
        return out
    except Exception:
        return {"현재": np.nan, "전일대비": np.nan, "5일 누적": np.nan, "20일선상": None}


def market_environment():
    data = {
        "VIX": yahoo_metric("^VIX"), "원/달러": yahoo_metric("KRW=X"),
        "나스닥100 선물": yahoo_metric("NQ=F"), "미국10년물": yahoo_metric("^TNX"),
        "WTI": yahoo_metric("CL=F"), "SOX": yahoo_metric("^SOX"),
        "KOSPI": index_metric("KS11"), "KOSDAQ": index_metric("KQ11"),
    }
    score, label, reasons = legacy_market_score(data)
    return score, label, data, reasons


def classify_sector(name, sector_text=""):
    text = f"{name} {sector_text}".lower()
    groups = {
        "반도체": ["반도체", "semiconductor", "하이닉스", "hpsp", "리노공업", "isc"],
        "성장·기술": ["소프트웨어", "software", "인터넷", "게임", "ai", "로봇", "플랫폼"],
        "에너지": ["정유", "에너지", "oil", "gas", "석유", "s-oil", "이노베이션"],
        "항공·운송": ["항공", "airline", "운송", "여행"],
    }
    return next((cat for cat, keys in groups.items() if any(k in text for k in keys)), "일반")


def asset_score(change, positive=True):
    if pd.isna(change): return 50
    x = change if positive else -change
    return 90 if x >= 2 else 80 if x >= 1 else 70 if x >= .3 else 55 if x > -.3 else 45 if x > -1 else 30 if x > -2 else 15


def sector_environment(name, sector_text, market_score, md):
    cat = classify_sector(name, sector_text)
    nq, sox = asset_score(md["나스닥100 선물"]["전일대비"]), asset_score(md["SOX"]["전일대비"])
    oil, oil_inv = asset_score(md["WTI"]["전일대비"]), asset_score(md["WTI"]["전일대비"], False)
    score = ({
        "반도체": .65 * sox + .20 * nq + .15 * market_score,
        "성장·기술": .75 * nq + .10 * sox + .15 * market_score,
        "에너지": .60 * oil + .40 * market_score,
        "항공·운송": .50 * oil_inv + .50 * market_score,
        "일반": .75 * market_score + .25 * nq,
    })[cat]
    return cat, int(np.clip(round(score), 0, 100))


@st.cache_data(ttl=900, show_spinner=False)
def benchmark(market, start):
    try:
        code = "KS11" if str(market).upper().startswith("KOSPI") else "KQ11"
        return fdr.DataReader(code, start)["Close"].pct_change() * 100
    except Exception:
        return pd.Series(dtype=float)


def row_features(df, market_ret, i, p):
    r, prev = df.iloc[i], df.iloc[i - 1]
    close = float(r["Close"])
    vr = float(r["Volume"] / r["VOL20"]) if pd.notna(r["VOL20"]) and r["VOL20"] else np.nan
    gap = (close / float(r["MA20"]) - 1) * 100
    aligned = market_ret.reindex(df.index)
    mret = float(aligned.iloc[i]) if len(aligned) and pd.notna(aligned.iloc[i]) else 0
    rel = float(r["RET1"]) - mret
    trend = bool(close > r["MA20"] and r["MA20"] >= r["MA60"])
    obv = bool(r["OBV"] > r["OBV10"] and r["OBV"] > prev["OBV"])
    cvd = bool(r["CVDP"] > r["CVDP10"] and r["CVDP"] > prev["CVDP"])
    breakout = bool(pd.notna(r["HIGH20"]) and close >= r["HIGH20"] * .995)
    pullback = bool(trend and close >= r["MA10"] * .985 and close <= r["MA20"] * 1.06 and r["CLOSE_POS"] >= 60 and r["RET1"] > 0)
    stype = "돌파형" if breakout else "눌림형" if pullback else "추세형"
    checks = [
        (r["VALUE20"] >= p["min_value"], f"거래대금 부족 ({r['VALUE20']/1e8:.1f}억 < {p['min_value']/1e8:.0f}억)"),
        (close >= p["min_price"], f"주가 부족 ({close:,.0f}원 < {p['min_price']:,.0f}원)"),
        (trend, "추세 미충족 (20일선·60일선)"), (obv, "OBV 미충족"),
        (vr >= p["min_vr"], f"거래량 부족 ({vr:.2f}배 < {p['min_vr']:.2f}배)"),
        (p["rsi_lo"] <= r["RSI"] <= p["rsi_hi"], f"RSI 범위 이탈 ({r['RSI']:.1f}, 기준 {p['rsi_lo']}~{p['rsi_hi']})"),
        (gap <= p["max_gap"], f"20일선 이격 초과 ({gap:.1f}% > {p['max_gap']}%)"),
        (r["CLOSE_POS"] >= p["close_pos"], f"종가위치 부족 ({r['CLOSE_POS']:.1f}% < {p['close_pos']}%)"),
        (r["UPPER_WICK"] <= p["max_wick"], f"윗꼬리 초과 ({r['UPPER_WICK']:.1f}% > {p['max_wick']}%)"),
        (rel >= p["min_rel"], f"시장대비강도 부족 ({rel:.2f}%p < {p['min_rel']:.2f}%p)"),
    ]
    failures = [reason for ok, reason in checks if not ok]
    risk = (12 if r["UPPER_WICK"] > 35 else 0) + (12 if gap > p["max_gap"] else 0) + (10 if r["RSI"] > 75 else 0) + (10 if r["RET1"] > 12 else 0)
    score = sum([10 if r["VALUE20"] >= p["min_value"] else 0, 15 if trend else 0, 10 if obv else 0,
                 8 if cvd else 0, 12 if vr >= p["min_vr"] else 0, 10 if p["rsi_lo"] <= r["RSI"] <= p["rsi_hi"] else 0,
                 12 if r["CLOSE_POS"] >= p["close_pos"] else 6 if r["CLOSE_POS"] >= 65 else 0,
                 8 if r["UPPER_WICK"] <= 25 else 0, 8 if rel >= p["min_rel"] else 0, 7 if breakout else 5 if pullback else 0])
    return {"score": int(np.clip(score - risk, 0, 100)), "hard": not failures, "failures": failures,
            "vr": vr, "gap": gap, "rel": rel, "obv": obv, "cvd": cvd, "type": stype,
            "close_pos": float(r["CLOSE_POS"]), "wick": float(r["UPPER_WICK"]), "risk": risk}


def trade_levels(df):
    r, entry = df.iloc[-1], float(df["Close"].iloc[-1])
    supports = [float(v) for v in (r.get("MA20"), r.get("LOW10")) if pd.notna(v) and float(v) < entry]
    stop = np.clip(max(supports) * .995 if supports else entry * .97, entry * .92, entry * .97)
    risk = entry - stop
    return {"진입가": round(entry), "초기손절": round(stop), "손절률%": round(risk / entry * 100, 2),
            "1차익절(+10%)": round(entry * 1.10), "2차익절(+20%)": round(entry * 1.20),
            "1차손익비R": round(entry * .10 / risk, 2), "2차손익비R": round(entry * .20 / risk, 2)}


PREDICTION_FEATURES = ["RSI", "CLOSE_POS", "UPPER_WICK", "VOL_RATIO", "MA20_GAP",
                       "ATR_PCT", "OBV_SLOPE", "CVDP_SLOPE", "RET1", "MKT_RET1", "MKT_RET5"]


def _weighted_rate(values, weights, condition):
    mask = condition(np.asarray(values, dtype=float))
    return float(np.average(mask.astype(float), weights=weights) * 100)


def similar_prediction(df, market_ret, horizon=5, min_samples=20, stop_pct=3.0):
    """Use only information known at each signal date; future rows are labels only."""
    base = df.copy()
    aligned = market_ret.reindex(base.index).fillna(0)
    base["MKT_RET1"] = aligned
    base["MKT_RET5"] = aligned.rolling(5).sum()
    current = base.iloc[-1]
    candidates = base.iloc[65:-(horizon + 1)].dropna(subset=PREDICTION_FEATURES).copy()
    if current[PREDICTION_FEATURES].isna().any() or len(candidates) < min_samples:
        return {"표본수": len(candidates), "예측상태": "표본 부족", "신뢰도": "표본 부족"}

    # Scale using the historical candidate pool only. The latest observation never
    # changes historical feature values, preventing look-ahead leakage.
    hist = candidates[PREDICTION_FEATURES].astype(float)
    center = hist.median()
    scale = (hist.quantile(.75) - hist.quantile(.25)).replace(0, np.nan)
    scale = scale.fillna(hist.std()).replace(0, 1).fillna(1)
    distance = (((hist - current[PREDICTION_FEATURES].astype(float)) / scale) ** 2).mean(axis=1) ** .5
    take = min(max(min_samples, int(len(distance) * .15)), 80)
    chosen = distance.nsmallest(take)
    if len(chosen) < min_samples:
        return {"표본수": len(chosen), "예측상태": "표본 부족", "신뢰도": "표본 부족"}

    outcomes = []
    for idx, dist in chosen.items():
        pos = base.index.get_loc(idx)
        entry = float(base["Close"].iloc[pos])
        nxt = base.iloc[pos + 1]
        future = base.iloc[pos + 1:pos + horizon + 1]
        outcomes.append({
            "distance": float(dist),
            "open": (float(nxt["Open"]) / entry - 1) * 100,
            "close": (float(nxt["Close"]) / entry - 1) * 100,
            "high": (float(nxt["High"]) / entry - 1) * 100,
            "low": (float(nxt["Low"]) / entry - 1) * 100,
            "horizon_close": (float(future["Close"].iloc[-1]) / entry - 1) * 100,
            "max_high": (float(future["High"].max()) / entry - 1) * 100,
            "min_low": (float(future["Low"].min()) / entry - 1) * 100,
        })
    out = pd.DataFrame(outcomes)
    weights = 1 / (out["distance"].to_numpy() + .15)
    weights /= weights.sum()
    q = lambda col, pct: float(out[col].quantile(pct))
    atr_pct = float(current["ATR_PCT"])
    open_low, open_high = min(q("open", .20), -atr_pct * .20), max(q("open", .80), atr_pct * .20)
    close_low, close_high = min(q("close", .20), -atr_pct * .45), max(q("close", .80), atr_pct * .45)
    expected_high, expected_low = max(q("high", .50), atr_pct * .55), min(q("low", .50), -atr_pct * .55)
    confidence = "높음" if len(out) >= 60 and out["distance"].median() <= 1.0 else "보통" if len(out) >= 35 else "낮음"
    return {
        "표본수": len(out), "예측상태": "산출", "신뢰도": confidence,
        "유사도중앙거리": round(float(out["distance"].median()), 2),
        "익일승률%": round(_weighted_rate(out["close"], weights, lambda x: x > 0), 1),
        "갭상승확률%": round(_weighted_rate(out["open"], weights, lambda x: x > 0), 1),
        f"{horizon}일내+3%도달%": round(_weighted_rate(out["max_high"], weights, lambda x: x >= 3), 1),
        "초기손절도달확률%": round(_weighted_rate(out["min_low"], weights, lambda x: x <= -abs(stop_pct)), 1),
        "예상시가하단%": round(open_low, 2), "예상시가상단%": round(open_high, 2),
        "예상종가하단%": round(close_low, 2), "예상종가상단%": round(close_high, 2),
        "예상고가%": round(expected_high, 2), "예상저가%": round(expected_low, 2),
        "예상시가평균%": round(float(np.average(out["open"], weights=weights)), 2),
        "익일평균%": round(float(np.average(out["close"], weights=weights)), 2),
        "익일표준편차%": round(float(out["close"].std(ddof=1)), 2),
        f"{horizon}일승률%": round(_weighted_rate(out["horizon_close"], weights, lambda x: x > 0), 1),
        f"{horizon}일평균%": round(float(np.average(out["horizon_close"], weights=weights)), 2),
        "평균MAE%": round(float(np.average(out["min_low"], weights=weights)), 2),
    }


def ranking_score(stock_score, market_score, sector_score, bt, rr):
    # 검증 전 설계 가중치: 기술 45%, 시장 15%, 업종 10%, 승률 15%, 손익비 10%, 표본 신뢰도 5%
    win = bt.get("5일승률%", np.nan); win = 50 if pd.isna(win) else win
    rr_score = np.clip(rr / 3 * 100, 0, 100)
    sample_score = np.clip(bt.get("표본수", 0) / 30 * 100, 0, 100)
    return round(.45 * stock_score + .15 * market_score + .10 * sector_score + .15 * win + .10 * rr_score + .05 * sample_score, 1)


def analyze(symbol, name, market, sector_text, start, p, do_bt, market_score, market_data):
    try:
        df = prep(fdr.DataReader(symbol, start))
        if len(df) < 80: return None
        mr = benchmark(market, start)
        f = row_features(df, mr, -1, p)
        cat, sector_score = sector_environment(name, sector_text, market_score, market_data)
        levels = trade_levels(df)
        bt = similar_prediction(df, mr, p["prediction_horizon"], p["min_prediction_samples"],
                                levels["손절률%"] if do_bt else 3.0) if do_bt else {
                                    "표본수": 0, "예측상태": "사용 안 함", "신뢰도": "-"}
        combined = round(.60 * f["score"] + .25 * market_score + .15 * sector_score, 1)
        decision = "매수후보" if f["hard"] and f["score"] >= p["min_score"] else "조건근접" if f["score"] >= p["min_score"] - 20 or len(f["failures"]) <= 3 else "제외"
        r = df.iloc[-1]
        out = {"종목코드": symbol, "종목명": name, "시장": market, "업종분류": cat, "날짜": df.index[-1].strftime("%Y-%m-%d"),
               "종가": round(float(r["Close"])), "등락률%": round(float(r["RET1"]), 2), "종목점수": f["score"],
               "시장환경": market_score, "업종환경": sector_score, "종합점수": combined, "판정": decision,
               "탈락사유": "없음" if not f["failures"] else " / ".join(f["failures"]), "유형": f["type"], "RSI14": round(float(r["RSI"]), 1),
               "거래량배수": round(f["vr"], 2), "종가위치%": round(f["close_pos"], 1), "윗꼬리%": round(f["wick"], 1),
               "시장대비강도%p": round(f["rel"], 2), "OBV": "충족" if f["obv"] else "미충족", "CVD Proxy": "충족" if f["cvd"] else "미충족"}
        out.update(bt); out.update(levels)
        out["최종순위점수"] = ranking_score(f["score"], market_score, sector_score, bt, levels["1차손익비R"])
        return out
    except Exception:
        return None


def validate_at_date(symbol, selected_date, market, p, lookback_days):
    """Recreate a forecast using data available at the selected close only."""
    start = (selected_date - timedelta(days=max(lookback_days * 2, 730))).isoformat()
    end = (selected_date + timedelta(days=14)).isoformat()
    raw = fdr.DataReader(symbol, start, end)
    full = prep(raw)
    if full.empty:
        return None
    eligible = full.index[pd.to_datetime(full.index).date <= selected_date]
    if not len(eligible):
        return None
    base_date = eligible[-1]
    base_pos = full.index.get_loc(base_date)
    if base_pos + 1 >= len(full):
        return {"상태": "실제 종가 대기", "기준일": pd.Timestamp(base_date).date()}
    history = full.iloc[:base_pos + 1].copy()
    market_history = benchmark(market, start).reindex(history.index)
    stop_pct = trade_levels(history)["손절률%"]
    prediction = similar_prediction(history, market_history, p["prediction_horizon"],
                                    p["min_prediction_samples"], stop_pct)
    actual_date = full.index[base_pos + 1]
    actual_open = float(full["Open"].iloc[base_pos + 1])
    actual_close = float(full["Close"].iloc[base_pos + 1])
    if prediction.get("예측상태") != "산출":
        return {"상태": "표본 부족", "기준일": pd.Timestamp(base_date).date(),
                "실제일": pd.Timestamp(actual_date).date(), "실제 시가": actual_open, "실제 종가": actual_close,
                "표본수": prediction.get("표본수", 0)}
    base_close = float(history["Close"].iloc[-1])
    predicted_open = base_close * (1 + float(prediction["예상시가평균%"]) / 100)
    predicted_close = base_close * (1 + float(prediction["익일평균%"]) / 100)
    open_difference = actual_open - predicted_open
    close_difference = actual_close - predicted_close
    return {"상태": "산출", "기준일": pd.Timestamp(base_date).date(),
            "실제일": pd.Timestamp(actual_date).date(),
            "예상 시가": predicted_open, "실제 시가": actual_open,
            "시가 차이": open_difference, "시가 차이율%": open_difference / predicted_open * 100,
            "예상 종가": predicted_close, "실제 종가": actual_close,
            "종가 차이": close_difference, "종가 차이율%": close_difference / predicted_close * 100,
            "상세": prediction}


# ── 종목 목록, 수급, 공매도 ─────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def listings():
    try:
        x = fdr.StockListing("KRX").copy()
    except Exception:
        x = pd.DataFrame()
    if x.empty and PYKRX_OK:
        try:
            tickers = krx_stock.get_market_ticker_list(market="ALL")
            x = pd.DataFrame({"Code": tickers, "Name": [krx_stock.get_market_ticker_name(t) for t in tickers]})
        except Exception:
            x = pd.DataFrame(columns=["Code", "Name"])
    code_col = next((c for c in ("Code", "Symbol") if c in x.columns), None)
    if not code_col or "Name" not in x.columns: return pd.DataFrame()
    x[code_col] = x[code_col].astype(str).str.zfill(6)
    return x.rename(columns={code_col: "Code"})


@st.cache_data(ttl=3600, show_spinner=False)
def overseas_product_map():
    return load_product_map()


@st.cache_data(ttl=300, show_spinner=False)
def current_fx_quote():
    return usdkrw_quote()


@st.cache_data(ttl=300, show_spinner=False)
def current_nxt_quotes():
    return nxt_delayed_quotes()


@st.cache_data(ttl=60, show_spinner=False)
def current_kospi200_night():
    try:
        return kospi200_night_quote()
    except Exception:
        return None


def enrich_after_hours(row):
    values = after_hours_snapshot(row["종목코드"], row["종가"], overseas_product_map(),
                                  current_fx_quote(), current_nxt_quotes())
    return {**row, **values}


def _date_range(days=25):
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return (now - timedelta(days=days)).strftime("%Y%m%d"), now.strftime("%Y%m%d")


@st.cache_data(ttl=1800, show_spinner=False)
def flow_and_short(symbol):
    summary, flow, short = {}, pd.DataFrame(), pd.DataFrame()
    start, end = _date_range()
    if PYKRX_OK:
        try:
            d = krx_stock.get_market_trading_value_by_date(start, end, symbol)
            if d is not None and len(d):
                flow = d.copy()
                foreign_col = next((c for c in d.columns if "외국인" in str(c)), None)
                inst_cols = [c for c in d.columns if any(k in str(c) for k in ("금융투자", "보험", "투신", "사모", "은행", "연기금", "기타금융"))]
                if foreign_col:
                    summary["외국인5일순매수(억원)"] = round(d[foreign_col].tail(5).sum() / 1e8, 1)
                if inst_cols:
                    summary["기관5일순매수(억원)"] = round(d[inst_cols].tail(5).sum().sum() / 1e8, 1)
                summary["수급출처"] = "KRX(pykrx)"
        except Exception:
            pass
        try:
            s = krx_stock.get_shorting_volume_by_date(start, end, symbol)
            if s is not None and len(s):
                short = s.copy()
                ratio_col = next((c for c in s.columns if "비중" in str(c)), None)
                vol_col = next((c for c in s.columns if str(c) in ("공매도", "공매도거래량") or ("공매도" in str(c) and "거래량" in str(c))), None)
                if ratio_col: summary["최근공매도비중%"] = round(float(s[ratio_col].iloc[-1]), 2)
                if vol_col: summary["공매도5일거래량"] = int(s[vol_col].tail(5).sum())
        except Exception:
            pass
        try:
            b = krx_stock.get_shorting_balance_by_date(start, end, symbol)
            if b is not None and len(b):
                ratio_col = next((c for c in b.columns if "비중" in str(c)), None)
                value_col = next((c for c in b.columns if str(c) in ("공매도금액", "공매도잔고금액") or ("잔고" in str(c) and "금액" in str(c))), None)
                if ratio_col: summary["공매도잔고비중%"] = round(float(b[ratio_col].iloc[-1]), 2)
                if value_col: summary["공매도잔고(억원)"] = round(float(b[value_col].iloc[-1]) / 1e8, 1)
        except Exception:
            pass
    if "외국인5일순매수(억원)" not in summary:
        try:
            fallback_summary, fallback_flow = naver_investor_flow(symbol)
            summary.update(fallback_summary)
            if len(fallback_flow):
                flow = fallback_flow
        except Exception:
            pass
    summary["공매도출처"] = "KRX 공개 통계" if any("공매도" in key and key != "공매도출처" for key in summary) else "KRX 응답 없음"
    return summary, flow, short


def chart(symbol, row):
    raw = fdr.DataReader(symbol, (date.today() - timedelta(days=140)).isoformat()).tail(80)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.04, row_heights=[.74, .26])
    fig.add_trace(go.Candlestick(x=raw.index, open=raw.Open, high=raw.High, low=raw.Low, close=raw.Close, name="OHLC"), row=1, col=1)
    colors = np.where(raw.Close >= raw.Open, "#e74c3c", "#3498db")
    fig.add_trace(go.Bar(x=raw.index, y=raw.Volume, marker_color=colors, name="거래량"), row=2, col=1)
    for label, col, color in (("진입가", "진입가", "#f1c40f"), ("초기손절", "초기손절", "#3498db"),
                               ("1차익절 +10%", "1차익절(+10%)", "#2ecc71"), ("2차익절 +20%", "2차익절(+20%)", "#9b59b6")):
        fig.add_hline(y=float(row[col]), line_dash="dash", line_color=color, annotation_text=label, row=1, col=1)
    fig.update_layout(height=650, xaxis_rangeslider_visible=False, legend_orientation="h", margin=dict(l=20, r=20, t=40, b=20))
    return fig


def show_detail(row):
    st.subheader(f"{row['종목명']} ({row['종목코드']}) 핵심 요약")
    with st.spinner("수급·공매도 데이터 확인 중..."):
        summary, flow, short = flow_and_short(row["종목코드"])

    foreign = summary.get("외국인5일순매수(억원)", np.nan)
    foreign_text = "조회 불가" if pd.isna(foreign) else f"{foreign:+,.1f}억원"
    short_ratio = summary.get("공매도잔고비중%", summary.get("최근공매도비중%", np.nan))
    short_text = "KRX 연결 필요" if pd.isna(short_ratio) else f"{short_ratio:.2f}%"
    sample_n = int(row.get("표본수", 0) or 0)
    prediction_ok = row.get("예측상태") == "산출"
    entry = float(row["진입가"])
    horizon = next((int(k.split("일내")[0]) for k in row.keys() if "일내+3%도달%" in k), 5)
    reach_key = f"{horizon}일내+3%도달%"

    def pct_price(pct):
        return entry * (1 + float(pct) / 100)

    def price_range(low_key, high_key):
        if not prediction_ok or pd.isna(row.get(low_key, np.nan)) or pd.isna(row.get(high_key, np.nan)):
            return "표본 부족"
        return f"{pct_price(row[low_key]):,.0f}~{pct_price(row[high_key]):,.0f}원"

    def probability(key):
        value = row.get(key, np.nan)
        return "표본 부족" if not prediction_ok or pd.isna(value) else f"{float(value):.0f}%"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("진입 추천 점수", f"{row['종합점수']:.1f}/100", row["판정"])
    c2.metric("OBV", row.get("OBV", "-"))
    c3.metric("CVD Proxy", row.get("CVD Proxy", "-"))
    c4.metric("RSI(14)", f"{row.get('RSI14', np.nan):.1f}")

    st.markdown("#### 익일 통계 예측")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("익일 상승 확률", probability("익일승률%"))
    c2.metric("갭상승 확률", probability("갭상승확률%"))
    c3.metric(f"{horizon}일 내 +3%", probability(reach_key))
    c4.metric("초기 손절 도달", probability("초기손절도달확률%"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("예상 시가 범위", price_range("예상시가하단%", "예상시가상단%"))
    c2.metric("예상 종가 범위", price_range("예상종가하단%", "예상종가상단%"))
    c3.metric("예상 고가", "표본 부족" if not prediction_ok else f"{pct_price(row['예상고가%']):,.0f}원")
    c4.metric("예상 저가", "표본 부족" if not prediction_ok else f"{pct_price(row['예상저가%']):,.0f}원")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("유사 표본", f"{sample_n}회" if prediction_ok else "표본 부족")
    c2.metric("예측 신뢰도", row.get("신뢰도", "표본 부족"))
    c3.metric("외국인 5일", foreign_text)
    c4.metric("공매도 비중", short_text)

    st.markdown("#### 장후 가격 비교")
    def won_value(key):
        value = row.get(key, np.nan)
        return "데이터 없음" if pd.isna(value) else f"{float(value):,.0f}원"

    def pct_value(key):
        value = row.get(key, np.nan)
        return "데이터 없음" if pd.isna(value) else f"{float(value):+.2f}%"

    c1, c2, c3 = st.columns(3)
    c1.metric("KRX 종가", won_value("KRX 종가"))
    c2.metric("NXT 현재가 (20분 지연)", won_value("NXT 현재가"))
    c3.metric("해외 24h 환산가", won_value("해외24h 환산가"))
    c1, c2, c3 = st.columns(3)
    c1.metric("해외 괴리율", pct_value("해외 괴리율%"))
    c2.metric("NXT 프리미엄", pct_value("NXT 프리미엄%"))
    c3.metric("해외가격 판정", row.get("해외가격 신호", "데이터 없음"))
    st.caption(f"해외상품 유형: {row.get('해외상품 유형', '매핑 없음')} · USD/KRW: {row.get('USD/KRW', np.nan):,.2f}" if pd.notna(row.get("USD/KRW", np.nan)) else
               f"해외상품 유형: {row.get('해외상품 유형', '매핑 없음')} · USD/KRW 데이터 없음")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("진입가", f"{row['진입가']:,.0f}원")
    c2.metric("손절가", f"{row['초기손절']:,.0f}원", f"-{row['손절률%']:.2f}%")
    c3.metric("1차 익절가", f"{row['1차익절(+10%)']:,.0f}원", "+10%")
    c4.metric("2차 익절가", f"{row['2차익절(+20%)']:,.0f}원", "+20%")

    st.info("예상가는 확정값이 아니라 과거 유사조건과 ATR14를 결합한 통계적 예상범위입니다. 미래 가격이나 수익을 보장하지 않습니다.")

    with st.expander("자세히 보기 — 탈락사유·전체 지표·차트·일별 수급"):
        st.write(f"**판정:** {row['판정']}  |  **탈락사유:** {row['탈락사유']}")
        details = {k: row.get(k) for k in ["종목점수", "시장환경", "업종환경", "최종순위점수", "업종분류", "유형", "RSI14", "거래량배수", "종가위치%", "윗꼬리%", "시장대비강도%p", "OBV", "CVD Proxy", "표본수", "신뢰도", "유사도중앙거리", "익일승률%", "갭상승확률%", "예상시가하단%", "예상시가상단%", "예상종가하단%", "예상종가상단%", "예상고가%", "예상저가%", reach_key, "초기손절도달확률%", "평균MAE%", "손절률%", "1차손익비R", "2차손익비R"]}
        st.dataframe(pd.DataFrame([details]), use_container_width=True, hide_index=True)
        if summary:
            st.markdown("#### 외국인·기관 수급 및 공매도")
            st.dataframe(pd.DataFrame([summary]), use_container_width=True, hide_index=True)
        if len(flow):
            st.markdown("#### 외국인·기관 일별 순매수/순매도")
            st.dataframe((flow / 1e8).round(2).rename_axis("날짜"), use_container_width=True)
            st.caption(f"단위: 억원. 양수는 순매수, 음수는 순매도입니다. 출처: {summary.get('수급출처', 'KRX')}")
        if len(short):
            st.markdown("#### 일별 공매도 거래")
            st.dataframe(short.rename_axis("날짜"), use_container_width=True)
        st.info("공매도 거래·잔고는 KRX 공개 통계이며 특정 투자자의 개별 포지션을 뜻하지 않습니다. 잔고 데이터는 공표 시차가 있을 수 있습니다.")
        try: st.plotly_chart(chart(row["종목코드"], row), use_container_width=True)
        except Exception as exc: st.warning(f"차트를 불러오지 못했습니다: {exc}")


def show_simple_prediction(row):
    """Minimal result used by direct stock lookup."""
    st.markdown(f"#### {row['종목명']} ({row['종목코드']}) 예상 가격")
    prediction_ok = row.get("예측상태") == "산출"
    entry = float(row["진입가"])

    def predicted_price(key):
        value = row.get(key, np.nan)
        if not prediction_ok or pd.isna(value):
            return "표본 부족"
        return f"{entry * (1 + float(value) / 100):,.0f}원"

    def direction_delta(key):
        value = row.get(key, np.nan)
        if not prediction_ok or pd.isna(value):
            return None
        value = float(value)
        direction = "상승" if value > 0 else "하락" if value < 0 else "보합"
        return f"{value:+.2f}% · {direction}"

    def probability(key):
        value = row.get(key, np.nan)
        return "표본 부족" if not prediction_ok or pd.isna(value) else f"{float(value):.0f}%"

    open_key = "예상시가평균%"
    if pd.isna(row.get(open_key, np.nan)) and prediction_ok:
        low = row.get("예상시가하단%", np.nan)
        high = row.get("예상시가상단%", np.nan)
        if pd.notna(low) and pd.notna(high):
            row = {**row, open_key: (float(low) + float(high)) / 2}

    night = current_kospi200_night() if str(row.get("시장", "")).upper().startswith("KOSPI") else None
    if night and prediction_ok and pd.notna(row.get(open_key, np.nan)):
        row = {**row, open_key: float(row[open_key]) + float(night["변동률%"])}

    c1, c2 = st.columns(2)
    c1.metric("예상 시가", predicted_price(open_key), direction_delta(open_key))
    c2.metric("예상 종가", predicted_price("익일평균%"), direction_delta("익일평균%"))
    st.caption(f"오늘 종가 {entry:,.0f}원 대비")
    if night:
        direction = "▲" if night["변동률%"] > 0 else "▼" if night["변동률%"] < 0 else "-"
        st.caption(
            f"코스피200 야간선물 {direction} {night['변동률%']:+.2f}%를 예상 시가에 반영 · "
            f"거래량 {night['거래량']:,} · {night['조회시각']} · {night['출처']}"
        )
    c1, c2 = st.columns(2)
    c1.metric("익일 상승 확률", probability("익일승률%"))
    c2.metric("갭상승 확률", probability("갭상승확률%"))
    st.caption("과거 유사조건과 ATR14를 결합한 통계적 예상 중심값이며 실제 가격을 보장하지 않습니다.")
    if st.button("자세히 보기", use_container_width=True):
        with st.spinner("상세 데이터 불러오는 중..."):
            st.session_state["scanner_v5_selected"] = enrich_after_hours(dict(row))
        st.session_state["scanner_v5_selected_mode"] = "detail"
        st.rerun()


def scanner_table(frame):
    color_cols = [c for c in ("해외 괴리율%", "NXT 프리미엄%") if c in frame.columns]
    if not color_cols:
        return frame
    def color_value(value):
        if pd.isna(value): return "color: #8b93a5"
        return "color: #2e9d50; font-weight: 700" if value > 0 else "color: #d14b4b; font-weight: 700" if value < 0 else ""
    return frame.style.map(color_value, subset=color_cols).format({c: "{:+.2f}%" for c in color_cols}, na_rep="데이터 없음")


def market_table_style(frame):
    def direction_color(value):
        text = str(value)
        if text.startswith("▲"): return "color: #238636; font-weight: 700"
        if text.startswith("▼"): return "color: #cf3c4f; font-weight: 700"
        return "color: #7a8292"

    def impact_color(value):
        colors = {"긍정": "#238636", "부정": "#cf3c4f", "중립": "#7a8292", "데이터 없음": "#7a8292"}
        return f"color: {colors.get(str(value), '#7a8292')}; font-weight: 700"

    return (frame.style
            .map(direction_color, subset=["전일대비", "5일 누적"])
            .map(impact_color, subset=["증시영향"])
            .format({"현재": "{:,.2f}"}, na_rep="데이터 없음"))


# ── 화면 ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("v5 스캔 조건")
    market_filter = st.selectbox("시장", ["전체", "KOSPI", "KOSDAQ"])
    max_symbols = st.select_slider("스캔 종목 수", [100, 200, 300, 500, 800, 1200], value=300)
    min_value = st.number_input("20일 평균 거래대금 최소(억원)", 1, value=30, step=10)
    min_price = st.number_input("최소 주가(원)", 100, value=2000, step=500)
    min_vr = st.slider("거래량 최소 배수", .5, 5., 1.5, .1)
    rlo, rhi = st.slider("RSI 범위", 0, 100, (55, 70))
    close_pos = st.slider("종가 위치 최소(%)", 50, 100, 75)
    max_wick = st.slider("윗꼬리 최대(%)", 5, 60, 35)
    min_rel = st.slider("시장 대비 최소 강도(%p)", -3., 10., .5, .5)
    max_gap = st.slider("MA20 최대 이격률(%)", 1, 30, 12)
    min_score = st.slider("최소 종목점수", 50, 100, 75, 5)
    lookback = st.select_slider("조회 기간(일)", [180, 250, 365, 540], value=365)
    do_bt = st.checkbox("과거 동일신호 백테스트", True)
    prediction_horizon = st.select_slider("+3% 도달 관찰기간(거래일)", [1, 3, 5, 10], value=5)
    min_prediction_samples = st.slider("예측 최소 유사표본 수", 10, 50, 20, 5)
    workers = st.slider("동시 조회 수", 2, 10, 5)

p = {"min_value": min_value * 1e8, "min_price": min_price, "min_vr": min_vr, "rsi_lo": rlo, "rsi_hi": rhi,
     "close_pos": close_pos, "max_wick": max_wick, "min_rel": min_rel, "max_gap": max_gap, "min_score": min_score,
     "prediction_horizon": prediction_horizon, "min_prediction_samples": min_prediction_samples}

with st.spinner("시장환경 확인 중..."):
    market_score, market_label, market_data, market_reasons = market_environment()

st.subheader("오늘 시장환경")
a, b, c, d = st.columns(4)
a.metric("시장환경 점수", f"{market_score}/100"); b.metric("판정", market_label)
c.metric("VIX", "-" if pd.isna(market_data["VIX"]["현재"]) else f"{market_data['VIX']['현재']:.1f}")
d.metric("나스닥100 선물", "-" if pd.isna(market_data["나스닥100 선물"]["현재"]) else f"{market_data['나스닥100 선물']['현재']:,.0f}", None if pd.isna(market_data["나스닥100 선물"]["전일대비"]) else f"{market_data['나스닥100 선물']['전일대비']:+.2f}%")
market_view = market_table(market_data)
st.dataframe(market_table_style(market_view), use_container_width=True, hide_index=True,
             column_config={"현재": st.column_config.NumberColumn("현재"),
                            "전일대비": st.column_config.TextColumn("전일대비 (%)"),
                            "5일 누적": st.column_config.TextColumn("5일 누적 (%)")})
groups = impact_groups(market_data)
st.markdown(f"**시장환경 {market_score}/100 · {market_label}**")
for impact in ("긍정", "부정", "중립"):
    if groups[impact]:
        st.markdown(f"**{impact}:** " + " · ".join(groups[impact]))
if groups["데이터 없음"]:
    st.caption("데이터 없음: " + " · ".join(groups["데이터 없음"]))

st.caption("최종순위점수는 종목 45%·시장 15%·업종 10%·과거 5일 승률 15%·1차 손익비 10%·표본 신뢰도 5%의 검증 전 설계 가중치입니다. 실전 성과를 보장하지 않습니다.")

L = listings()
st.subheader("직접 종목검색")
search_mode = st.radio("조회 방식", ["오늘 예측", "과거 날짜 검증"], horizontal=True)
selected_validation_date = None
if search_mode == "과거 날짜 검증":
    selected_validation_date = st.date_input(
        "기준 날짜", value=date.today() - timedelta(days=1),
        min_value=date.today() - timedelta(days=730), max_value=date.today() - timedelta(days=1),
        help="선택한 날의 종가까지 알려졌다고 가정해 다음 거래일 종가를 예측합니다.")

search_col, button_col = st.columns([4, 1])
with search_col:
    query = st.text_input("종목명 직접 입력", placeholder="여기에 원하는 종목명을 입력하세요")
with button_col:
    st.write("")
    move_clicked = st.button("이 종목으로 이동하기", type="primary", use_container_width=True)
matches = pd.DataFrame()
if query and len(L):
    matches = L[L["Name"].astype(str).str.contains(query, case=False, na=False, regex=False) | L["Code"].str.contains(query, regex=False)].head(30)
if move_clicked and len(matches):
    exact = matches[(matches["Name"].astype(str).str.lower() == query.strip().lower()) | (matches["Code"] == query.strip())]
    r = exact.iloc[0] if len(exact) else matches.iloc[0]
    mkt = str(r.get("Market", "KOSPI")); sec = str(r.get("Sector", r.get("Industry", "")))
    if search_mode == "과거 날짜 검증":
        with st.spinner("선택한 날짜 기준으로 검증 중..."):
            validation = validate_at_date(r.Code, selected_validation_date, mkt, p, lookback)
        st.session_state["scanner_v5_validation"] = {"종목명": r.Name, "종목코드": r.Code, "결과": validation}
    else:
        with st.spinner("종목 분석 중..."):
            result = analyze(r.Code, r.Name, mkt, sec, (date.today() - timedelta(days=lookback)).isoformat(), p, do_bt, market_score, market_data)
        if result:
            st.session_state["scanner_v5_selected"] = result
            st.session_state["scanner_v5_selected_mode"] = "simple"
        else: st.error("분석에 필요한 가격 데이터가 부족합니다.")
elif move_clicked and query:
    st.warning("일치하는 KRX 종목이 없습니다.")

if "scanner_v5_validation" in st.session_state and search_mode == "과거 날짜 검증":
    validation_item = st.session_state["scanner_v5_validation"]
    validation = validation_item["결과"]
    st.markdown(f"#### {validation_item['종목명']} ({validation_item['종목코드']}) 과거 예측 검증")
    if not validation:
        st.warning("선택한 날짜의 가격 데이터를 확인할 수 없습니다.")
    elif validation["상태"] == "표본 부족":
        st.warning(f"표본 부족 · 유사 표본 {validation.get('표본수', 0)}회")
    elif validation["상태"] == "실제 종가 대기":
        st.info("다음 거래일 실제 종가가 아직 없어 검증할 수 없습니다.")
    elif not all(key in validation for key in ("예상 시가", "실제 시가", "시가 차이", "예상 종가", "종가 차이")):
        st.info("검증 계산 방식이 업데이트되었습니다. ‘이 종목으로 이동하기’를 다시 눌러주세요.")
    else:
        st.markdown(f"""
        <div class="validation-grid">
          <div class="validation-card"><div class="validation-label">예상 시가</div><div class="validation-value">{validation['예상 시가']:,.0f}원</div></div>
          <div class="validation-card"><div class="validation-label">실제 시가</div><div class="validation-value">{validation['실제 시가']:,.0f}원</div></div>
          <div class="validation-card"><div class="validation-label">시가 차이</div><div class="validation-value">{validation['시가 차이']:+,.0f}원</div><div class="validation-sub">{validation['시가 차이율%']:+.2f}%</div></div>
          <div class="validation-card"><div class="validation-label">예상 종가</div><div class="validation-value">{validation['예상 종가']:,.0f}원</div></div>
          <div class="validation-card"><div class="validation-label">실제 종가</div><div class="validation-value">{validation['실제 종가']:,.0f}원</div></div>
          <div class="validation-card"><div class="validation-label">종가 차이</div><div class="validation-value">{validation['종가 차이']:+,.0f}원</div><div class="validation-sub">{validation['종가 차이율%']:+.2f}%</div></div>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"기준일 {validation['기준일']} → 실제 거래일 {validation['실제일']} · 예상가는 당시 데이터만 사용한 유사표본 가중 중심값입니다.")

if st.button("오늘 종가 매매 후보 찾아보기", type="primary", use_container_width=True):
    scan = L.copy()
    if market_filter != "전체" and "Market" in scan.columns: scan = scan[scan["Market"].astype(str).str.upper().str.startswith(market_filter)]
    cap = next((c for c in ("Marcap", "MarketCap") if c in scan.columns), None)
    if cap: scan = scan.sort_values(cap, ascending=False)
    scan = scan.head(max_symbols)
    start = (date.today() - timedelta(days=lookback)).isoformat()
    rows, progress, message = [], st.progress(0), st.empty()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for _, r in scan.iterrows():
            mkt, sec = str(r.get("Market", "KOSPI")), str(r.get("Sector", r.get("Industry", "")))
            futures.append(pool.submit(analyze, r.Code, r.Name, mkt, sec, start, p, do_bt, market_score, market_data))
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result: rows.append(result)
            progress.progress(i / max(len(futures), 1)); message.text(f"{i}/{len(futures)} 종목 분석 중")
    progress.empty(); message.empty()
    if rows:
        rows = [enrich_after_hours(row) for row in rows]
        R = pd.DataFrame(rows).sort_values(["최종순위점수", "종합점수"], ascending=False)
        st.session_state["scanner_v5_all"] = R
    else: st.error("분석 결과가 없습니다.")

if "scanner_v5_all" in st.session_state:
    R = st.session_state["scanner_v5_all"]
    buys = R[R["판정"] == "매수후보"].head(5)
    near = R[R["판정"] != "매수후보"].sort_values(["최종순위점수", "종합점수"], ascending=False).head(5)
    st.subheader("오늘의 최종 매수후보 TOP5")
    if len(buys): st.dataframe(scanner_table(buys), use_container_width=True, hide_index=True)
    else: st.info("오늘 하드필터 통과 종목은 0개입니다. 아래 조건근접 TOP5를 대신 확인하세요.")
    st.subheader("조건근접 TOP5")
    st.dataframe(scanner_table(near), use_container_width=True, hide_index=True)

    st.subheader("업종별 / 전체 후보표")
    sector_choice = st.selectbox("업종 보기", ["전체", "반도체", "성장·기술", "에너지", "항공·운송", "일반"])
    view = R if sector_choice == "전체" else R[R["업종분류"] == sector_choice]
    st.dataframe(scanner_table(view), use_container_width=True, hide_index=True)
    if len(view):
        labels = [f"{r['종목명']} ({r['종목코드']}) · {r['판정']}" for _, r in view.iterrows()]
        chosen = st.selectbox("상세 차트 종목 선택", labels)
        if st.button("선택 종목 상세보기"):
            st.session_state["scanner_v5_selected"] = view.iloc[labels.index(chosen)].to_dict()
            st.session_state["scanner_v5_selected_mode"] = "detail"
    st.download_button("v5 전체 결과 CSV 다운로드", R.to_csv(index=False).encode("utf-8-sig"), "krx_jongga_scanner_v5.csv", "text/csv")

if "scanner_v5_selected" in st.session_state:
    if st.session_state.get("scanner_v5_selected_mode") == "simple":
        show_simple_prediction(st.session_state["scanner_v5_selected"])
    else:
        show_detail(st.session_state["scanner_v5_selected"])

st.divider()
st.caption("연구·정보 제공용 도구입니다. CVD Proxy는 실제 체결 CVD가 아니며, 백테스트에는 거래비용·슬리피지·생존편향이 완전히 반영되지 않습니다. 투자 판단과 책임은 사용자에게 있습니다.")

