from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import unicodedata

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from global_price import after_hours_snapshot, load_product_map, nxt_delayed_quotes, usdkrw_quote
from daily_structure import analyze_daily_structure
from market_sources import naver_investor_flow
from market_ui import extract_close, impact_groups, last_metrics, legacy_market_score, market_table
from night_futures import kospi200_night_quote
from rebound_pattern import analyze_rebound_pattern, normalize_intraday, pattern_filter_mask
from structure_score import calculate_structure_score

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

ANALYSIS_ERRORS = {}


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
[data-testid="stMetricValue"] {
  min-width: 0; font-size: clamp(1.05rem, 2.6vw, 1.85rem);
  line-height: 1.18; letter-spacing: -.025em;
}
[data-testid="stMetricValue"] > div {
  min-width: 0; white-space: normal !important; overflow: visible !important;
  text-overflow: clip !important; overflow-wrap: anywhere; word-break: keep-all; line-height: 1.18;
}
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
  [data-testid="stMetric"] { padding: .85rem 1rem; border-radius: .85rem; min-height: 7rem; }
  [data-testid="stMetricValue"] { font-size: clamp(1rem, 6vw, 1.55rem); }
  [data-testid="stDataFrame"] { font-size: .78rem; }
  .validation-grid { grid-template-columns: 1fr; }
}
</style>
""", unsafe_allow_html=True)
SCAN_SETTING_DEFAULTS = {
    "market_filter": "전체",
    "max_symbols": 50,
    "min_value": 30,
    "min_price": 2000,
    "min_vr": 1.5,
    "rsi_range": (55, 70),
    "close_pos": 75,
    "max_wick": 35,
    "min_rel": 0.5,
    "max_gap": 12,
    "min_score": 75,
    "lookback": 365,
    "do_bt": True,
    "prediction_horizon": 5,
    "min_prediction_samples": 20,
    "workers": 5,
    "pattern_approach_pct": 3.0,
    "pattern_filter": "전체",
    "swing_window": 3,
    "swing_atr_multiple": 1.0,
    "swing_min_change_pct": 2.0,
}
for setting_name, default_value in SCAN_SETTING_DEFAULTS.items():
    canonical_key = f"scanner_v5_setting_{setting_name}"
    st.session_state.setdefault(canonical_key, default_value)
    st.session_state.setdefault(f"sidebar_{setting_name}", st.session_state[canonical_key])
    st.session_state.setdefault(f"mobile_{setting_name}", st.session_state[canonical_key])


def sync_scan_setting(source_key, setting_name, peer_key):
    value = st.session_state[source_key]
    st.session_state[f"scanner_v5_setting_{setting_name}"] = value
    st.session_state[peer_key] = value


def reset_scan_settings():
    for setting_name, default_value in SCAN_SETTING_DEFAULTS.items():
        st.session_state[f"scanner_v5_setting_{setting_name}"] = default_value
        st.session_state[f"sidebar_{setting_name}"] = default_value
        st.session_state[f"mobile_{setting_name}"] = default_value
    st.session_state["scanner_v5_settings_reset_notice"] = True


def setting_widget(prefix, setting_name, widget, *args, **kwargs):
    widget_key = f"{prefix}_{setting_name}"
    peer_prefix = "mobile" if prefix == "sidebar" else "sidebar"
    kwargs.update({
        "key": widget_key,
        "on_change": sync_scan_setting,
        "args": (widget_key, setting_name, f"{peer_prefix}_{setting_name}"),
    })
    return widget(*args, **kwargs)


def render_scan_settings(prefix):
    setting_widget(prefix, "market_filter", st.selectbox, "시장", ["전체", "KOSPI", "KOSDAQ"])
    setting_widget(prefix, "max_symbols", st.select_slider, "스캔 종목 수", options=[50, 100, 200, 300, 500, 800, 1200])
    setting_widget(prefix, "min_value", st.number_input, "20일 평균 거래대금 최소(억원)", min_value=1, step=10)
    setting_widget(prefix, "min_price", st.number_input, "최소 주가(원)", min_value=100, step=500)
    setting_widget(prefix, "min_vr", st.slider, "거래량 최소 배수", min_value=.5, max_value=5., step=.1)
    setting_widget(prefix, "rsi_range", st.slider, "RSI 범위", min_value=0, max_value=100)
    setting_widget(prefix, "close_pos", st.slider, "종가 위치 최소(%)", min_value=50, max_value=100)
    setting_widget(prefix, "max_wick", st.slider, "윗꼬리 최대(%)", min_value=5, max_value=60)
    setting_widget(prefix, "min_rel", st.slider, "시장 대비 최소 강도(%p)", min_value=-3., max_value=10., step=.5)
    setting_widget(prefix, "max_gap", st.slider, "MA20 최대 이격률(%)", min_value=1, max_value=30)
    setting_widget(prefix, "min_score", st.slider, "최소 종목점수", min_value=50, max_value=100, step=5)
    setting_widget(prefix, "lookback", st.select_slider, "조회 기간(일)", options=[180, 250, 365, 540])
    setting_widget(prefix, "do_bt", st.checkbox, "후보 스캔 시 과거 동일신호 백테스트")
    setting_widget(prefix, "prediction_horizon", st.select_slider, "+3% 도달 관찰기간(거래일)", options=[1, 3, 5, 10])
    setting_widget(prefix, "min_prediction_samples", st.slider, "예측 최소 유사표본 수", min_value=10, max_value=50, step=5)
    setting_widget(prefix, "workers", st.slider, "동시 조회 수", min_value=2, max_value=10)
    setting_widget(prefix, "pattern_approach_pct", st.slider, "추정 패턴 접근 범위(%)", min_value=1.0, max_value=10.0, step=.5)
    setting_widget(
        prefix, "pattern_filter", st.selectbox, "추정 패턴 필터",
        ["전체", "반등가 ±3% 종목", "반등가 상향 돌파 종목", "+33%선 돌파 종목",
         "+50%선 돌파 종목", "다음 목표가까지 상승여력 10% 이상",
         "거래량 증가 + 반등선 돌파", "거래량 증가 + 33%선 돌파"],
    )
    st.caption("차트 구조 판정 설정")
    setting_widget(prefix, "swing_window", st.slider, "일봉 Swing 좌우 확인 봉", min_value=2, max_value=7)
    setting_widget(prefix, "swing_atr_multiple", st.slider, "Swing 최소 ATR 배수", min_value=.5, max_value=3., step=.25)
    setting_widget(prefix, "swing_min_change_pct", st.slider, "Swing 최소 가격변화(%)", min_value=.5, max_value=10., step=.5)
    st.button(
        "↺ 필터 기본값으로 초기화",
        key=f"{prefix}_reset_scan_settings",
        use_container_width=True,
        on_click=reset_scan_settings,
    )
    st.caption("잘못 변경한 경우 시장·지표·조회 조건을 처음 설정으로 되돌립니다.")


@st.dialog("이용 전 확인", width="large")
def show_usage_consent():
    st.markdown(
        """
        본 프로그램은 테스트 목적으로 제작된 임시 서비스입니다. **테스트 기간은 2026년 8월 17일까지이며,
        이후 서비스 개선을 위해 종료됩니다.** 아래 내용을 각각 확인해 주세요.
        """
    )
    consent_1 = st.checkbox(
        "1. 본 서비스는 투자자문이나 매수·매도 추천이 아니며, 과거 데이터를 활용한 학습·참고용 분석 도구임을 확인했습니다.",
        key="scanner_v5_consent_investment",
    )
    consent_2 = st.checkbox(
        "2. 예상가격·확률·점수는 실제 결과나 수익을 보장하지 않으며, 모든 투자 판단과 손익의 책임은 이용자 본인에게 있음을 확인했습니다.",
        key="scanner_v5_consent_responsibility",
    )
    consent_3 = st.checkbox(
        "3. 테스트 기간은 2026년 8월 17일까지이며, 이후 서비스 개선을 위해 종료됩니다. 또한 데이터 지연·누락·오류가 발생하거나 서비스가 예고 없이 변경·중단될 수 있음을 확인했습니다.",
        key="scanner_v5_consent_service",
    )
    all_agreed = consent_1 and consent_2 and consent_3
    st.caption("세 항목에 모두 동의해야 서비스를 이용할 수 있습니다.")
    if st.button(
        "동의하고 시작하기",
        type="primary",
        use_container_width=True,
        disabled=not all_agreed,
        key="scanner_v5_accept_terms",
    ):
        st.session_state["scanner_v5_terms_accepted"] = True
        st.rerun()


if not st.session_state.get("scanner_v5_terms_accepted", False):
    show_usage_consent()
    st.stop()


st.title("KRX 종가매매 종목 스캐너 v5 by. 바빠맘")
st.caption("시장·업종·수급·공매도·기술 신호와 과거 동일신호 통계를 한 화면에서 확인합니다.")
st.markdown("""
<style>
.top-guide {display:grid; gap:.55rem; margin:.7rem 0 .6rem;}
.top-guide-item {display:grid; grid-template-columns:7rem 1fr; gap:.7rem; align-items:start;
  padding:.72rem .85rem; border:1px solid #e3e7ef; border-radius:.7rem; background:#fff;}
.top-guide-label {font-weight:750; color:#344054; white-space:nowrap; font-size:.88rem;}
.top-guide-text {color:#667085; line-height:1.45; font-size:.86rem;}
.top-warning {margin:0 0 1rem; padding:.78rem .9rem; border:1px solid #e45852;
  border-left:5px solid #e45852; border-radius:.7rem; background:#fff3f2;
  color:#c93632; font-weight:650; line-height:1.5;}
@media (max-width:640px) {
  .top-guide-item {grid-template-columns:1fr; gap:.2rem; padding:.7rem .8rem;}
  .top-guide-label {font-size:.82rem;}
  .top-guide-text, .top-warning {font-size:.78rem; line-height:1.45;}
}
</style>
<div class="top-guide">
  <div class="top-guide-item">
    <div class="top-guide-label">🇰🇷 적용 시장</div>
    <div class="top-guide-text">대한민국 KRX(KOSPI·KOSDAQ) 정규장 기준 데이터에만 적용됩니다.</div>
  </div>
  <div class="top-guide-item">
    <div class="top-guide-label">📊 데이터 제한</div>
    <div class="top-guide-text">신규 상장 종목이거나 과거 가격 데이터를 충분히 확인할 수 없는 경우 검색·예측 결과가 표시되지 않을 수 있습니다.</div>
  </div>
  <div class="top-guide-item">
    <div class="top-guide-label">🧮 계산 방식</div>
    <div class="top-guide-text">조회 기준 종가에 과거 유사신호의 다음 거래일 가격 분포와 ATR14 변동성을 반영해 예상 범위와 대표값을 계산합니다.</div>
  </div>
</div>
<div class="top-warning">
  ⚠️ 모든 점수와 예상가격은 수익이나 가격 상승을 보장하지 않습니다. 실제 투자 결과에 대해 서비스 제공자는 책임지지 않으며 참고자료로만 사용해야 합니다.
</div>
""", unsafe_allow_html=True)

st.markdown("""
<style>
@media (max-width: 768px) {
  .block-container { padding-top: 4.5rem !important; }
  .st-key-mobile_settings_panel { display: block; }
  .st-key-mobile_settings_panel details {
    border: 1px solid #d5dae4;
    border-radius: 14px;
    background: #ffffff;
    box-shadow: 0 2px 10px rgba(25, 35, 55, .05);
  }
  .st-key-mobile_settings_panel details > summary {
    min-height: 44px;
    padding: .72rem .85rem;
    font-weight: 750;
  }
}
@media (min-width: 769px) {
  .st-key-mobile_settings_panel { display: none; }
}
</style>
""", unsafe_allow_html=True)
with st.container(key="mobile_settings_panel"):
    with st.expander("⚙️ 상세 조건 설정하기", expanded=False):
        st.caption("시장·RSI·거래량·종가 위치 등 스캔 기준을 변경합니다.")
        render_scan_settings("mobile")


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


@st.cache_data(ttl=300, show_spinner=False)
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
        "EWY": yahoo_metric("EWY"), "KORU": yahoo_metric("KORU"),
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


@st.cache_data(ttl=1800, show_spinner=False)
def hourly_prices(symbol, market):
    if not YF_OK:
        return pd.DataFrame()
    suffix = ".KS" if str(market).upper().startswith("KOSPI") else ".KQ"
    try:
        raw = yf.download(f"{str(symbol).zfill(6)}{suffix}", period="2y", interval="60m",
                          auto_adjust=False, progress=False, threads=False)
        return normalize_intraday(raw)
    except Exception:
        return pd.DataFrame()


def rebound_snapshot(symbol, market, approach_pct):
    frame = hourly_prices(symbol, market)
    return analyze_rebound_pattern(frame, approach_pct=approach_pct), frame


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
MACRO_TICKERS = {
    "NQ": "NQ=F", "SOX": "^SOX", "VIX": "^VIX", "FX": "KRW=X",
    "TNX": "^TNX", "WTI": "CL=F", "EWY": "EWY", "KORU": "KORU",
}


@st.cache_data(ttl=900, show_spinner=False)
def macro_prediction_history(start):
    """Build lagged global-market features that were knowable before the KRX close."""
    if not YF_OK:
        return pd.DataFrame()
    series = {}
    for prefix, ticker in MACRO_TICKERS.items():
        try:
            raw = yf.download(ticker, start=start, interval="1d", progress=False,
                              auto_adjust=False, threads=False)
            close = pd.to_numeric(extract_close(raw, ticker), errors="coerce").dropna()
            if close.empty:
                continue
            close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
            # Same-calendar-day US closes are not known at the Korean close.
            known = close.shift(1)
            ret1 = known.pct_change() * 100
            # KORU is leveraged; normalize it to an approximate 1x move so it
            # contributes as a secondary Korea-risk signal rather than dominating.
            series[f"{prefix}_RET1"] = ret1 / 3 if prefix == "KORU" else ret1
            if prefix in {"NQ", "SOX", "FX", "EWY"}:
                series[f"{prefix}_RET5"] = known.pct_change(5) * 100
            if prefix == "VIX":
                series["VIX_LEVEL"] = known
        except Exception:
            continue
    return pd.DataFrame(series).sort_index() if series else pd.DataFrame()


def current_macro_features(market_data):
    """Map the latest observable market snapshot to the model's macro columns."""
    mapping = {
        "NQ": "나스닥100 선물", "SOX": "SOX", "VIX": "VIX",
        "FX": "원/달러", "TNX": "미국10년물", "WTI": "WTI",
        "EWY": "EWY", "KORU": "KORU",
    }
    values = {}
    for prefix, label in mapping.items():
        item = market_data.get(label, {})
        if pd.notna(item.get("전일대비", np.nan)):
            current_ret = float(item["전일대비"])
            values[f"{prefix}_RET1"] = current_ret / 3 if prefix == "KORU" else current_ret
        if prefix in {"NQ", "SOX", "FX", "EWY"} and pd.notna(item.get("5일 누적", np.nan)):
            values[f"{prefix}_RET5"] = float(item["5일 누적"])
        if prefix == "VIX" and pd.notna(item.get("현재", np.nan)):
            values["VIX_LEVEL"] = float(item["현재"])
    return values


def _weighted_rate(values, weights, condition):
    mask = condition(np.asarray(values, dtype=float))
    return float(np.average(mask.astype(float), weights=weights) * 100)


def sector_macro_weights(sector_category):
    """Give sector-relevant overseas moves more influence in the similarity search."""
    common = {
        "NQ": 1.15, "VIX": 1.15, "FX": 1.10, "TNX": 1.00,
        "EWY": 1.20, "KORU": 1.05, "SOX": 1.00, "WTI": 1.00,
    }
    sector = str(sector_category or "일반")
    if sector == "반도체":
        common.update({"SOX": 2.40, "NQ": 1.45, "TNX": 1.10})
    elif sector == "성장·기술":
        common.update({"NQ": 2.20, "SOX": 1.25, "TNX": 1.35})
    elif sector == "에너지":
        common.update({"WTI": 2.50, "NQ": .85})
    elif sector == "항공·운송":
        common.update({"WTI": 2.40, "FX": 1.45, "NQ": .90})
    return common


def similar_prediction(df, market_ret, horizon=5, min_samples=20, stop_pct=3.0,
                       macro_history=None, macro_current=None, sector_category=None):
    """Use only information known at each signal date; future rows are labels only."""
    base = df.copy()
    aligned = market_ret.reindex(base.index).fillna(0)
    base["MKT_RET1"] = aligned
    base["MKT_RET5"] = aligned.rolling(5).sum()
    macro_features = []
    if macro_history is not None and not macro_history.empty:
        macro = macro_history.copy()
        macro.index = pd.to_datetime(macro.index).tz_localize(None).normalize()
        base_dates = pd.to_datetime(base.index).tz_localize(None).normalize()
        aligned_macro = macro.reindex(base_dates, method="ffill")
        aligned_macro.index = base.index
        for column in aligned_macro.columns:
            values = pd.to_numeric(aligned_macro[column], errors="coerce")
            if values.notna().mean() >= .70 and pd.notna(values.iloc[-1]):
                base[column] = values
                macro_features.append(column)
        if macro_current:
            for column in macro_features:
                if column in macro_current and pd.notna(macro_current[column]):
                    base.loc[base.index[-1], column] = float(macro_current[column])
    prediction_features = PREDICTION_FEATURES + macro_features
    current = base.iloc[-1]
    candidates = base.iloc[65:-(horizon + 1)].dropna(subset=prediction_features).copy()
    if current[prediction_features].isna().any() or len(candidates) < min_samples:
        return {"표본수": len(candidates), "예측상태": "표본 부족", "신뢰도": "표본 부족"}

    # Scale using the historical candidate pool only. The latest observation never
    # changes historical feature values, preventing look-ahead leakage.
    hist = candidates[prediction_features].astype(float)
    center = hist.median()
    scale = (hist.quantile(.75) - hist.quantile(.25)).replace(0, np.nan)
    scale = scale.fillna(hist.std()).replace(0, 1).fillna(1)
    standardized_sq = ((hist - current[prediction_features].astype(float)) / scale) ** 2
    sector_weights = sector_macro_weights(sector_category)
    feature_weights = pd.Series(1.0, index=prediction_features)
    for column in macro_features:
        prefix = column.split("_", 1)[0]
        feature_weights[column] = sector_weights.get(prefix, 1.0)
    distance = (
        standardized_sq.mul(feature_weights, axis=1).sum(axis=1) / feature_weights.sum()
    ) ** .5
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
        "거시지표반영": ", ".join(macro_features) if macro_features else "국내시장만 반영",
        "업종지표가중": str(sector_category or "시장 공통"),
        "유사도중앙거리": round(float(out["distance"].median()), 2),
        "익일승률%": round(_weighted_rate(out["close"], weights, lambda x: x > 0), 1),
        "익일하락확률%": round(_weighted_rate(out["close"], weights, lambda x: x < 0), 1),
        "익일보합확률%": round(_weighted_rate(out["close"], weights, lambda x: np.isclose(x, 0, atol=1e-12)), 1),
        "갭상승확률%": round(_weighted_rate(out["open"], weights, lambda x: x > 0), 1),
        "갭하락확률%": round(_weighted_rate(out["open"], weights, lambda x: x < 0), 1),
        "보합출발확률%": round(_weighted_rate(out["open"], weights, lambda x: np.isclose(x, 0, atol=1e-12)), 1),
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


LIVE_FORECAST_KEYS = (
    "익일승률%", "익일하락확률%", "익일보합확률%",
    "갭상승확률%", "갭하락확률%", "보합출발확률%",
    "예상시가하단%", "예상시가상단%", "예상종가하단%", "예상종가상단%",
    "예상고가%", "예상저가%", "예상시가평균%", "익일평균%",
)


def merge_close_and_live_forecasts(close_forecast, live_forecast, checked_at=None):
    """Keep the close snapshot while exposing a separately labelled live nowcast."""
    if live_forecast.get("예측상태") != "산출":
        result = dict(close_forecast)
        result["실시간보정상태"] = live_forecast.get("예측상태", "산출 불가")
        return result
    result = dict(live_forecast)
    if close_forecast.get("예측상태") == "산출":
        for key in LIVE_FORECAST_KEYS:
            if key in close_forecast:
                result[f"장마감_{key}"] = close_forecast[key]
    result["실시간보정상태"] = "산출"
    result["실시간보정시각"] = checked_at or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    result["실시간참고지표"] = "나스닥100 선물 · EWY · KORU(1/3 정규화) · SOX · VIX · 원/달러 · 미국10년물 · WTI"
    return result


@st.cache_data(ttl=300, show_spinner=False)
def skhynix_adr_returns():
    """NASDAQ SKHY daily returns; percentage moves avoid ADR ratio/premium distortion."""
    if not YF_OK:
        return pd.Series(dtype=float)
    try:
        raw = yf.download("SKHY", period="6mo", interval="1d", progress=False,
                          auto_adjust=False, threads=False)
        close = pd.to_numeric(extract_close(raw, "SKHY"), errors="coerce").dropna()
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        return (close.pct_change() * 100).dropna()
    except Exception:
        return pd.Series(dtype=float)


def apply_stock_specific_open_signal(forecast, symbol, krx_history):
    """Blend SKHY's move into SK hynix's open using observed ADR-to-KRX gap history."""
    result = dict(forecast)
    if str(symbol).zfill(6) != "000660" or result.get("예측상태") != "산출":
        return result
    adr_ret = skhynix_adr_returns()
    if adr_ret.empty or krx_history is None or len(krx_history) < 3:
        result["ADR보정상태"] = "SKHY 데이터 부족"
        return result

    krx = krx_history[["Open", "Close"]].dropna().copy()
    krx.index = pd.to_datetime(krx.index).tz_localize(None).normalize()
    krx_gap = (krx["Open"] / krx["Close"].shift(1) - 1) * 100
    left = pd.DataFrame({"krx_date": krx_gap.index, "gap": krx_gap.values}).sort_values("krx_date")
    right = pd.DataFrame({"adr_date": adr_ret.index, "adr_ret": adr_ret.values}).sort_values("adr_date")
    paired = pd.merge_asof(
        left, right, left_on="krx_date", right_on="adr_date",
        direction="backward", allow_exact_matches=False,
    ).dropna()
    paired = paired[(paired["gap"].abs() <= 30) & (paired["adr_ret"].abs() <= 30)].tail(60)
    if len(paired) < 10 or paired["adr_ret"].std() == 0:
        result["ADR보정상태"] = f"SKHY 겹침 표본 부족({len(paired)}회)"
        return result

    slope, intercept = np.polyfit(paired["adr_ret"].to_numpy(), paired["gap"].to_numpy(), 1)
    slope = float(np.clip(slope, .15, 1.25))
    latest_move = float(adr_ret.iloc[-1])
    adr_implied_gap = float(np.clip(intercept + slope * latest_move, -20, 20))
    price_keys = (
        "예상시가평균%", "예상고가%", "예상저가%", "익일평균%",
        "예상시가하단%", "예상시가상단%", "예상종가하단%", "예상종가상단%",
    )
    for key in price_keys:
        if pd.notna(result.get(key, np.nan)):
            result.setdefault(f"NXT보정전_{key}", float(result[key]))
    base_open = float(result.get("NXT보정전_예상시가평균%", result.get("예상시가평균%", 0.0)))
    blend = float(np.clip(.25 + len(paired) / 100, .35, .60))
    result["예상시가평균%"] = round((1 - blend) * base_open + blend * adr_implied_gap, 2)
    result["SKHY등락률%"] = round(latest_move, 2)
    result["ADR시가보정비중%"] = round(blend * 100)
    result["ADR겹침표본수"] = int(len(paired))
    result["ADR보정상태"] = "SKHY 등락률 반영"
    result["실시간참고지표"] = result.get("실시간참고지표", "") + " · SK하이닉스 ADR(SKHY)"
    return result


def apply_nxt_premarket_open_signal(forecast, symbol, krx_close, checked_at=None):
    """Use the delayed NXT premarket quote as the strongest live KRX-open reference."""
    result = dict(forecast)
    now = checked_at or datetime.now(ZoneInfo("Asia/Seoul"))
    if result.get("예측상태") != "산출" or now.weekday() >= 5 or not (8 <= now.hour < 9):
        return result
    try:
        nxt_price = float(current_nxt_quotes().get(str(symbol).zfill(6), np.nan))
        close = float(krx_close)
    except (TypeError, ValueError):
        return result
    if pd.isna(nxt_price) or not np.isfinite(nxt_price) or close <= 0:
        result["NXT시가보정상태"] = "NXT 가격 없음"
        return result
    nxt_gap = (nxt_price / close - 1) * 100
    if abs(nxt_gap) > 30:
        result["NXT시가보정상태"] = "NXT 가격 검증 제외"
        return result

    base_open = float(result.get("예상시가평균%", 0.0))
    # NXT is an actual same-morning trade, but remains a separate venue and is
    # displayed with a delay; retain part of the historical/overseas forecast.
    blend = .80
    adjusted_open = (1 - blend) * base_open + blend * nxt_gap
    shift = adjusted_open - base_open
    price_keys = (
        "예상시가평균%", "예상고가%", "예상저가%", "익일평균%",
        "예상시가하단%", "예상시가상단%", "예상종가하단%", "예상종가상단%",
    )
    for key in price_keys:
        original = result.get(f"NXT보정전_{key}", np.nan)
        if pd.notna(original):
            result[key] = round(float(np.clip(float(original) + shift, -30, 30)), 2)
    result["NXT 현재가"] = round(nxt_price)
    result["NXT_KRX괴리율%"] = round(nxt_gap, 2)
    result["NXT시가보정비중%"] = round(blend * 100)
    result["NXT시가보정상태"] = "오전 NXT 반영"
    result["NXT보정시각"] = now.strftime("%Y-%m-%d %H:%M")
    return result


@st.cache_data(ttl=300, show_spinner=False)
def kospi_next_session_prediction(lookback_days, min_samples, macro_current):
    start = (date.today() - timedelta(days=max(int(lookback_days) * 3, 1095))).isoformat()
    try:
        raw = fdr.DataReader("KS11", start)
        frame = prep(raw)
        if len(frame) < 100:
            return {"예측상태": "가격 데이터 부족", "표본수": 0}
        kospi_return = frame["Close"].pct_change() * 100
        history_macro = macro_prediction_history(start)
        close_result = similar_prediction(
            frame, kospi_return, horizon=1, min_samples=int(min_samples), stop_pct=2.0,
            macro_history=history_macro,
        )
        live_result = similar_prediction(
            frame, kospi_return, horizon=1, min_samples=int(min_samples), stop_pct=2.0,
            macro_history=history_macro, macro_current=dict(macro_current),
        )
        result = merge_close_and_live_forecasts(close_result, live_result)
        result["기준지수"] = float(frame["Close"].iloc[-1])
        result["기준일"] = pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d")
        return result
    except Exception as exc:
        return {"예측상태": "산출 실패", "표본수": 0, "예측오류": type(exc).__name__}


def render_kospi_next_prediction(market_data, lookback_days, min_samples):
    st.subheader("익일 코스피 예상 수치")
    st.markdown(
        """
        <div style="margin:-.25rem 0 .8rem;color:#a94a4a;font-size:clamp(.7rem,2.3vw,.8rem);line-height:1.45;">
        ※ 과거 유사 시장과 거시지표를 이용한 통계적 참고값이며 실제 익일 지수나 수익을 보장하지 않습니다.
        돌발 뉴스·정책·환율 및 야간시장 변화에 따라 결과가 달라질 수 있습니다.
        </div>
        """, unsafe_allow_html=True,
    )
    macro_current = current_macro_features(market_data)
    result = kospi_next_session_prediction(
        lookback_days, min_samples, tuple(sorted(macro_current.items()))
    )
    if result.get("예측상태") != "산출":
        st.warning(f"코스피 예상 수치를 산출하지 못했습니다. 상태: {result.get('예측상태', '데이터 없음')}")
        return

    def probability(key):
        value = result.get(key, np.nan)
        return "데이터 없음" if pd.isna(value) else f"{float(value):.0f}%"

    def percent_range(low_key, high_key):
        low, high = result.get(low_key, np.nan), result.get(high_key, np.nan)
        if pd.isna(low) or pd.isna(high):
            return "데이터 없음"
        return f"{float(low):+.2f}% ~ {float(high):+.2f}%"

    def close_percent_range(low_key, high_key):
        low, high = result.get(f"장마감_{low_key}", np.nan), result.get(f"장마감_{high_key}", np.nan)
        if pd.isna(low) or pd.isna(high):
            return "데이터 없음"
        return f"{float(low):+.2f}% ~ {float(high):+.2f}%"

    c1, c2, c3 = st.columns(3)
    c1.metric("익일 상승확률", probability("익일승률%"))
    c2.metric("익일 하락확률", probability("익일하락확률%"))
    c3.metric("익일 보합확률", probability("익일보합확률%"))
    c1, c2, c3 = st.columns(3)
    c1.metric("갭상승확률", probability("갭상승확률%"))
    c2.metric("갭하락확률", probability("갭하락확률%"))
    c3.metric("보합출발확률", probability("보합출발확률%"))
    c1, c2 = st.columns(2)
    c1.metric("장 마감 기준 시가 범위", close_percent_range("예상시가하단%", "예상시가상단%"))
    c2.metric("장 마감 기준 종가 범위", close_percent_range("예상종가하단%", "예상종가상단%"))
    c1, c2 = st.columns(2)
    c1.metric("실시간 보정 시가 범위", percent_range("예상시가하단%", "예상시가상단%"))
    c2.metric("실시간 보정 종가 범위", percent_range("예상종가하단%", "예상종가상단%"))

    st.caption(
        f"기준일 {result.get('기준일', '-')} · 코스피 {result.get('기준지수', np.nan):,.2f} · "
        f"유사표본 {int(result.get('표본수', 0))}회 · 신뢰도 {result.get('신뢰도', '-')}"
    )
    st.caption(f"반영 변수: {result.get('거시지표반영', '국내시장만 반영')}")
    st.caption(
        f"실시간 보정 기준 {result.get('실시간보정시각', '-')} · "
        f"{result.get('실시간참고지표', '-')}. 새로고침 시 최신 지표로 다시 계산됩니다."
    )
    night_quote = current_kospi200_night()
    if night_quote:
        st.info(
            f"코스피200 야간선물 {night_quote.get('변동률%', 0):+.2f}% · "
            f"거래량 {night_quote.get('거래량', 0):,} · 야간 참고신호"
        )
    else:
        st.caption("코스피200 야간선물은 야간장·개장 전 수신 가능 시간에 표시됩니다.")


def ranking_score(stock_score, market_score, sector_score, bt, rr):
    # 검증 전 설계 가중치: 기술 45%, 시장 15%, 업종 10%, 승률 15%, 손익비 10%, 표본 신뢰도 5%
    win = bt.get("5일승률%", np.nan); win = 50 if pd.isna(win) else win
    rr_score = np.clip(rr / 3 * 100, 0, 100)
    sample_score = np.clip(bt.get("표본수", 0) / 30 * 100, 0, 100)
    return round(.45 * stock_score + .15 * market_score + .10 * sector_score + .15 * win + .10 * rr_score + .05 * sample_score, 1)


def load_stock_daily_prices(symbol, market, start):
    """Load KRX daily OHLCV with an independent Yahoo fallback."""
    required = {"Open", "High", "Low", "Close", "Volume"}
    frames = []
    for fetch_start in (start, (date.today() - timedelta(days=900)).isoformat()):
        try:
            candidate = fdr.DataReader(str(symbol).zfill(6), fetch_start)
            if candidate is not None and required.issubset(candidate.columns):
                frames.append(candidate)
                if len(candidate) >= 130:
                    return candidate
        except Exception:
            continue
    if frames:
        best = max(frames, key=len)
        if len(best) >= 80:
            return best
    if YF_OK:
        suffix = ".KS" if str(market).upper().startswith("KOSPI") else ".KQ"
        ticker = f"{str(symbol).zfill(6)}{suffix}"
        try:
            candidate = yf.download(ticker, period="5y", interval="1d", auto_adjust=False,
                                    progress=False, threads=False)
            if isinstance(candidate.columns, pd.MultiIndex):
                candidate.columns = candidate.columns.get_level_values(0)
            if required.issubset(candidate.columns) and len(candidate) >= 80:
                return candidate
        except Exception:
            pass
    return max(frames, key=len) if frames else pd.DataFrame()


def analyze(symbol, name, market, sector_text, start, p, do_bt, market_score, market_data, forecast_mode="auto"):
    try:
        raw = load_stock_daily_prices(symbol, market, start)
        latest_prices = pd.to_numeric(raw["Close"], errors="coerce").dropna()
        current_price = float(latest_prices.iloc[-1]) if len(latest_prices) else np.nan
        current_change = (
            (float(latest_prices.iloc[-1]) / float(latest_prices.iloc[-2]) - 1) * 100
            if len(latest_prices) >= 2 and float(latest_prices.iloc[-2]) != 0 else np.nan
        )
        latest_price_date = (
            pd.Timestamp(raw.index[-1]).strftime("%Y-%m-%d") if len(raw) else "-"
        )
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        market_hours = now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (15, 40)
        latest_is_today = len(raw) and pd.Timestamp(raw.index[-1]).date() == now.date()
        today_open = (
            float(pd.to_numeric(pd.Series([raw["Open"].iloc[-1]]), errors="coerce").iloc[0])
            if latest_is_today and "Open" in raw.columns else np.nan
        )
        use_previous_close = forecast_mode == "today" or (forecast_mode == "auto" and market_hours)
        if use_previous_close and latest_is_today:
            raw = raw.iloc[:-1]  # 오늘 종가 예측은 장중 미완성 일봉을 제외
        df = prep(raw)
        if len(df) < 80: return None
        mr = benchmark(market, start)
        f = row_features(df, mr, -1, p)
        cat, sector_score = sector_environment(name, sector_text, market_score, market_data)
        levels = trade_levels(df)
        macro_history = macro_prediction_history(start)
        if do_bt:
            close_bt = similar_prediction(
                df, mr, p["prediction_horizon"], p["min_prediction_samples"],
                levels["손절률%"], macro_history, sector_category=cat,
            )
            live_bt = similar_prediction(
                df, mr, p["prediction_horizon"], p["min_prediction_samples"],
                levels["손절률%"], macro_history, current_macro_features(market_data), cat,
            )
            bt = merge_close_and_live_forecasts(close_bt, live_bt, now.strftime("%Y-%m-%d %H:%M"))
            bt = apply_stock_specific_open_signal(bt, symbol, df)
            bt = apply_nxt_premarket_open_signal(bt, symbol, float(df["Close"].iloc[-1]), now)
        else:
            bt = {"표본수": 0, "예측상태": "사용 안 함", "신뢰도": "-"}
        combined = round(.60 * f["score"] + .25 * market_score + .15 * sector_score, 1)
        decision = "매수후보" if f["hard"] and f["score"] >= p["min_score"] else "조건근접" if f["score"] >= p["min_score"] - 20 or len(f["failures"]) <= 3 else "제외"
        r = df.iloc[-1]
        out = {"종목코드": symbol, "종목명": name, "시장": market, "업종분류": cat, "날짜": df.index[-1].strftime("%Y-%m-%d"),
               "예측모드": forecast_mode,
               "종가": round(float(r["Close"])), "등락률%": round(float(r["RET1"]), 2),
               "현재가": round(current_price) if pd.notna(current_price) else round(float(r["Close"])),
               "현재등락률%": round(current_change, 2) if pd.notna(current_change) else round(float(r["RET1"]), 2),
               "현재가구분": "장중 최신 확인가" if market_hours and latest_is_today else "최근 KRX 종가",
               "현재가기준일": latest_price_date,
               "현재가조회시각": now.strftime("%Y-%m-%d %H:%M:%S"),
               "오늘시가": round(today_open) if pd.notna(today_open) else np.nan,
               "종목점수": f["score"],
               "시장환경": market_score, "업종환경": sector_score, "종합점수": combined, "판정": decision,
               "탈락사유": "없음" if not f["failures"] else " / ".join(f["failures"]), "유형": f["type"], "RSI14": round(float(r["RSI"]), 1),
               "거래량배수": round(f["vr"], 2), "종가위치%": round(f["close_pos"], 1), "윗꼬리%": round(f["wick"], 1),
               "시장대비강도%p": round(f["rel"], 2), "OBV": "충족" if f["obv"] else "미충족", "CVD Proxy": "충족" if f["cvd"] else "미충족"}
        try:
            structure = analyze_daily_structure(raw, {
                "swing_window": int(p.get("swing_window", 3)),
                "swing_atr_multiple": float(p.get("swing_atr_multiple", 1.0)),
                "swing_min_change_pct": float(p.get("swing_min_change_pct", 2.0)),
            })
            structure["차트 구조 점수"] = calculate_structure_score(structure) if structure.get("차트구조") != "데이터 부족" else np.nan
        except Exception as structure_exc:
            structure = {
                "차트구조": "계산 오류", "차트 구조 점수": np.nan,
                "구름위치": "데이터 없음", "HL/HH": "데이터 없음", "BB상태": "데이터 없음",
                "구조판정근거": f"구조 모듈 오류: {type(structure_exc).__name__}",
            }
        out.update(structure)
        out.update(bt); out.update(levels)
        pattern, _ = rebound_snapshot(symbol, market, p.get("pattern_approach_pct", 3.0))
        out.update(pattern)
        out["패턴 접근범위%"] = p.get("pattern_approach_pct", 3.0)
        out["최종순위점수"] = ranking_score(f["score"], market_score, sector_score, bt, levels["1차손익비R"])
        return out
    except Exception as exc:
        ANALYSIS_ERRORS[str(symbol).zfill(6)] = f"{type(exc).__name__}: {exc}"
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
                                    p["min_prediction_samples"], stop_pct,
                                    macro_prediction_history(start))
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
@st.cache_data(ttl=600, show_spinner=False)
def listings():
    x = pd.DataFrame()
    try:
        x = fdr.StockListing("KRX").copy()
    except Exception:
        pass
    # The combined KRX endpoint occasionally returns an empty frame even while
    # the separate market endpoints remain available.
    if x.empty:
        frames = []
        for market_name in ("KOSPI", "KOSDAQ"):
            try:
                market_frame = fdr.StockListing(market_name).copy()
                if not market_frame.empty:
                    if "Market" not in market_frame.columns:
                        market_frame["Market"] = market_name
                    frames.append(market_frame)
            except Exception:
                continue
        if frames:
            x = pd.concat(frames, ignore_index=True, sort=False)
    if x.empty and PYKRX_OK:
        try:
            tickers = krx_stock.get_market_ticker_list(market="ALL")
            x = pd.DataFrame({"Code": tickers, "Name": [krx_stock.get_market_ticker_name(t) for t in tickers]})
        except Exception:
            x = pd.DataFrame(columns=["Code", "Name"])
    code_col = next((c for c in ("Code", "Symbol") if c in x.columns), None)
    if not code_col or "Name" not in x.columns: return pd.DataFrame()
    x[code_col] = x[code_col].astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    x["Name"] = x["Name"].astype(str).map(lambda value: unicodedata.normalize("NFC", value).strip())
    x = x[(x[code_col].str.len() == 6) & x["Name"].ne("")]
    return x.rename(columns={code_col: "Code"}).drop_duplicates("Code").reset_index(drop=True)


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
    updated = apply_nxt_premarket_open_signal(row, row["종목코드"], row["종가"])
    return {**updated, **values}


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


def rebound_chart(symbol, row):
    pattern, raw = rebound_snapshot(symbol, row.get("시장", "KOSPI"), row.get("패턴 접근범위%", 3.0))
    if raw.empty or pattern.get("추정 패턴 상태") != "산출":
        return None
    view = raw.tail(180)
    fig = go.Figure(go.Candlestick(x=view.index, open=view.Open, high=view.High, low=view.Low,
                                   close=view.Close, name="60분봉"))
    for label, key, color in (("반등 기준", "추정 반등가", "#ff1493"),
                              ("+33%", "+33% 가격", "#ff69b4"),
                              ("+50%", "+50% 가격", "#8e44ad"),
                              ("+100%", "+100% 가격", "#2f6fed")):
        price = float(pattern[key])
        fig.add_hline(y=price, line_color=color, line_width=2,
                      annotation_text=f"{label} {price:,.0f}원", annotation_position="top right")
    fig.update_layout(height=620, xaxis_rangeslider_visible=False, margin=dict(l=20, r=20, t=30, b=20))
    return fig


def accumulation_snapshot(row, summary):
    """Score observable accumulation traces; never identifies a specific trader."""
    score, reasons, risks = 0, [], []
    vr = float(row.get("거래량배수", 0) or 0)
    close_pos = float(row.get("종가위치%", 50) or 50)
    wick = float(row.get("윗꼬리%", 100) or 100)
    relative = float(row.get("시장대비강도%p", 0) or 0)

    if vr >= 3:
        score += 20; reasons.append(f"거래량 {vr:.2f}배 급증")
    elif vr >= 2:
        score += 17; reasons.append(f"거래량 {vr:.2f}배 증가")
    elif vr >= 1.5:
        score += 13; reasons.append(f"거래량 {vr:.2f}배")
    elif vr >= 1.2:
        score += 8
    else:
        risks.append(f"거래량 {vr:.2f}배로 뚜렷한 증가 없음")

    if row.get("OBV") == "충족":
        score += 15; reasons.append("OBV 상승")
    else:
        risks.append("OBV 미충족")
    if row.get("CVD Proxy") == "충족":
        score += 15; reasons.append("CVD Proxy 매수 우위")
    else:
        risks.append("CVD Proxy 미충족")

    if close_pos >= 85:
        score += 15; reasons.append(f"종가가 고가권({close_pos:.0f}%)")
    elif close_pos >= 75:
        score += 11; reasons.append(f"종가위치 {close_pos:.0f}%")
    elif close_pos >= 60:
        score += 6
    else:
        risks.append(f"종가위치 낮음({close_pos:.0f}%)")

    if wick <= 15:
        score += 10; reasons.append(f"윗꼬리 짧음({wick:.0f}%)")
    elif wick <= 25:
        score += 7
    elif wick <= 35:
        score += 4
    else:
        risks.append(f"윗꼬리 과다({wick:.0f}%)")

    if relative >= 3:
        score += 10; reasons.append(f"시장 대비 +{relative:.1f}%p")
    elif relative >= 1:
        score += 7; reasons.append(f"시장 대비 +{relative:.1f}%p")
    elif relative >= 0:
        score += 3
    else:
        risks.append(f"시장 대비 {relative:.1f}%p")

    foreign = summary.get("외국인5일순매수(억원)", np.nan)
    if pd.notna(foreign):
        if float(foreign) > 0:
            score += 10; reasons.append(f"외국인 5일 {float(foreign):+,.1f}억원")
        else:
            risks.append(f"외국인 5일 {float(foreign):+,.1f}억원")
    else:
        risks.append("외국인 수급 조회 불가")

    short_ratio = summary.get("공매도잔고비중%", summary.get("최근공매도비중%", np.nan))
    if pd.notna(short_ratio):
        if float(short_ratio) <= 3:
            score += 5
        elif float(short_ratio) <= 7:
            score += 2
        elif float(short_ratio) >= 10:
            risks.append(f"공매도 비중 높음({float(short_ratio):.1f}%)")

    score = int(max(0, min(100, round(score))))
    if vr >= 1.5 and close_pos >= 75 and score >= 70:
        stage = "거래량 동반 매집·돌파 가능성"
    elif score >= 65:
        stage = "매집 가능성 높음"
    elif score >= 50:
        stage = "매집 흔적 관찰"
    elif vr >= 1.5 and wick > 35:
        stage = "분산(물량 정리) 주의"
    else:
        stage = "뚜렷한 매집 흔적 낮음"
    return {
        "점수": score, "단계": stage,
        "근거": " · ".join(reasons[:5]) if reasons else "뚜렷한 긍정 신호 없음",
        "위험": " · ".join(risks[:4]) if risks else "두드러진 위험 신호 없음",
    }


def show_accumulation(row, summary):
    result = accumulation_snapshot(row, summary)
    st.markdown("### 매집 흔적")
    st.caption(f"위험 신호: {result['위험']}")
    st.caption("※ 거래량·가격·수급의 정황 점수이며 특정 세력이나 계좌의 진입을 확인한 결과가 아닙니다.")
    c1, c2 = st.columns(2)
    c1.metric("매집 흔적 점수", f"{result['점수']}/100")
    c2.metric("현재 단계", result["단계"])
    if result["점수"] >= 65:
        st.success(f"근거: {result['근거']}")
    else:
        st.info(f"근거: {result['근거']}")


@st.cache_data(ttl=300, show_spinner=False)
def detail_prediction_snapshot(symbol, market, sector_category, price_date, horizon, min_samples, stop_pct, lookback_days):
    """Recalculate missing scan statistics with a longer history for detail view."""
    symbol = str(symbol).zfill(6)
    extended_days = max(int(lookback_days) * 3, 1095)
    start = (date.today() - timedelta(days=extended_days)).isoformat()
    try:
        raw = fdr.DataReader(symbol, start)
        history = prep(raw)
        if history.empty:
            return {"표본수": 0, "예측상태": "가격 데이터 부족", "신뢰도": "산출 불가"}

        # Keep the forecast anchored to the same confirmed close used by the scan.
        cutoff = pd.to_datetime(price_date, errors="coerce")
        if pd.notna(cutoff):
            history = history[pd.to_datetime(history.index) <= cutoff]
        if len(history) < 80:
            return {"표본수": 0, "예측상태": "가격 데이터 부족", "신뢰도": "산출 불가"}

        market_history = benchmark(market, start).reindex(history.index)
        macro_history = macro_prediction_history(start)
        close_result = similar_prediction(
            history, market_history, horizon, min_samples, stop_pct, macro_history,
            sector_category=sector_category,
        )
        live_result = similar_prediction(
            history, market_history, horizon, min_samples, stop_pct, macro_history,
            current_macro_features(market_data), sector_category,
        )
        result = merge_close_and_live_forecasts(close_result, live_result)
        result = apply_stock_specific_open_signal(result, symbol, history)
        result = apply_nxt_premarket_open_signal(result, symbol, float(history["Close"].iloc[-1]))
        result["예측계산기준"] = f"상세보기 확장 재계산 · 최근 약 {extended_days // 365}년"
        return result
    except Exception as exc:
        return {
            "표본수": 0,
            "예측상태": "재계산 실패",
            "신뢰도": "산출 불가",
            "예측오류": type(exc).__name__,
        }


def ensure_detail_prediction(row):
    """Hydrate detail statistics even when scan-time backtesting was disabled/insufficient."""
    hydrated = dict(row)
    required_gap_keys = {"갭상승확률%", "갭하락확률%", "보합출발확률%", "거시지표반영", "실시간보정상태"}
    if hydrated.get("예측상태") == "산출" and required_gap_keys.issubset(hydrated):
        hydrated.setdefault("예측계산기준", "후보 스캔 시 계산")
        return hydrated

    horizon = int(p.get("prediction_horizon", 5))
    min_samples = int(p.get("min_prediction_samples", 20))
    stop_pct = float(hydrated.get("손절률%", 3.0) or 3.0)
    refreshed = detail_prediction_snapshot(
        hydrated.get("종목코드", ""),
        hydrated.get("시장", "KOSPI"),
        hydrated.get("업종분류", "일반"),
        hydrated.get("날짜"),
        horizon,
        min_samples,
        stop_pct,
        int(p.get("lookback", 365)),
    )
    hydrated.update(refreshed)
    return hydrated


def show_detail(row):
    if st.button("← 간편보기로 돌아가기", use_container_width=True, key="back_to_simple_view"):
        st.session_state["scanner_v5_selected_mode"] = "simple"
        st.query_params["view"] = "simple"
        st.rerun()
    st.subheader(f"{row['종목명']} ({row['종목코드']}) 핵심 요약")
    if row.get("예측상태") != "산출" or not {"갭하락확률%", "보합출발확률%", "거시지표반영", "실시간보정상태"}.issubset(row):
        with st.spinner("상세 통계를 확장 데이터로 다시 계산 중..."):
            row = ensure_detail_prediction(row)
        st.session_state["scanner_v5_selected"] = row
    with st.spinner("수급·공매도 데이터 확인 중..."):
        summary, flow, short = flow_and_short(row["종목코드"])

    foreign = summary.get("외국인5일순매수(억원)", np.nan)
    foreign_text = "조회 불가" if pd.isna(foreign) else f"{foreign:+,.1f}억원"
    short_ratio = summary.get("공매도잔고비중%", summary.get("최근공매도비중%", np.nan))
    short_text = "KRX 연결 필요" if pd.isna(short_ratio) else f"{short_ratio:.2f}%"
    sample_n = int(row.get("표본수", 0) or 0)
    prediction_ok = row.get("예측상태") == "산출"
    minimum_n = int(p.get("min_prediction_samples", 20))
    prediction_status = str(row.get("예측상태", "산출 불가"))
    if sample_n:
        unavailable_text = f"표본 {sample_n}회 · 최소 {minimum_n}회 미달"
    elif prediction_status == "사용 안 함":
        unavailable_text = "상세 재계산 불가"
    else:
        unavailable_text = prediction_status
    entry = float(row["진입가"])
    horizon = next((int(k.split("일내")[0]) for k in row.keys() if "일내+3%도달%" in k), 5)
    reach_key = f"{horizon}일내+3%도달%"

    def pct_price(pct):
        return entry * (1 + float(pct) / 100)

    def expected_price(key):
        value = row.get(key, np.nan)
        if not prediction_ok or pd.isna(value):
            return unavailable_text
        return f"{pct_price(value):,.0f}원"

    def probability(key):
        value = row.get(key, np.nan)
        return unavailable_text if not prediction_ok or pd.isna(value) else f"{float(value):.0f}%"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("진입 추천 점수", f"{row['종합점수']:.1f}/100", row["판정"])
    c2.metric("OBV", row.get("OBV", "-"))
    c3.metric("CVD Proxy", row.get("CVD Proxy", "-"))
    c4.metric("RSI(14)", f"{row.get('RSI14', np.nan):.1f}")

    detail_context = prediction_context(row["날짜"], row.get("예측모드", "auto"))
    st.markdown(f"#### {detail_context['구분']} 통계")
    st.caption(row.get("예측계산기준", "후보 스캔 시 계산"))
    st.caption(f"예측 변수: {row.get('거시지표반영', '국내시장만 반영')}")
    st.caption(f"업종별 해외지표 가중: {row.get('업종지표가중', row.get('업종분류', '일반'))}")
    if row.get("ADR보정상태") == "SKHY 등락률 반영":
        st.caption(
            f"SK하이닉스 ADR(SKHY) {row.get('SKHY등락률%', 0):+.2f}% · "
            f"겹침 표본 {int(row.get('ADR겹침표본수', 0))}회 · "
            f"예상 시가 보정비중 {int(row.get('ADR시가보정비중%', 0))}%"
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{detail_context['확률접두어']} 상승 확률", probability("익일승률%"))
    c2.metric("갭상승 확률", probability("갭상승확률%"))
    c3.metric(f"{horizon}일 내 +3%", probability(reach_key))
    c4.metric("초기 손절 도달", probability("초기손절도달확률%"))

    c1, c2 = st.columns(2)
    c1.metric("갭하락 확률", probability("갭하락확률%"))
    c2.metric("보합출발 확률", probability("보합출발확률%"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("예상 시가", expected_price("예상시가평균%"))
    c2.metric("예상 고가", expected_price("예상고가%"))
    c3.metric("예상 저가", expected_price("예상저가%"))
    c4.metric("예상 종가", expected_price("익일평균%"))
    if row.get("실시간보정상태") == "산출":
        st.caption(
            f"{row.get('실시간보정시각', '-')} 기준 실시간 참고지표를 반영한 단일 예상값입니다."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("유사 표본", f"{sample_n}회" if sample_n else unavailable_text)
    c2.metric("예측 신뢰도", row.get("신뢰도", "산출 불가"))
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
    c3.metric("해외 DR·24시간 선물 환산가", won_value("해외24h 환산가"))
    c1, c2, c3 = st.columns(3)
    c1.metric("해외 괴리율", pct_value("해외 괴리율%"))
    c2.metric("NXT 프리미엄", pct_value("NXT 프리미엄%"))
    c3.metric("해외가격 판정", row.get("해외가격 신호", "데이터 없음"))
    product_name = row.get("해외상품명", "매핑 없음")
    price_source = row.get("해외가격 출처", "데이터 없음")
    ratio = row.get("원주환산비율", np.nan)
    ratio_text = f"해외상품 1개 = KRX 원주 {float(ratio):g}주" if pd.notna(ratio) else "환산비율 없음"
    fx_text = f"USD/KRW {row.get('USD/KRW'):,.2f}" if pd.notna(row.get("USD/KRW", np.nan)) else "USD/KRW 데이터 없음"
    st.caption(f"상품: {product_name} · 출처: {price_source} · {ratio_text} · {fx_text}")
    if pd.isna(row.get("해외24h 환산가", np.nan)):
        st.caption(f"표시하지 못한 이유: {row.get('해외가격 신호', '시세 수신 실패')}")
    elif str(row.get("해외상품 유형", "")).lower() in {"perpetual", "perp", "futures"}:
        st.caption("※ 무기한선물 환산가는 현물·ADR 가격이 아니며, 참고용 선물시장 기대가격입니다.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("진입가", f"{row['진입가']:,.0f}원")
    c2.metric("손절가", f"{row['초기손절']:,.0f}원", f"-{row['손절률%']:.2f}%")
    c3.metric("1차 익절가", f"{row['1차익절(+10%)']:,.0f}원", "+10%")
    c4.metric("2차 익절가", f"{row['2차익절(+20%)']:,.0f}원", "+20%")

    st.info("예상가는 확정값이 아니라 과거 유사조건과 ATR14를 결합한 통계적 예상범위입니다. 미래 가격이나 수익을 보장하지 않습니다.")

    with st.expander("자세히 보기 — 탈락사유·전체 지표·차트·일별 수급"):
        st.write(f"**판정:** {row['판정']}  |  **탈락사유:** {row['탈락사유']}")
        details = {k: row.get(k) for k in ["종목점수", "차트구조", "차트 구조 점수", "구름위치", "구름이격상태", "HL/HH", "BB상태", "거래량구조", "시장환경", "업종환경", "최종순위점수", "업종분류", "유형", "RSI14", "거래량배수", "종가위치%", "윗꼬리%", "시장대비강도%p", "OBV", "CVD Proxy", "표본수", "신뢰도", "유사도중앙거리", "익일승률%", "갭상승확률%", "갭하락확률%", "보합출발확률%", "예상시가하단%", "예상시가상단%", "예상종가하단%", "예상종가상단%", "예상고가%", "예상저가%", reach_key, "초기손절도달확률%", "평균MAE%", "손절률%", "1차손익비R", "2차손익비R"]}
        st.dataframe(pd.DataFrame([details]), use_container_width=True, hide_index=True)
        st.markdown("#### 차트 구조 판정 근거")
        st.write(row.get("구조판정근거", "데이터 없음"))
        structure_raw = row.get("구조원본값")
        if isinstance(structure_raw, dict):
            st.dataframe(pd.DataFrame([structure_raw]), use_container_width=True, hide_index=True)
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
        st.markdown("#### 60분봉 추정 반등 패턴")
        try:
            pattern_fig = rebound_chart(row["종목코드"], row)
            if pattern_fig is None:
                st.info("60분봉 표본이 부족하거나 의미 있는 스윙을 찾지 못했습니다.")
            else:
                pattern_keys = ["추정 패턴 점수", "기준 스윙 고점", "기준 스윙 저점", "기준가 결정시간",
                                "고점-50% 대응오차%", "현재 패턴 단계", "다음 목표가", "다음 목표까지(%)"]
                st.dataframe(pd.DataFrame([{k: row.get(k) for k in pattern_keys}]), use_container_width=True, hide_index=True)
                st.plotly_chart(pattern_fig, use_container_width=True)
        except Exception as exc:
            st.warning(f"60분봉 추정 패턴을 불러오지 못했습니다: {exc}")


def krx_market_is_open():
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (15, 40)


def prediction_context(price_date, forecast_mode="auto"):
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    basis = pd.Timestamp(price_date).date()
    market_hours = krx_market_is_open()
    if forecast_mode == "today" or (forecast_mode == "auto" and market_hours):
        return {
            "구분": "오늘 종가 예측", "제목": "오늘 예상 가격", "확률접두어": "오늘",
            "대상": "오늘 장 마감", "기준": f"{basis:%Y-%m-%d} 확정 종가",
            "안내": "장중 미완성 일봉을 제외하고 직전 확정 종가로 오늘 가격을 추정합니다.",
        }
    temporary = forecast_mode == "next" and market_hours
    return {
        "구분": "익일 예측 (장중 임시)" if temporary else "익일 예측",
        "제목": "익일 예상 가격", "확률접두어": "익일",
        "대상": "다음 KRX 거래일", "기준": f"{basis:%Y-%m-%d} {'장중 가격(미확정)' if temporary else '확정 종가'}",
        "안내": "장중 데이터로 계산한 임시 익일 예측이며 장 마감 후 값이 달라질 수 있습니다."
                if temporary else "가장 최근 확정 종가로 다음 KRX 거래일 가격을 추정합니다.",
    }

def show_simple_prediction(row):
    """Mobile-first result used immediately after direct stock lookup."""
    if row.get("예측상태") != "산출" or not {"갭하락확률%", "보합출발확률%", "거시지표반영"}.issubset(row):
        with st.spinner("갭 방향 통계를 계산 중..."):
            row = ensure_detail_prediction(row)
        st.session_state["scanner_v5_selected"] = row
    context = prediction_context(row["날짜"], row.get("예측모드", "auto"))
    st.markdown(f"#### {row['종목명']} ({row['종목코드']})")
    st.info(
        f"**{context['구분']}** · 가격 기준: {context['기준']} · "
        f"예측 대상: {context['대상']}\n\n{context['안내']}"
    )
    current_price = row.get("현재가", row.get("종가", np.nan))
    current_change = row.get("현재등락률%", row.get("등락률%", np.nan))
    current_label = row.get("현재가구분", "최근 KRX 종가")
    price_text = "데이터 없음" if pd.isna(current_price) else f"{float(current_price):,.0f}원"
    change_text = None if pd.isna(current_change) else f"{float(current_change):+.2f}%"
    prediction_ok = row.get("예측상태") == "산출"
    entry = float(row["진입가"])

    today_open = row.get("오늘시가", np.nan)
    open_low_pct = row.get("예상시가하단%", np.nan)
    open_high_pct = row.get("예상시가상단%", np.nan)
    expected_open = (
        entry * (1 + (float(open_low_pct) + float(open_high_pct)) / 200)
        if prediction_ok and pd.notna(open_low_pct) and pd.notna(open_high_pct) else np.nan
    )
    open_gap = (
        (float(today_open) / expected_open - 1) * 100
        if pd.notna(today_open) and pd.notna(expected_open) and expected_open else np.nan
    )
    horizon = next((int(k.split("일내")[0]) for k in row.keys() if "일내+3%도달%" in k), 5)
    reach_key = f"{horizon}일내+3%도달%"

    def pct_price(key):
        value = row.get(key, np.nan)
        if not prediction_ok or pd.isna(value):
            return None
        return entry * (1 + float(value) / 100)

    def single_text(key):
        value = pct_price(key)
        return "표본 부족" if value is None else f"{value:,.0f}원"

    def probability(key):
        value = row.get(key, np.nan)
        return "표본 부족" if not prediction_ok or pd.isna(value) else f"{float(value):.0f}%"

    forecast_cards = [
        ("예상 시가", single_text("예상시가평균%")),
        ("예상 고가", single_text("예상고가%")),
        ("예상 저가", single_text("예상저가%")),
        ("예상 종가", single_text("익일평균%")),
    ]
    st.markdown(f"### {context['제목']}")
    st.markdown(
        """
        <div style="margin:.15rem 0 .8rem; color:#a94a4a;
                    font-size:clamp(.72rem,2.4vw,.82rem); line-height:1.45;">
          ※ 예상가격은 과거 데이터에 기반한 통계적 추정치입니다. 실제 주가는 시장 상황에 따라
          예상치와 다를 수 있습니다. 참고자료로만 사용해야 하며 투자 결과를 보장하거나 책임지지 않습니다.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("""
    <style>
    .forecast-stack {display:grid; grid-template-columns:1fr; gap:.75rem; margin:.25rem 0 1rem;}
    .forecast-card {min-width:0; padding:1rem 1.1rem; border:1px solid #e1e6ef; border-radius:14px;
      background:#fff; box-shadow:0 2px 10px rgba(25,35,55,.035); overflow:hidden;}
    .forecast-title {font-size:clamp(.9rem,3.5vw,1.05rem); color:#667085; margin-bottom:.35rem;}
    .forecast-value {font-size:clamp(1.05rem,4.2vw,1.65rem); font-weight:750; line-height:1.28;
      letter-spacing:-.035em; overflow:visible; overflow-wrap:anywhere; word-break:keep-all;}
    @media (min-width:800px) {.forecast-stack {grid-template-columns:repeat(2,minmax(0,1fr));}}
    </style>
    """, unsafe_allow_html=True)
    cards = "".join(
        f'<div class="forecast-card"><div class="forecast-title">{title}</div>'
        f'<div class="forecast-value">{value}</div></div>'
        for title, value in forecast_cards
    )
    st.markdown(f'<div class="forecast-stack">{cards}</div>', unsafe_allow_html=True)
    if row.get("실시간보정상태") == "산출":
        st.caption(
            f"실시간 보정 기준 {row.get('실시간보정시각', '-')} · "
            f"참고지표: {row.get('실시간참고지표', '-')}. 새로고침 시 최신 지표로 다시 계산됩니다."
        )
    st.caption(
        f"표시된 기준 가격으로부터 {context['대상']} 가격을 추정합니다. 예상 고가·저가는 "
        "확정가격이 아니라 과거 유사신호 분포와 ATR14를 이용한 대표 추정값입니다."
    )

    st.markdown(
        f"""
        <style>
        .current-price-heading {{display:flex; align-items:baseline; justify-content:space-between;
          flex-wrap:wrap; gap:.25rem .75rem; margin:1.15rem 0 .55rem;}}
        .current-price-title {{font-size:clamp(1.25rem,4vw,1.75rem); font-weight:750;
          color:#31333f; line-height:1.25;}}
        .current-price-time {{font-size:clamp(.76rem,2.6vw,.9rem); color:#7a8292; line-height:1.4;}}
        @media (max-width:640px) {{
          .current-price-heading {{align-items:flex-start; flex-direction:column;}}
        }}
        </style>
        <div class="current-price-heading">
          <div class="current-price-title">현재 주가 정보</div>
          <div class="current-price-time">기준일자 {row.get('현재가기준일', row.get('날짜', '-'))}
          · 조회시간 {row.get('현재가조회시각', '-')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if row.get("예측모드") == "today":
        price_col, open_col, gap_col = st.columns(3)
        price_col.metric(current_label, price_text, change_text)
        open_col.metric(
            "오늘 시가",
            "금일 정규장 오픈 대기 중" if pd.isna(today_open) else f"{float(today_open):,.0f}원",
        )
        gap_col.metric(
            "예상 대비 시가 괴리율",
            "시가 확인 후 계산" if pd.isna(today_open) else "표본 부족" if pd.isna(open_gap) else f"{open_gap:+.2f}%",
        )
        st.caption(
            "시가 괴리율은 실제 오늘 시가와 예상 시가 범위의 중앙 대표값을 비교합니다. "
            "양수는 예상보다 높게, 음수는 예상보다 낮게 출발했다는 뜻입니다."
        )
    else:
        st.metric(current_label, price_text, change_text)

    st.caption("장중 가격은 지연될 수 있으며 실시간 체결가를 보장하지 않습니다.")

    st.markdown(f"### {context['확률접두어']} 가능성")
    rise_basis_text = (
        "오늘 상승확률은 전일 확정 종가보다 오늘 종가가 높게 마감한 과거 유사신호의 비율입니다. "
        "오늘 시가나 현재가 대비 상승확률은 아닙니다."
        if row.get("예측모드") == "today" else
        "익일 상승확률은 기준일 확정 종가보다 다음 거래일 종가가 높게 마감한 과거 유사신호의 비율입니다."
    )
    st.markdown(
        f"""
        <div style="margin:.1rem 0 .8rem; color:#697386;
                    font-size:clamp(.72rem,2.4vw,.82rem); line-height:1.5;">
          {rise_basis_text}<br>
          나머지 수치는 현재 조건과 비슷한 과거 유사신호에서 갭상승·갭하락·보합출발·목표가 또는 손절선 도달이
          실제로 발생한 비율입니다.<br>
          <span style="color:#a94a4a;">※ 과거 통계에 기반한 참고자료이며 실제 미래 확률이나 수익을
          보장하지 않습니다. 시장 상황에 따라 결과가 달라질 수 있습니다.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{context['확률접두어']} 상승확률", probability("익일승률%"))
    c2.metric("갭상승확률", probability("갭상승확률%"))
    c3.metric(f"{horizon}일 내 +3%", probability(reach_key))
    c4.metric("손절선 도달확률", probability("초기손절도달확률%"))
    c1, c2 = st.columns(2)
    c1.metric("갭하락확률", probability("갭하락확률%"))
    c2.metric("보합출발확률", probability("보합출발확률%"))
    st.caption("갭상승·갭하락·보합출발은 다음 거래일 시가를 기준일 종가와 비교해 각각 계산합니다.")
    st.caption(f"예측 변수: {row.get('거시지표반영', '국내시장만 반영')}")
    st.caption(f"업종별 해외지표 가중: {row.get('업종지표가중', row.get('업종분류', '일반'))}")
    if row.get("ADR보정상태") == "SKHY 등락률 반영":
        st.caption(
            f"SK하이닉스 ADR(SKHY) {row.get('SKHY등락률%', 0):+.2f}% · "
            f"겹침 표본 {int(row.get('ADR겹침표본수', 0))}회 · "
            f"예상 시가 보정비중 {int(row.get('ADR시가보정비중%', 0))}%"
        )
    night_quote = current_kospi200_night()
    if night_quote:
        st.info(
            f"코스피200 야간선물 {night_quote.get('변동률%', 0):+.2f}% · "
            f"거래량 {night_quote.get('거래량', 0):,} · 현재 야간 참고신호"
        )
    else:
        st.caption("코스피200 야간선물은 야간장·개장 전 수신 가능 시간에 별도 참고신호로 표시됩니다.")

    with st.spinner("매집 흔적과 수급 확인 중..."):
        accumulation_summary, _, _ = flow_and_short(row["종목코드"])
    show_accumulation(row, accumulation_summary)

    st.markdown("### 진입·손절·익절")
    entry_basis_text = (
        "오늘 종가 예측의 기준 진입가는 직전 거래일의 확정 종가입니다."
        if row.get("예측모드") == "today" else
        "익일 예측의 기준 진입가는 조회 시점에 사용된 마지막 일봉의 종가입니다. 장중에는 지연된 최신 가격일 수 있습니다."
    )
    st.markdown(
        f"""
        <div style="margin:.1rem 0 .8rem; color:#697386;
                    font-size:clamp(.72rem,2.4vw,.82rem); line-height:1.5;">
          {entry_basis_text}<br>
          <span style="color:#a94a4a;">※ 진입가는 해당 가격에 매수하라는 의미가 아니라 손절가와
          1차·2차 익절가를 계산하기 위한 기준가격입니다. 참고자료로만 사용하세요.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("기준 진입가", f"{row['진입가']:,.0f}원")
    c2.metric("손절가", f"{row['초기손절']:,.0f}원", f"-{row['손절률%']:.2f}%")
    c3.metric("1차 익절가", f"{row['1차익절(+10%)']:,.0f}원", "+10%")
    c4.metric("2차 익절가", f"{row['2차익절(+20%)']:,.0f}원", "+20%")

    if st.button("자세히 보기", use_container_width=True):
        with st.spinner("상세 데이터 불러오는 중..."):
            st.session_state["scanner_v5_selected"] = enrich_after_hours(dict(row))
        st.session_state["scanner_v5_selected_mode"] = "detail"
        st.query_params["view"] = "detail"
        st.rerun()


def current_price_label(row):
    price = row.get("현재가", row.get("종가", np.nan))
    change = row.get("현재등락률%", row.get("등락률%", np.nan))
    if pd.isna(price):
        return "데이터 없음"
    if pd.isna(change):
        return f"{price:,.0f}원"
    arrow = "▲" if change > 0 else "▼" if change < 0 else "—"
    sign = "+" if change > 0 else ""
    return f"{price:,.0f}원 ({arrow} {sign}{change:.2f}%)"


def scanner_table(frame):
    data = frame.copy()
    core = ["종목코드", "종목명", "차트구조", "차트 구조 점수", "구름위치", "HL/HH", "BB상태", "NXT갭", "NXT유지력", "NXT패턴"]
    ordered = [c for c in core if c in data.columns] + [c for c in data.columns if c not in core]
    data = data[ordered]
    price_column = "현재가 (등락률)"
    if price_column in data.columns:
        hidden = {"현재가", "현재등락률%", "종가", "등락률%"}
        columns = [c for c in data.columns if c not in hidden and c != price_column]
        insert_at = columns.index("종목명") + 1 if "종목명" in columns else 0
        columns.insert(insert_at, price_column)
        data = data[columns]

    color_cols = [c for c in ("해외 괴리율%", "NXT 프리미엄%") if c in data.columns]
    if not color_cols and price_column not in data.columns:
        return data

    style = data.style
    if price_column in data.columns:
        def price_direction(value):
            text = str(value)
            if "▲" in text: return "color: #d14b4b; font-weight: 700"
            if "▼" in text: return "color: #2f6fed; font-weight: 700"
            return "color: #7a8292; font-weight: 700"
        style = style.map(price_direction, subset=[price_column])

    if color_cols:
        def color_value(value):
            if pd.isna(value): return "color: #8b93a5"
            return "color: #2e9d50; font-weight: 700" if value > 0 else "color: #d14b4b; font-weight: 700" if value < 0 else ""
        style = style.map(color_value, subset=color_cols).format(
            {col: "{:+.2f}%" for col in color_cols}, na_rep="데이터 없음")
    return style

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
    render_scan_settings("sidebar")

market_filter = st.session_state["scanner_v5_setting_market_filter"]
max_symbols = st.session_state["scanner_v5_setting_max_symbols"]
min_value = st.session_state["scanner_v5_setting_min_value"]
min_price = st.session_state["scanner_v5_setting_min_price"]
min_vr = st.session_state["scanner_v5_setting_min_vr"]
rlo, rhi = st.session_state["scanner_v5_setting_rsi_range"]
close_pos = st.session_state["scanner_v5_setting_close_pos"]
max_wick = st.session_state["scanner_v5_setting_max_wick"]
min_rel = st.session_state["scanner_v5_setting_min_rel"]
max_gap = st.session_state["scanner_v5_setting_max_gap"]
min_score = st.session_state["scanner_v5_setting_min_score"]
lookback = st.session_state["scanner_v5_setting_lookback"]
do_bt = st.session_state["scanner_v5_setting_do_bt"]
prediction_horizon = st.session_state["scanner_v5_setting_prediction_horizon"]
min_prediction_samples = st.session_state["scanner_v5_setting_min_prediction_samples"]
workers = st.session_state["scanner_v5_setting_workers"]
pattern_approach_pct = st.session_state["scanner_v5_setting_pattern_approach_pct"]
pattern_filter = st.session_state["scanner_v5_setting_pattern_filter"]
swing_window = st.session_state["scanner_v5_setting_swing_window"]
swing_atr_multiple = st.session_state["scanner_v5_setting_swing_atr_multiple"]
swing_min_change_pct = st.session_state["scanner_v5_setting_swing_min_change_pct"]

p = {"min_value": min_value * 1e8, "min_price": min_price, "min_vr": min_vr, "rsi_lo": rlo, "rsi_hi": rhi,
     "close_pos": close_pos, "max_wick": max_wick, "min_rel": min_rel, "max_gap": max_gap, "min_score": min_score,
     "lookback": lookback, "prediction_horizon": prediction_horizon,
     "min_prediction_samples": min_prediction_samples}
p["pattern_approach_pct"] = pattern_approach_pct
p.update({"swing_window": swing_window, "swing_atr_multiple": swing_atr_multiple,
          "swing_min_change_pct": swing_min_change_pct})

with st.spinner("시장환경 확인 중..."):
    market_score, market_label, market_data, market_reasons = market_environment()

L = listings()
st.subheader("조회 방식")
search_mode = st.radio(
    "조회 방식 선택",
    ["오늘 종가 예측", "익일 예측", "과거 날짜 검증"],
    horizontal=True,
    label_visibility="collapsed",
)
search_mode_help = {
    "오늘 종가 예측": (
        "평일 개장 전 또는 09:00~15:40 장중에 사용합니다. 개장 전에는 오늘 시가를 오픈 대기 중으로 표시하고, "
        "직전 확정 종가를 기준으로 오늘 종가를 예측합니다."
    ),
    "익일 예측": (
        "다음 KRX 거래일의 가격을 예측합니다. "
        "장중에 조회하면 미완성 당일 데이터를 사용한 임시 익일 예측으로 표시됩니다."
    ),
    "과거 날짜 검증": (
        "선택한 과거 날짜의 종가까지만 사용해 다음 거래일 예측값과 실제 가격을 비교합니다."
    ),
}
st.caption(f"ℹ️ {search_mode_help[search_mode]}")
selected_validation_date = None
if search_mode == "과거 날짜 검증":
    selected_validation_date = st.date_input(
        "기준 날짜", value=date.today() - timedelta(days=1),
        min_value=date.today() - timedelta(days=730), max_value=date.today() - timedelta(days=1),
        help="선택한 날의 종가까지 알려졌다고 가정해 다음 거래일 종가를 예측합니다.")

st.subheader("직접 종목검색")
st.caption("아래 검색창에 종목명 두 글자 이상 또는 종목코드를 입력하면 일치 종목이 바로 표시됩니다.")
for obsolete_search_key in (
    "scanner_v5_stock_search_options", "scanner_v5_stock_search_submitted",
    "scanner_v5_stock_search_result", "scanner_v5_stock_search_query",
):
    st.session_state.pop(obsolete_search_key, None)
stock_options = [""]
if len(L):
    stock_options += [
        f"{row['Name']} ({str(row['Code']).zfill(6)})"
        for _, row in L.sort_values(["Name", "Code"]).iterrows()
    ]
selected_stock = st.selectbox(
    "종목명 또는 종목코드 검색",
    stock_options,
    index=0,
    key="scanner_v5_direct_stock_dropdown_v2",
    placeholder="예: 삼성 또는 005930",
    help="검색창에 글자를 입력하면 일치하는 종목 목록이 드롭다운 안에 바로 표시됩니다.",
)
if not len(L):
    st.warning("KRX 종목 목록을 불러오지 못했습니다. 잠시 후 새로고침해 주세요.")

move_clicked = st.button(
    "이 종목 예상가 확인하기",
    type="primary",
    use_container_width=True,
    disabled=not bool(selected_stock),
    key="scanner_v5_direct_stock_submit",
)

direct_status = st.empty()
if st.session_state.get("scanner_v5_direct_notice"):
    direct_status.success(st.session_state["scanner_v5_direct_notice"])

query = selected_stock
matches = pd.DataFrame()
if selected_stock and len(L):
    selected_code = selected_stock.rsplit("(", 1)[-1].rstrip(")")
    listing_codes = L["Code"].astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    matches = L[listing_codes == selected_code].head(1)
if move_clicked:
    st.session_state["scanner_v5_hide_market"] = True
    for state_key in (
        "scanner_v5_all", "scanner_v5_result_mode", "scanner_v5_scan_notice",
        "scanner_v5_selected", "scanner_v5_selected_mode", "scanner_v5_validation",
    ):
        st.session_state.pop(state_key, None)
    if "view" in st.query_params:
        del st.query_params["view"]
    st.session_state["scanner_v5_direct_notice"] = "분석 중입니다. 완료되면 결과를 이 안내 아래에서 확인할 수 있습니다."
    direct_status.info("🔎 종목을 분석하고 있습니다. 완료 후 아래에서 결과를 확인하세요.")

if move_clicked and len(matches):
    r = matches.iloc[0]
    mkt = str(r.get("Market", "KOSPI")); sec = str(r.get("Sector", r.get("Industry", "")))
    if search_mode == "과거 날짜 검증":
        with st.spinner("선택한 날짜 기준으로 검증 중..."):
            validation = validate_at_date(r.Code, selected_validation_date, mkt, p, lookback)
        st.session_state["scanner_v5_validation"] = {"종목명": r.Name, "종목코드": r.Code, "결과": validation}
        st.session_state["scanner_v5_direct_notice"] = "✅ 검증이 완료되었습니다. 결과를 아래에서 확인하세요."
        direct_status.success(st.session_state["scanner_v5_direct_notice"])
    else:
        now_krx = datetime.now(ZoneInfo("Asia/Seoul"))
        after_regular_close = (now_krx.hour, now_krx.minute) > (15, 40)
        if search_mode == "오늘 종가 예측" and (now_krx.weekday() >= 5 or after_regular_close):
            st.warning("오늘 종가 예측은 평일 개장 전 또는 09:00~15:40 장중에 사용할 수 있습니다. 장 종료 후에는 익일 예측을 선택해주세요.")
            st.stop()
        with st.spinner("종목 분석 중..."):
            forecast_mode = "today" if search_mode == "오늘 종가 예측" else "next"
            result = analyze(
                r.Code, r.Name, mkt, sec,
                (date.today() - timedelta(days=lookback)).isoformat(),
                p, True, market_score, market_data,
                forecast_mode=forecast_mode,
            )
        if result:
            with st.spinner("매집 흔적 확인 중..."):
                accumulation_summary, _, _ = flow_and_short(result["종목코드"])
            accumulation = accumulation_snapshot(result, accumulation_summary)
            result.update({
                "매집 흔적 점수": accumulation["점수"],
                "매집 단계": accumulation["단계"],
                "매집 근거": accumulation["근거"],
                "매집 위험": accumulation["위험"],
            })
            st.session_state["scanner_v5_selected"] = result
            st.session_state["scanner_v5_selected_mode"] = "simple"
            st.session_state["scanner_v5_direct_notice"] = "✅ 분석이 완료되었습니다. 결과를 아래에서 확인하세요."
            direct_status.success(st.session_state["scanner_v5_direct_notice"])
            st.query_params["view"] = "simple"
        else:
            error_detail = ANALYSIS_ERRORS.get(str(r.Code).zfill(6), "가격 데이터 조회 실패")
            st.session_state["scanner_v5_direct_notice"] = f"분석 실패 · {error_detail}"
            direct_status.warning(st.session_state["scanner_v5_direct_notice"])
elif move_clicked and query:
    st.session_state["scanner_v5_direct_notice"] = "일치하는 KRX 종목이 없습니다."
    direct_status.warning(st.session_state["scanner_v5_direct_notice"])

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

st.markdown("### 매수 후보를 찾고 싶다면")
st.caption(
    f"빠른 검색을 위해 기본 {max_symbols}개 종목만 분석합니다. 더 많은 종목을 검색하려면 "
    "위의 ‘⚙️ 상세 조건 설정하기’에서 스캔 종목 수를 변경하세요."
)
scan_clicked = st.button("오늘 종가 매매 후보 찾아보기", type="primary", use_container_width=True)
accumulation_scan_clicked = st.button("매집 흔적 있는 종목만 보기", type="primary", use_container_width=True)
scan_status = st.empty()
if st.session_state.get("scanner_v5_scan_notice"):
    scan_status.success(st.session_state["scanner_v5_scan_notice"])

if scan_clicked or accumulation_scan_clicked:
    st.session_state["scanner_v5_hide_market"] = True
    for state_key in (
        "scanner_v5_selected", "scanner_v5_selected_mode", "scanner_v5_validation",
        "scanner_v5_direct_notice", "scanner_v5_all", "scanner_v5_result_mode",
    ):
        st.session_state.pop(state_key, None)
    if "view" in st.query_params:
        del st.query_params["view"]
    scan_name = "매집 흔적 종목" if accumulation_scan_clicked else "종가 매매 후보"
    st.session_state["scanner_v5_scan_notice"] = f"{scan_name}을(를) 분석 중입니다."
    scan_status.info(f"🔎 {scan_name}을(를) 찾고 있습니다. 완료 후 아래에서 결과를 확인하세요.")
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

    if rows and accumulation_scan_clicked:
        # 수급 조회 전 기술 신호가 40점 이상인 종목만 정밀 확인해 전체 검색 시간을 줄인다.
        preliminary = [
            row for row in rows
            if accumulation_snapshot(row, {})["점수"] >= 40
        ]
        preliminary = sorted(preliminary, key=lambda row: row.get("종목점수", 0), reverse=True)[:80]
        filtered, acc_progress = [], st.progress(0)
        acc_message = st.empty()
        for i, row in enumerate(preliminary, 1):
            summary, _, _ = flow_and_short(row["종목코드"])
            accumulation = accumulation_snapshot(row, summary)
            if accumulation["점수"] >= 50:
                row.update({
                    "매집 흔적 점수": accumulation["점수"],
                    "매집 단계": accumulation["단계"],
                    "매집 근거": accumulation["근거"],
                    "매집 위험": accumulation["위험"],
                })
                filtered.append(row)
            acc_progress.progress(i / max(len(preliminary), 1))
            acc_message.text(f"{i}/{len(preliminary)} 종목 매집 흔적 확인 중")
        acc_progress.empty(); acc_message.empty()
        rows = sorted(filtered, key=lambda row: (row["매집 흔적 점수"], row.get("종합점수", 0)), reverse=True)

    if rows:
        rows = [enrich_after_hours(row) for row in rows]
        sort_columns = ["매집 흔적 점수", "종합점수"] if accumulation_scan_clicked else ["최종순위점수", "종합점수"]
        R = pd.DataFrame(rows).sort_values(sort_columns, ascending=False)
        st.session_state["scanner_v5_all"] = R
        st.session_state["scanner_v5_result_mode"] = "accumulation" if accumulation_scan_clicked else "standard"
        st.session_state["scanner_v5_scan_notice"] = "✅ 검색이 완료되었습니다. 결과를 바로 아래에서 확인하세요."
        scan_status.success(st.session_state["scanner_v5_scan_notice"])
    elif accumulation_scan_clicked:
        st.session_state["scanner_v5_scan_notice"] = "조건에 맞는 매집 흔적 종목이 없습니다."
        scan_status.warning(st.session_state["scanner_v5_scan_notice"])
    else:
        st.session_state["scanner_v5_scan_notice"] = "분석 결과가 없습니다."
        scan_status.warning(st.session_state["scanner_v5_scan_notice"])

if not st.session_state.get("scanner_v5_hide_market", False):
    st.divider()
    with st.spinner("익일 코스피 예상 수치 계산 중..."):
        render_kospi_next_prediction(market_data, lookback, min_prediction_samples)
    st.divider()
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

if "scanner_v5_all" in st.session_state:
    R = st.session_state["scanner_v5_all"].copy()
    if st.session_state.get("scanner_v5_result_mode") == "accumulation":
        R["현재가 (등락률)"] = R.apply(current_price_label, axis=1)
        st.subheader("매집 흔적 검색 결과")
        st.caption("현재가는 조회 시점에 수집 가능한 최신 가격입니다. 매집 흔적 점수 50점 이상만 표시하며, 특정 세력이나 계좌의 실제 진입을 확인한 결과는 아닙니다.")
    R = R[pattern_filter_mask(R, pattern_filter, pattern_approach_pct)]
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
    pattern_columns = ["추정 반등가", "반등가 거리(%)", "현재 패턴 단계", "다음 목표가", "다음 목표까지(%)"]
    leading = [c for c in ["종목코드", "종목명", "종가", "판정", "매집 흔적 점수", "매집 단계", "매집 근거", "매집 위험"] if c in view.columns]
    display_columns = leading + [c for c in pattern_columns if c in view.columns]
    remaining = [c for c in view.columns if c not in display_columns]
    st.dataframe(scanner_table(view[display_columns + remaining]), use_container_width=True, hide_index=True)
    if len(view):
        labels = [f"{r['종목명']} ({r['종목코드']}) · {r['판정']}" for _, r in view.iterrows()]
        chosen = st.selectbox("상세 차트 종목 선택", labels)
        if st.button("선택 종목 상세보기"):
            st.session_state["scanner_v5_selected"] = view.iloc[labels.index(chosen)].to_dict()
            st.session_state["scanner_v5_selected_mode"] = "detail"
            st.query_params["view"] = "detail"
    st.download_button("v5 전체 결과 CSV 다운로드", R.to_csv(index=False).encode("utf-8-sig"), "krx_jongga_scanner_v5.csv", "text/csv")

if "scanner_v5_selected" in st.session_state:
    requested_view = st.query_params.get("view")
    if requested_view in ("simple", "detail"):
        st.session_state["scanner_v5_selected_mode"] = requested_view
    if st.session_state.get("scanner_v5_selected_mode") == "simple":
        show_simple_prediction(st.session_state["scanner_v5_selected"])
    else:
        show_detail(st.session_state["scanner_v5_selected"])

st.divider()
st.caption("연구·정보 제공용 도구입니다. CVD Proxy는 실제 체결 CVD가 아니며, 백테스트에는 거래비용·슬리피지·생존편향이 완전히 반영되지 않습니다. 투자 판단과 책임은 사용자에게 있습니다.")
st.markdown(
    """
    <div style="margin:.55rem 0 1rem; color:#7a8292; font-size:clamp(.68rem,2.2vw,.78rem);
                line-height:1.5; text-align:center;">
      © 2026 바빠맘. All Rights Reserved.<br>
      이 앱의 자체 제작 코드·화면 구성·콘텐츠에 대한 권리는 바빠맘에게 있습니다.
      허락 없는 복제·배포·변형은 금지되며, 권리 침해 시 관련 법령에 따른 책임이 발생할 수 있습니다.
      외부 데이터와 오픈소스 구성요소의 권리는 각 제공자에게 있습니다.
    </div>
    """,
    unsafe_allow_html=True,
)
