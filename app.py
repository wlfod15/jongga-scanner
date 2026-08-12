from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

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
    return x


def _last_metrics(series):
    s = series.dropna()
    if len(s) < 2:
        return {"현재": np.nan, "1일": np.nan, "5일": np.nan}
    base = float(s.iloc[-6]) if len(s) >= 6 else float(s.iloc[0])
    return {"현재": float(s.iloc[-1]), "1일": (s.iloc[-1] / s.iloc[-2] - 1) * 100,
            "5일": (s.iloc[-1] / base - 1) * 100}


@st.cache_data(ttl=900, show_spinner=False)
def yahoo_metric(ticker):
    if not YF_OK:
        return {"현재": np.nan, "1일": np.nan, "5일": np.nan}
    try:
        x = yf.download(ticker, period="12d", interval="1d", progress=False,
                        auto_adjust=False, threads=False)
        close = x["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return _last_metrics(close)
    except Exception:
        return {"현재": np.nan, "1일": np.nan, "5일": np.nan}


@st.cache_data(ttl=900, show_spinner=False)
def index_metric(code):
    try:
        x = fdr.DataReader(code, (date.today() - timedelta(days=45)).isoformat())
        out = _last_metrics(x["Close"])
        out["20일선상"] = bool(x["Close"].iloc[-1] >= x["Close"].rolling(20).mean().iloc[-1]) if len(x) >= 20 else None
        return out
    except Exception:
        return {"현재": np.nan, "1일": np.nan, "5일": np.nan, "20일선상": None}


def market_environment():
    data = {
        "VIX": yahoo_metric("^VIX"), "원/달러": yahoo_metric("KRW=X"),
        "나스닥100 선물": yahoo_metric("NQ=F"), "미국10년물": yahoo_metric("^TNX"),
        "WTI": yahoo_metric("CL=F"), "SOX": yahoo_metric("^SOX"),
        "KOSPI": index_metric("KS11"), "KOSDAQ": index_metric("KQ11"),
    }
    score, reasons = 50, []
    vix = data["VIX"]
    if pd.notna(vix["현재"]):
        if vix["현재"] < 18: score += 12; reasons.append("VIX 안정")
        elif vix["현재"] < 25: score += 5
        elif vix["현재"] < 30: score -= 8; reasons.append("VIX 경계")
        else: score -= 15; reasons.append("VIX 고위험")
        if pd.notna(vix["1일"]) and vix["1일"] >= 10: score -= 10; reasons.append("VIX 급등")
    fx = data["원/달러"]
    if pd.notna(fx["1일"]):
        if fx["1일"] <= -0.3: score += 6; reasons.append("원화 강세")
        elif fx["1일"] >= 1: score -= 10; reasons.append("원/달러 급등")
        elif fx["1일"] >= .5: score -= 5
    nq = data["나스닥100 선물"]
    if pd.notna(nq["1일"]):
        if nq["1일"] >= 1: score += 12; reasons.append("나스닥 선물 강세")
        elif nq["1일"] >= .3: score += 7
        elif nq["1일"] <= -1: score -= 15; reasons.append("나스닥 선물 약세")
        elif nq["1일"] <= -.3: score -= 7
    tnx = data["미국10년물"]
    if pd.notna(tnx["1일"]):
        if tnx["1일"] >= 3: score -= 7; reasons.append("미국 10년물 급등")
        elif tnx["1일"] <= -3: score += 4
    for key in ("KOSPI", "KOSDAQ"):
        m = data[key]
        if pd.notna(m["1일"]): score += 5 if m["1일"] >= 1 else (-5 if m["1일"] <= -1 else 0)
        score += 3 if m.get("20일선상") is True else (-3 if m.get("20일선상") is False else 0)
    if pd.notna(data["WTI"]["1일"]) and abs(data["WTI"]["1일"]) >= 5:
        score -= 3; reasons.append("유가 변동성 확대")
    score = int(np.clip(score, 0, 100))
    label = "우호" if score >= 75 else "보통" if score >= 55 else "주의" if score >= 40 else "고위험"
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
    nq, sox = asset_score(md["나스닥100 선물"]["1일"]), asset_score(md["SOX"]["1일"])
    oil, oil_inv = asset_score(md["WTI"]["1일"]), asset_score(md["WTI"]["1일"], False)
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


def backtest(df, market_ret, p):
    samples = []
    for i in range(65, len(df) - 10):
        try:
            f = row_features(df, market_ret, i, p)
            if f["hard"] and f["score"] >= p["min_score"]:
                entry, future = float(df["Close"].iloc[i]), df.iloc[i + 1:i + 11]
                samples.append(((df["Close"].iloc[i + 5] / entry - 1) * 100,
                                (future["High"].max() / entry - 1) * 100,
                                (future["Low"].min() / entry - 1) * 100))
        except Exception:
            continue
    if not samples: return {"표본수": 0, "5일승률%": np.nan, "5일평균%": np.nan, "10일내+3%도달%": np.nan, "평균MAE%": np.nan}
    x = np.asarray(samples)
    return {"표본수": len(x), "5일승률%": round((x[:, 0] > 0).mean() * 100, 1), "5일평균%": round(x[:, 0].mean(), 2),
            "10일내+3%도달%": round((x[:, 1] >= 3).mean() * 100, 1), "평균MAE%": round(x[:, 2].mean(), 2)}


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
        bt = backtest(df, mr, p) if do_bt else {"표본수": 0}
        levels = trade_levels(df)
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


# ── 종목 목록, 수급, 공매도 ─────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def listings():
    x = fdr.StockListing("KRX").copy()
    code_col = next((c for c in ("Code", "Symbol") if c in x.columns), None)
    if not code_col or "Name" not in x.columns: return pd.DataFrame()
    x[code_col] = x[code_col].astype(str).str.zfill(6)
    return x.rename(columns={code_col: "Code"})


def _date_range(days=25):
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return (now - timedelta(days=days)).strftime("%Y%m%d"), now.strftime("%Y%m%d")


@st.cache_data(ttl=1800, show_spinner=False)
def flow_and_short(symbol):
    summary, flow, short = {}, pd.DataFrame(), pd.DataFrame()
    if not PYKRX_OK: return summary, flow, short
    start, end = _date_range()
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
    st.subheader(f"{row['종목명']} ({row['종목코드']}) 상세")
    cols = st.columns(6)
    for c, label, value in zip(cols, ("판정", "종합점수", "진입가", "초기손절", "1차익절", "2차익절"),
                               (row["판정"], f"{row['종합점수']:.1f}", f"{row['진입가']:,.0f}원", f"{row['초기손절']:,.0f}원", f"{row['1차익절(+10%)']:,.0f}원", f"{row['2차익절(+20%)']:,.0f}원")):
        c.metric(label, value)
    st.write(f"**탈락사유:** {row['탈락사유']}")
    details = {k: row.get(k) for k in ["종목점수", "시장환경", "업종환경", "최종순위점수", "업종분류", "유형", "RSI14", "거래량배수", "종가위치%", "윗꼬리%", "시장대비강도%p", "OBV", "CVD Proxy", "표본수", "5일승률%", "5일평균%", "10일내+3%도달%", "평균MAE%", "손절률%", "1차손익비R", "2차손익비R"]}
    st.dataframe(pd.DataFrame([details]), use_container_width=True, hide_index=True)
    with st.spinner("수급·공매도 데이터 확인 중..."):
        summary, flow, short = flow_and_short(row["종목코드"])
    if summary:
        st.markdown("#### 외국인·기관 수급 및 공매도")
        st.dataframe(pd.DataFrame([summary]), use_container_width=True, hide_index=True)
    if len(flow):
        with st.expander("외국인·기관 일별 순매수/순매도 현황"):
            st.dataframe((flow / 1e8).round(2).rename_axis("날짜"), use_container_width=True)
            st.caption("단위: 억원. 양수는 순매수, 음수는 순매도입니다.")
    if len(short):
        with st.expander("일별 공매도 거래 현황"):
            st.dataframe(short.rename_axis("날짜"), use_container_width=True)
    st.info("공매도 거래·잔고는 KRX 공개 통계이며 특정 투자자의 개별 포지션을 뜻하지 않습니다. 잔고 데이터는 공표 시차가 있을 수 있습니다.")
    try: st.plotly_chart(chart(row["종목코드"], row), use_container_width=True)
    except Exception as exc: st.warning(f"차트를 불러오지 못했습니다: {exc}")


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
    workers = st.slider("동시 조회 수", 2, 10, 5)

p = {"min_value": min_value * 1e8, "min_price": min_price, "min_vr": min_vr, "rsi_lo": rlo, "rsi_hi": rhi,
     "close_pos": close_pos, "max_wick": max_wick, "min_rel": min_rel, "max_gap": max_gap, "min_score": min_score}

with st.spinner("시장환경 확인 중..."):
    market_score, market_label, market_data, market_reasons = market_environment()

st.subheader("오늘 시장환경")
a, b, c, d = st.columns(4)
a.metric("시장환경 점수", f"{market_score}/100"); b.metric("판정", market_label)
c.metric("VIX", "-" if pd.isna(market_data["VIX"]["현재"]) else f"{market_data['VIX']['현재']:.1f}")
d.metric("나스닥100 선물", "-" if pd.isna(market_data["나스닥100 선물"]["현재"]) else f"{market_data['나스닥100 선물']['현재']:,.0f}", None if pd.isna(market_data["나스닥100 선물"]["1일"]) else f"{market_data['나스닥100 선물']['1일']:+.2f}%")
st.dataframe(pd.DataFrame([{"지표": k, **{q: (None if pd.isna(v[q]) else round(v[q], 2)) for q in ("현재", "1일", "5일")}} for k, v in market_data.items()]), use_container_width=True, hide_index=True)
if market_reasons: st.caption("시장환경 신호: " + " · ".join(market_reasons))

st.caption("최종순위점수는 종목 45%·시장 15%·업종 10%·과거 5일 승률 15%·1차 손익비 10%·표본 신뢰도 5%의 검증 전 설계 가중치입니다. 실전 성과를 보장하지 않습니다.")

L = listings()
st.subheader("직접 종목검색")
query = st.text_input("종목명 또는 6자리 코드", placeholder="예: 삼성전자 또는 005930")
matches = pd.DataFrame()
if query and len(L):
    matches = L[L["Name"].astype(str).str.contains(query, case=False, na=False, regex=False) | L["Code"].str.contains(query, regex=False)].head(30)
if len(matches):
    labels = [f"{r.Name} ({r.Code})" for _, r in matches.iterrows()]
    selected_search = st.selectbox("검색 결과", labels)
    if st.button("이 종목 분석", type="primary"):
        r = matches.iloc[labels.index(selected_search)]
        mkt = str(r.get("Market", "KOSPI")); sec = str(r.get("Sector", r.get("Industry", "")))
        with st.spinner("종목 분석 중..."):
            result = analyze(r.Code, r.Name, mkt, sec, (date.today() - timedelta(days=lookback)).isoformat(), p, do_bt, market_score, market_data)
        if result: st.session_state["scanner_v5_selected"] = result
        else: st.error("분석에 필요한 가격 데이터가 부족합니다.")
elif query:
    st.warning("일치하는 KRX 종목이 없습니다.")

if st.button("오늘 종가매매 후보 스캔 v5", type="primary", use_container_width=True):
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
        R = pd.DataFrame(rows).sort_values(["최종순위점수", "종합점수"], ascending=False)
        st.session_state["scanner_v5_all"] = R
    else: st.error("분석 결과가 없습니다.")

if "scanner_v5_all" in st.session_state:
    R = st.session_state["scanner_v5_all"]
    buys = R[R["판정"] == "매수후보"].head(5)
    near = R[R["판정"] != "매수후보"].sort_values(["최종순위점수", "종합점수"], ascending=False).head(5)
    st.subheader("오늘의 최종 매수후보 TOP5")
    if len(buys): st.dataframe(buys, use_container_width=True, hide_index=True)
    else: st.info("오늘 하드필터 통과 종목은 0개입니다. 아래 조건근접 TOP5를 대신 확인하세요.")
    st.subheader("조건근접 TOP5")
    st.dataframe(near, use_container_width=True, hide_index=True)

    st.subheader("업종별 / 전체 후보표")
    sector_choice = st.selectbox("업종 보기", ["전체", "반도체", "성장·기술", "에너지", "항공·운송", "일반"])
    view = R if sector_choice == "전체" else R[R["업종분류"] == sector_choice]
    st.dataframe(view, use_container_width=True, hide_index=True)
    if len(view):
        labels = [f"{r['종목명']} ({r['종목코드']}) · {r['판정']}" for _, r in view.iterrows()]
        chosen = st.selectbox("상세 차트 종목 선택", labels)
        if st.button("선택 종목 상세보기"):
            st.session_state["scanner_v5_selected"] = view.iloc[labels.index(chosen)].to_dict()
    st.download_button("v5 전체 결과 CSV 다운로드", R.to_csv(index=False).encode("utf-8-sig"), "krx_jongga_scanner_v5.csv", "text/csv")

if "scanner_v5_selected" in st.session_state:
    show_detail(st.session_state["scanner_v5_selected"])

st.divider()
st.caption("연구·정보 제공용 도구입니다. CVD Proxy는 실제 체결 CVD가 아니며, 백테스트에는 거래비용·슬리피지·생존편향이 완전히 반영되지 않습니다. 투자 판단과 책임은 사용자에게 있습니다.")

