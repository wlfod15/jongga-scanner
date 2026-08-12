
import streamlit as st
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="KRX 종가매매 스캐너", layout="wide")

st.title("KRX 종가매매 스캐너")
st.caption("일봉 OHLCV 기반 후보 압축 도구 · OBV + 거래량 + 추세 + RSI + CVD Proxy")

# -----------------------------
# Indicator helpers
# -----------------------------
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()

def calc_cvd_proxy(df: pd.DataFrame) -> pd.Series:
    """
    진짜 CVD가 아님.
    일봉 OHLCV만으로 매수/매도 주도 체결량을 정확히 분리할 수 없으므로
    종가의 캔들 내 위치(Close Location Value)에 거래량을 곱한 누적값을 사용.
    """
    spread = (df["High"] - df["Low"]).replace(0, np.nan)
    clv = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / spread
    clv = clv.fillna(0).clip(-1, 1)
    return (clv * df["Volume"]).cumsum()

def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    for c in needed:
        if c not in df.columns:
            return pd.DataFrame()
    df = df[needed].dropna(subset=["Close"])
    return df

def analyze_symbol(symbol, name, market, start_date, params):
    try:
        df = fdr.DataReader(symbol, start_date)
        df = normalize_ohlcv(df)
        if len(df) < 80:
            return None

        df["MA5"] = df["Close"].rolling(5).mean()
        df["MA20"] = df["Close"].rolling(20).mean()
        df["MA60"] = df["Close"].rolling(60).mean()
        df["VOL_MA20"] = df["Volume"].rolling(20).mean()
        df["VALUE"] = df["Close"] * df["Volume"]
        df["VALUE_MA20"] = df["VALUE"].rolling(20).mean()
        df["RSI14"] = calc_rsi(df["Close"], 14)
        df["OBV"] = calc_obv(df["Close"], df["Volume"])
        df["OBV_MA10"] = df["OBV"].rolling(10).mean()
        df["CVDP"] = calc_cvd_proxy(df)
        df["CVDP_MA10"] = df["CVDP"].rolling(10).mean()
        df["HIGH20_PREV"] = df["High"].shift(1).rolling(20).max()
        df["RET1"] = df["Close"].pct_change() * 100

        r = df.iloc[-1]
        p = df.iloc[-2]

        if pd.isna(r["MA60"]) or pd.isna(r["RSI14"]) or pd.isna(r["VOL_MA20"]):
            return None

        close = float(r["Close"])
        vol_ratio = float(r["Volume"] / r["VOL_MA20"]) if r["VOL_MA20"] else np.nan
        value_ma20 = float(r["VALUE_MA20"])
        ma20_gap = (close / float(r["MA20"]) - 1) * 100 if r["MA20"] else np.nan
        high20_gap = (close / float(r["HIGH20_PREV"]) - 1) * 100 if r["HIGH20_PREV"] else np.nan

        # 핵심 조건
        c_liquid = value_ma20 >= params["min_value_20d"]
        c_price = close >= params["min_price"]
        c_trend = (close > r["MA20"]) and (r["MA20"] >= r["MA60"])
        c_obv = (r["OBV"] > r["OBV_MA10"]) and (r["OBV"] > p["OBV"])
        c_cvdp = (r["CVDP"] > r["CVDP_MA10"]) and (r["CVDP"] > p["CVDP"])
        c_volume = vol_ratio >= params["min_vol_ratio"]
        c_rsi = params["rsi_min"] <= r["RSI14"] <= params["rsi_max"]
        c_extension = ma20_gap <= params["max_ma20_gap"]
        c_ret = params["ret_min"] <= r["RET1"] <= params["ret_max"]

        # 전고점 접근/돌파 보너스
        near_breakout = high20_gap >= -params["breakout_distance"]
        breakout = high20_gap >= 0

        # 100점 점수
        score = 0
        score += 10 if c_liquid else 0
        score += 5 if c_price else 0
        score += 20 if c_trend else 0
        score += 15 if c_obv else 0
        score += 15 if c_cvdp else 0
        score += 15 if c_volume else 0
        score += 10 if c_rsi else 0
        score += 5 if c_extension else 0
        score += 5 if c_ret else 0
        if near_breakout:
            score += 5
        if breakout:
            score += 5
        score = min(score, 100)

        hard_pass = all([c_liquid, c_price, c_trend, c_obv, c_volume, c_rsi, c_extension, c_ret])

        return {
            "종목코드": symbol,
            "종목명": name,
            "시장": market,
            "날짜": df.index[-1].strftime("%Y-%m-%d"),
            "종가": int(round(close)),
            "등락률%": round(float(r["RET1"]), 2),
            "점수": int(score),
            "신호": "매수후보" if hard_pass and score >= params["min_score"] else "",
            "거래량배수": round(vol_ratio, 2),
            "20일평균거래대금(억)": round(value_ma20 / 1e8, 1),
            "RSI14": round(float(r["RSI14"]), 1),
            "MA20이격%": round(ma20_gap, 2),
            "20일고점대비%": round(high20_gap, 2),
            "OBV상승": "O" if c_obv else "",
            "CVDProxy상승": "O" if c_cvdp else "",
            "추세정배열": "O" if c_trend else "",
            "1차익절가(+10%)": int(round(close * 1.10)),
            "2차익절가(+20%)": int(round(close * 1.20)),
            "본전손절가": int(round(close)),
        }
    except Exception:
        return None

@st.cache_data(ttl=60*60, show_spinner=False)
def load_listing(market):
    market_code = {"전체": "KRX", "코스피": "KOSPI", "코스닥": "KOSDAQ"}[market]
    df = fdr.StockListing(market_code)
    return df

def pick_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.header("스캔 조건")
    market = st.selectbox("시장", ["전체", "코스피", "코스닥"], index=0)
    max_symbols = st.select_slider("스캔 종목 수", options=[100, 200, 300, 500, 800, 1200], value=300)
    min_value_20d_uk = st.number_input("20일 평균 거래대금 최소(억원)", min_value=1, value=30, step=10)
    min_price = st.number_input("최소 주가(원)", min_value=100, value=2000, step=500)
    min_vol_ratio = st.slider("거래량 최소 배수(20일 평균 대비)", 0.5, 5.0, 1.5, 0.1)
    rsi_min, rsi_max = st.slider("RSI 범위", 0, 100, (50, 72))
    max_ma20_gap = st.slider("MA20 최대 이격률(%)", 1, 30, 12)
    ret_min, ret_max = st.slider("당일 등락률 범위(%)", -10, 30, (-2, 10))
    breakout_distance = st.slider("20일 고점 접근 허용폭(%)", 0, 15, 5)
    min_score = st.slider("최소 점수", 50, 100, 75, 5)
    lookback_days = st.select_slider("데이터 조회 기간", options=[120, 180, 250, 365], value=180)
    workers = st.slider("동시 조회 수", 2, 12, 6)

params = {
    "min_value_20d": min_value_20d_uk * 1e8,
    "min_price": min_price,
    "min_vol_ratio": min_vol_ratio,
    "rsi_min": rsi_min,
    "rsi_max": rsi_max,
    "max_ma20_gap": max_ma20_gap,
    "ret_min": ret_min,
    "ret_max": ret_max,
    "breakout_distance": breakout_distance,
    "min_score": min_score,
}

st.markdown("""
### 기본 로직
**유동성 → 추세 → 거래량 → OBV → CVD Proxy → RSI → 과열 여부 → 전고점 접근** 순으로 압축합니다.

- **OBV**: 종가 상승/하락 방향에 거래량을 누적
- **CVD Proxy**: 일봉의 종가 위치와 거래량으로 만든 대체 지표
- **진짜 CVD가 아닙니다.** 실제 CVD는 매수/매도 주도 체결을 구분할 수 있는 체결 데이터가 필요합니다.
""")

if st.button("오늘 종가매매 후보 스캔", type="primary", use_container_width=True):
    try:
        listing = load_listing(market)
    except Exception as e:
        st.error(f"종목 목록을 불러오지 못했습니다: {e}")
        st.stop()

    symbol_col = pick_col(listing, ["Code", "Symbol"])
    name_col = pick_col(listing, ["Name"])
    market_col = pick_col(listing, ["Market"])
    marcap_col = pick_col(listing, ["Marcap", "MarketCap"])

    if not symbol_col or not name_col:
        st.error("FinanceDataReader 종목 목록 형식이 예상과 다릅니다.")
        st.write(listing.head())
        st.stop()

    listing = listing.copy()
    listing[symbol_col] = listing[symbol_col].astype(str).str.zfill(6)

    if marcap_col:
        listing = listing.sort_values(marcap_col, ascending=False)

    listing = listing.head(max_symbols)

    start_date = (date.today() - timedelta(days=lookback_days)).isoformat()
    rows = []
    progress = st.progress(0)
    status = st.empty()

    symbols = []
    for _, row in listing.iterrows():
        symbols.append((
            row[symbol_col],
            row[name_col],
            str(row[market_col]) if market_col else market
        ))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(analyze_symbol, sym, name, mkt, start_date, params): (sym, name)
            for sym, name, mkt in symbols
        }
        total = len(futures)
        done = 0
        for fut in as_completed(futures):
            done += 1
            res = fut.result()
            if res:
                rows.append(res)
            progress.progress(done / total)
            status.text(f"{done}/{total} 종목 분석 중")

    progress.empty()
    status.empty()

    if not rows:
        st.warning("분석 가능한 종목이 없습니다. 조건을 완화하거나 다시 시도하세요.")
        st.stop()

    result = pd.DataFrame(rows).sort_values(
        ["신호", "점수", "20일평균거래대금(억)"],
        ascending=[False, False, False]
    )

    candidates = result[(result["신호"] == "매수후보") & (result["점수"] >= min_score)].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("분석 완료", f"{len(result)}종목")
    c2.metric("매수 후보", f"{len(candidates)}종목")
    c3.metric("최고 점수", int(result["점수"].max()))

    st.subheader("매수 후보")
    if len(candidates):
        st.dataframe(candidates, use_container_width=True, hide_index=True)
        csv = candidates.to_csv(index=False).encode("utf-8-sig")
        st.download_button("후보 CSV 다운로드", csv, "krx_close_candidates.csv", "text/csv")
    else:
        st.info("현재 설정에서 매수 후보가 없습니다. 최소 점수/거래량 조건을 낮춰 보세요.")

    with st.expander("전체 분석 결과"):
        st.dataframe(result, use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "주의: 이 도구는 후보 압축용이며 자동매수 기능이 없습니다. "
    "일봉 데이터만으로는 실제 매수·매도 주도 체결량을 분리할 수 없어 CVD는 Proxy로 표시합니다. "
    "신호 성과는 반드시 별도 백테스트로 검증해야 합니다."
)
