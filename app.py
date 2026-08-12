import streamlit as st
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

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

st.set_page_config(page_title="KRX 종가매매 스캐너 v4", layout="wide")
st.title("KRX 종가매매 스캐너 v4")
st.caption("종목점수 + 시장환경 + 업종환경 + 수급 + 백테스트 + 익절/손절선")

# -----------------------------
# Indicators
# -----------------------------
def calc_rsi(close, period=14):
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100/(1+rs)

def calc_obv(close, volume):
    return (np.sign(close.diff()).fillna(0) * volume).cumsum()

def calc_cvdp(df):
    spread = (df["High"] - df["Low"]).replace(0, np.nan)
    clv = (((df["Close"]-df["Low"]) - (df["High"]-df["Close"])) / spread).fillna(0).clip(-1, 1)
    return (clv * df["Volume"]).cumsum()

def prep(df):
    if df is None or len(df) < 80:
        return pd.DataFrame()
    need = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in need):
        return pd.DataFrame()
    df = df[need].dropna(subset=["Close"]).copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["VALUE20"] = (df["Close"] * df["Volume"]).rolling(20).mean()
    df["RSI"] = calc_rsi(df["Close"])
    df["OBV"] = calc_obv(df["Close"], df["Volume"])
    df["OBV10"] = df["OBV"].rolling(10).mean()
    df["CVDP"] = calc_cvdp(df)
    df["CVDP10"] = df["CVDP"].rolling(10).mean()
    df["HIGH20"] = df["High"].shift(1).rolling(20).max()
    df["LOW10"] = df["Low"].shift(1).rolling(10).min()
    df["RET1"] = df["Close"].pct_change() * 100
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    df["CLOSE_POS"] = ((df["Close"] - df["Low"]) / rng * 100).fillna(50).clip(0,100)
    df["UPPER_WICK"] = ((df["High"] - np.maximum(df["Open"], df["Close"])) / rng * 100).fillna(0).clip(0,100)
    return df

# -----------------------------
# Market / sector environment
# -----------------------------
def _last_metrics(series):
    series = series.dropna()
    if len(series) < 2:
        return {"last": np.nan, "d1": np.nan, "d5": np.nan}
    last = float(series.iloc[-1])
    d1 = (last / float(series.iloc[-2]) - 1) * 100
    base = float(series.iloc[-6]) if len(series) >= 6 else float(series.iloc[0])
    d5 = (last / base - 1) * 100
    return {"last": last, "d1": d1, "d5": d5}

@st.cache_data(ttl=900, show_spinner=False)
def yahoo_metric(ticker):
    if not YF_OK:
        return {"last": np.nan, "d1": np.nan, "d5": np.nan}
    try:
        x = yf.download(ticker, period="10d", interval="1d", progress=False, auto_adjust=False, threads=False)
        if x is None or len(x) == 0:
            return {"last": np.nan, "d1": np.nan, "d5": np.nan}
        close = x["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:,0]
        return _last_metrics(close)
    except Exception:
        return {"last": np.nan, "d1": np.nan, "d5": np.nan}

@st.cache_data(ttl=900, show_spinner=False)
def fdr_index_metric(code):
    try:
        x = fdr.DataReader(code, (date.today()-timedelta(days=40)).isoformat())
        m = _last_metrics(x["Close"])
        if len(x) >= 20:
            ma20 = float(x["Close"].rolling(20).mean().iloc[-1])
            m["above20"] = bool(float(x["Close"].iloc[-1]) >= ma20)
        else:
            m["above20"] = None
        return m
    except Exception:
        return {"last": np.nan, "d1": np.nan, "d5": np.nan, "above20": None}

def market_environment():
    data = {
        "VIX": yahoo_metric("^VIX"),
        "원/달러": yahoo_metric("KRW=X"),
        "나스닥100선물": yahoo_metric("NQ=F"),
        "미10년물": yahoo_metric("^TNX"),
        "WTI": yahoo_metric("CL=F"),
        "SOX": yahoo_metric("^SOX"),
        "KOSPI": fdr_index_metric("KS11"),
        "KOSDAQ": fdr_index_metric("KQ11"),
    }
    score = 50
    reasons = []

    v = data["VIX"]
    if pd.notna(v["last"]):
        if v["last"] < 18: score += 12; reasons.append("VIX 안정")
        elif v["last"] < 25: score += 5
        elif v["last"] < 30: score -= 8; reasons.append("VIX 경계")
        else: score -= 15; reasons.append("VIX 고위험")
        if pd.notna(v["d1"]) and v["d1"] >= 10:
            score -= 10; reasons.append("VIX 급등")

    fx = data["원/달러"]
    if pd.notna(fx["d1"]):
        if fx["d1"] <= -0.3: score += 6; reasons.append("원화 강세")
        elif fx["d1"] >= 1.0: score -= 10; reasons.append("원/달러 급등")
        elif fx["d1"] >= 0.5: score -= 5
        if pd.notna(fx["d5"]) and fx["d5"] >= 2:
            score -= 5

    nq = data["나스닥100선물"]
    if pd.notna(nq["d1"]):
        if nq["d1"] >= 1: score += 12; reasons.append("나스닥 선물 강세")
        elif nq["d1"] >= 0.3: score += 7
        elif nq["d1"] <= -1: score -= 15; reasons.append("나스닥 선물 약세")
        elif nq["d1"] <= -0.3: score -= 7

    tnx = data["미10년물"]
    if pd.notna(tnx["last"]) and pd.notna(tnx["d1"]):
        if tnx["d1"] >= 3: score -= 7; reasons.append("미10년물 급등")
        elif tnx["d1"] <= -3: score += 4

    for idx in ["KOSPI", "KOSDAQ"]:
        m = data[idx]
        if pd.notna(m["d1"]):
            if m["d1"] >= 1: score += 5
            elif m["d1"] <= -1: score -= 5
        if m.get("above20") is True: score += 3
        elif m.get("above20") is False: score -= 3

    oil = data["WTI"]
    if pd.notna(oil["d1"]) and abs(oil["d1"]) >= 5:
        score -= 3; reasons.append("유가 변동성 확대")

    score = int(max(0, min(100, score)))
    if score >= 75: label = "우호"
    elif score >= 55: label = "보통"
    elif score >= 40: label = "주의"
    else: label = "고위험"
    return score, label, data, reasons

def classify_sector(name, sector_text=""):
    text = f"{name} {sector_text}".lower()
    if any(k.lower() in text for k in ["반도체","semiconductor","하이닉스","한미반도체","hpsp","리노공업","isc"]):
        return "반도체"
    if any(k.lower() in text for k in ["소프트웨어","software","인터넷","게임","ai","로봇","플랫폼"]):
        return "성장/기술"
    if any(k.lower() in text for k in ["정유","에너지","oil","gas","석유","s-oil","sk이노베이션"]):
        return "에너지"
    if any(k.lower() in text for k in ["항공","airline","운송","여행"]):
        return "항공/운송"
    return "일반"

def signed_asset_score(d1, positive=True):
    if pd.isna(d1):
        return 50
    x = d1 if positive else -d1
    if x >= 2: return 90
    if x >= 1: return 80
    if x >= 0.3: return 70
    if x > -0.3: return 55
    if x > -1: return 45
    if x > -2: return 30
    return 15

def sector_environment(name, sector_text, market_score, market_data):
    cat = classify_sector(name, sector_text)
    nq = signed_asset_score(market_data["나스닥100선물"]["d1"], True)
    sox = signed_asset_score(market_data["SOX"]["d1"], True)
    oil_pos = signed_asset_score(market_data["WTI"]["d1"], True)
    oil_inv = signed_asset_score(market_data["WTI"]["d1"], False)

    if cat == "반도체":
        score = 0.65*sox + 0.20*nq + 0.15*market_score
    elif cat == "성장/기술":
        score = 0.75*nq + 0.10*sox + 0.15*market_score
    elif cat == "에너지":
        score = 0.60*oil_pos + 0.40*market_score
    elif cat == "항공/운송":
        score = 0.50*oil_inv + 0.50*market_score
    else:
        score = 0.75*market_score + 0.25*nq
    return cat, int(round(max(0,min(100,score))))

# -----------------------------
# Stock score / trade levels
# -----------------------------
def market_benchmark(market, start):
    code = "KS11" if str(market).upper().startswith("KOSPI") else "KQ11"
    try:
        x = fdr.DataReader(code, start)
        return x["Close"].pct_change()*100
    except Exception:
        return pd.Series(dtype=float)

def row_features(df, market_ret, i, p):
    r = df.iloc[i]
    prev = df.iloc[i-1]
    close = float(r["Close"])
    vr = float(r["Volume"]/r["VOL20"]) if r["VOL20"] else np.nan
    gap20 = (close/float(r["MA20"])-1)*100
    highgap = (close/float(r["HIGH20"])-1)*100 if pd.notna(r["HIGH20"]) else np.nan

    aligned = market_ret.reindex(df.index)
    mret = float(aligned.iloc[i]) if len(aligned) and pd.notna(aligned.iloc[i]) else 0.0
    rel = float(r["RET1"]) - mret

    trend = close > r["MA20"] and r["MA20"] >= r["MA60"]
    obv = r["OBV"] > r["OBV10"] and r["OBV"] > prev["OBV"]
    cvd = r["CVDP"] > r["CVDP10"] and r["CVDP"] > prev["CVDP"]
    breakout = close >= r["HIGH20"]*0.995 if pd.notna(r["HIGH20"]) else False
    pullback = bool(trend and close >= r["MA10"]*0.985 and close <= r["MA20"]*1.06 and r["CLOSE_POS"] >= 60 and float(r["RET1"]) > 0)
    stype = "돌파형" if breakout else ("눌림형" if pullback else "추세형")

    risk = 0
    if r["UPPER_WICK"] > 35: risk += 12
    if gap20 > p["max_gap"]: risk += 12
    if r["RSI"] > 75: risk += 10
    if float(r["RET1"]) > 12: risk += 10

    score = 0
    score += 10 if r["VALUE20"] >= p["min_value"] else 0
    score += 15 if trend else 0
    score += 10 if obv else 0
    score += 8 if cvd else 0
    score += 12 if vr >= p["min_vr"] else 0
    score += 10 if p["rsi_lo"] <= r["RSI"] <= p["rsi_hi"] else 0
    score += 12 if r["CLOSE_POS"] >= p["close_pos"] else (6 if r["CLOSE_POS"] >= 65 else 0)
    score += 8 if r["UPPER_WICK"] <= 25 else 0
    score += 8 if rel >= p["min_rel"] else 0
    score += 7 if breakout else (5 if pullback else 0)
    score = int(max(0,min(100,score-risk)))

    hard = bool(
        r["VALUE20"] >= p["min_value"]
        and close >= p["min_price"]
        and trend and obv
        and vr >= p["min_vr"]
        and p["rsi_lo"] <= r["RSI"] <= p["rsi_hi"]
        and gap20 <= p["max_gap"]
        and r["CLOSE_POS"] >= p["close_pos"]
        and r["UPPER_WICK"] <= p["max_wick"]
        and rel >= p["min_rel"]
    )
    return {
        "score":score, "hard":hard, "vr":vr, "gap20":gap20, "highgap":highgap,
        "rel":rel, "mret":mret, "obv":obv, "cvd":cvd, "stype":stype,
        "risk":risk, "closepos":float(r["CLOSE_POS"]), "wick":float(r["UPPER_WICK"])
    }

def trade_levels(df):
    r = df.iloc[-1]
    entry = float(r["Close"])
    supports = []
    for v in [r.get("MA20", np.nan), r.get("LOW10", np.nan)]:
        if pd.notna(v) and float(v) < entry:
            supports.append(float(v))
    raw = (max(supports) * 0.995) if supports else entry*0.97
    stop = min(entry*0.97, raw) if raw < entry else entry*0.97
    stop = max(entry*0.92, min(entry*0.97, stop))
    tp1 = entry*1.10
    tp2 = entry*1.20
    risk_pct = (entry-stop)/entry*100
    rr1 = (tp1-entry)/(entry-stop) if entry>stop else np.nan
    rr2 = (tp2-entry)/(entry-stop) if entry>stop else np.nan
    return {
        "진입가": int(round(entry)),
        "초기손절선": int(round(stop)),
        "손절폭%": round(risk_pct,2),
        "1차익절선(+10%)": int(round(tp1)),
        "2차익절선(+20%)": int(round(tp2)),
        "1차손익비R": round(rr1,2),
        "2차손익비R": round(rr2,2),
        "1차익절후손절": int(round(entry)),
    }

def backtest(df, market_ret, p):
    vals = []
    for i in range(65, len(df)-10):
        try:
            f = row_features(df, market_ret, i, p)
            if f["hard"] and f["score"] >= p["min_score"]:
                entry = float(df["Close"].iloc[i])
                future = df.iloc[i+1:i+11]
                vals.append({
                    "r1": (float(df["Close"].iloc[i+1])/entry-1)*100,
                    "r3": (float(df["Close"].iloc[i+3])/entry-1)*100,
                    "r5": (float(df["Close"].iloc[i+5])/entry-1)*100,
                    "mfe": (float(future["High"].max())/entry-1)*100,
                    "mae": (float(future["Low"].min())/entry-1)*100,
                })
        except Exception:
            pass
    if not vals:
        return {"n":0}
    x = pd.DataFrame(vals)
    return {
        "n":len(x),
        "win1":(x["r1"]>0).mean()*100,
        "avg1":x["r1"].mean(),
        "win3":(x["r3"]>0).mean()*100,
        "avg3":x["r3"].mean(),
        "win5":(x["r5"]>0).mean()*100,
        "avg5":x["r5"].mean(),
        "hit3":(x["mfe"]>=3).mean()*100,
        "mae":x["mae"].mean()
    }

def analyze(sym, name, mkt, sector_text, start, p, do_bt, market_score, market_data):
    try:
        df = prep(fdr.DataReader(sym, start))
        if len(df) < 80:
            return None
        mr = market_benchmark(mkt, start)
        f = row_features(df, mr, -1, p)
        r = df.iloc[-1]
        bt = backtest(df, mr, p) if do_bt and f["score"] >= p["min_score"]-10 else {"n":0}
        cat, sec_score = sector_environment(name, sector_text, market_score, market_data)
        combined = int(round(0.60*f["score"] + 0.25*market_score + 0.15*sec_score))
        levels = trade_levels(df)
        row = {
            "종목코드":sym, "종목명":name, "시장":mkt, "업종분류":cat,
            "날짜":df.index[-1].strftime("%Y-%m-%d"), "종가":int(round(float(r["Close"]))),
            "등락률%":round(float(r["RET1"]),2),
            "종목점수":f["score"], "시장환경":market_score, "업종환경":sec_score,
            "종합점수":combined,
            "신호":"매수후보" if f["hard"] and f["score"]>=p["min_score"] else "",
            "유형":f["stype"], "종가위치%":round(f["closepos"],1), "윗꼬리%":round(f["wick"],1),
            "거래량배수":round(f["vr"],2), "시장대비강도%":round(f["rel"],2),
            "RSI14":round(float(r["RSI"]),1), "MA20이격%":round(f["gap20"],2),
            "20일고점대비%":round(f["highgap"],2), "OBV":"O" if f["obv"] else "",
            "CVD Proxy":"O" if f["cvd"] else "", "위험감점":f["risk"],
            "과거동일신호":bt.get("n",0),
            "익일승률%":round(bt.get("win1",np.nan),1),
            "익일평균%":round(bt.get("avg1",np.nan),2),
            "3일승률%":round(bt.get("win3",np.nan),1),
            "3일평균%":round(bt.get("avg3",np.nan),2),
            "5일승률%":round(bt.get("win5",np.nan),1),
            "5일평균%":round(bt.get("avg5",np.nan),2),
            "10일내+3%도달%":round(bt.get("hit3",np.nan),1),
            "10일평균MAE%":round(bt.get("mae",np.nan),2),
        }
        row.update(levels)
        return row
    except Exception:
        return None

# -----------------------------
# Listings / flow
# -----------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def listing(market):
    return fdr.StockListing({"전체":"KRX", "코스피":"KOSPI", "코스닥":"KOSDAQ"}[market])

def pick_col(df, names):
    for n in names:
        if n in df.columns:
            return n
    return None

def investor_flow(symbol):
    if not PYKRX_OK:
        return {}
    try:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        end = now.strftime("%Y%m%d")
        start = (now-timedelta(days=14)).strftime("%Y%m%d")
        d = krx_stock.get_market_trading_value_by_date(start, end, symbol)
        if d is None or len(d) == 0:
            return {}
        last = d.tail(5)
        foreign = float(last["외국인"].sum()) if "외국인" in d.columns else np.nan
        inst_cols = [c for c in ["금융투자","보험","투신","사모","은행","기타금융","연기금"] if c in d.columns]
        inst = float(last[inst_cols].sum().sum()) if inst_cols else np.nan
        return {
            "외국인5일순매수(억)": round(foreign/1e8,1) if pd.notna(foreign) else np.nan,
            "기관5일순매수(억)": round(inst/1e8,1) if pd.notna(inst) else np.nan,
            "수급동반":"O" if pd.notna(foreign) and pd.notna(inst) and foreign>0 and inst>0 else ""
        }
    except Exception:
        return {}

# -----------------------------
# UI
# -----------------------------
with st.sidebar:
    st.header("v4 스캔 조건")
    market = st.selectbox("시장", ["전체","코스피","코스닥"])
    max_symbols = st.select_slider("스캔 종목 수", [100,200,300,500,800,1200], value=300)
    min_value = st.number_input("20일 평균 거래대금 최소(억원)", 1, value=30, step=10)
    min_price = st.number_input("최소 주가(원)", 100, value=2000, step=500)
    min_vr = st.slider("거래량 최소 배수", 0.5, 5.0, 1.5, 0.1)
    rlo, rhi = st.slider("RSI 범위", 0, 100, (55,70))
    close_pos = st.slider("종가 위치 최소(%)", 50, 100, 75)
    max_wick = st.slider("윗꼬리 최대(%)", 5, 60, 35)
    min_rel = st.slider("시장 대비 최소 강도(%p)", -3.0, 10.0, 0.5, 0.5)
    max_gap = st.slider("MA20 최대 이격률(%)", 1, 30, 12)
    min_score = st.slider("최소 종목점수", 50, 100, 75, 5)
    lookback = st.select_slider("데이터 조회 기간(백테스트 포함)", [180,250,365,540], value=365)
    do_bt = st.checkbox("후보 근처 종목 과거 신호 통계 계산", True)
    workers = st.slider("동시 조회 수", 2, 10, 5)

p = {
    "min_value":min_value*1e8, "min_price":min_price, "min_vr":min_vr,
    "rsi_lo":rlo, "rsi_hi":rhi, "close_pos":close_pos, "max_wick":max_wick,
    "min_rel":min_rel, "max_gap":max_gap, "min_score":min_score
}

with st.spinner("시장환경 확인 중..."):
    market_score, market_label, market_data, market_reasons = market_environment()

st.subheader("오늘 시장환경")
a,b,c,d = st.columns(4)
a.metric("시장환경 점수", f"{market_score}/100")
b.metric("판정", market_label)
vix = market_data["VIX"]
nq = market_data["나스닥100선물"]
c.metric("VIX", "-" if pd.isna(vix["last"]) else f"{vix['last']:.1f}",
         None if pd.isna(vix["d1"]) else f"{vix['d1']:+.1f}%")
d.metric("나스닥100 선물", "-" if pd.isna(nq["last"]) else f"{nq['last']:,.0f}",
         None if pd.isna(nq["d1"]) else f"{nq['d1']:+.2f}%")

env_rows = []
for key in ["원/달러","미10년물","WTI","SOX","KOSPI","KOSDAQ"]:
    m = market_data[key]
    env_rows.append({
        "지표":key,
        "현재":None if pd.isna(m["last"]) else round(m["last"],2),
        "1일변화%":None if pd.isna(m["d1"]) else round(m["d1"],2),
        "5일변화%":None if pd.isna(m["d5"]) else round(m["d5"],2)
    })
st.dataframe(pd.DataFrame(env_rows), use_container_width=True, hide_index=True)
if market_reasons:
    st.caption("시장환경 신호: " + " · ".join(market_reasons))
st.caption("해외지표는 데이터 제공처 시세 지연/휴장 때문에 실제 체결 시점과 차이가 날 수 있습니다.")

st.markdown("""
### v4 판정 구조
**종목 자체 60% + 시장환경 25% + 업종환경 15%**로 종합점수를 별도로 표시합니다.

- 반도체: **SOX + 나스닥100 선물** 비중 확대
- 성장/기술: **나스닥100 선물** 비중 확대
- 에너지: **WTI** 비중 확대
- 항공/운송: **유가 상승을 역방향 위험요인**으로 반영
- 손절선은 20일선/최근 10일 저점을 참고하되 진입가 대비 **-3%~-8% 범위**로 제한
- 1차 익절 **+10%**, 2차 익절 **+20%**, 1차 익절 후 남은 물량은 진입가를 방어선으로 표시
""")

if st.button("오늘 종가매매 후보 스캔 v4", type="primary", use_container_width=True):
    L = listing(market).copy()
    sc = pick_col(L, ["Code","Symbol"])
    nc = pick_col(L, ["Name"])
    mc = pick_col(L, ["Market"])
    cap = pick_col(L, ["Marcap","MarketCap"])
    sec = pick_col(L, ["Sector","Industry","Dept"])

    if not sc or not nc:
        st.error("종목 목록 형식이 예상과 다릅니다.")
        st.stop()

    L[sc] = L[sc].astype(str).str.zfill(6)
    if cap:
        L = L.sort_values(cap, ascending=False)
    L = L.head(max_symbols)
    start = (date.today()-timedelta(days=lookback)).isoformat()

    syms = []
    for _, x in L.iterrows():
        syms.append((
            x[sc], x[nc], str(x[mc]) if mc else market,
            str(x[sec]) if sec and pd.notna(x[sec]) else ""
        ))

    rows = []
    bar = st.progress(0)
    msg = st.empty()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fs = [ex.submit(analyze, sym, name, mkt, sector_text, start, p, do_bt, market_score, market_data)
              for sym,name,mkt,sector_text in syms]
        for i, fut in enumerate(as_completed(fs),1):
            z = fut.result()
            if z:
                rows.append(z)
            bar.progress(i/len(fs))
            msg.text(f"{i}/{len(fs)} 종목 분석 중")
    bar.empty(); msg.empty()

    if not rows:
        st.error("분석 결과가 없습니다.")
        st.stop()

    R = pd.DataFrame(rows)
    C = R[R["신호"]=="매수후보"].sort_values(["종합점수","종목점수"], ascending=False).copy()

    if len(C):
        enriched = []
        for _, row in C.head(20).iterrows():
            extra = investor_flow(row["종목코드"])
            dd = row.to_dict()
            dd.update(extra)
            enriched.append(dd)
        C = pd.DataFrame(enriched)

    st.session_state["scanner_v4_candidates"] = C
    st.session_state["scanner_v4_all"] = R
    st.session_state["scanner_v4_market"] = {
        "score":market_score, "label":market_label, "data":market_data
    }

if "scanner_v4_candidates" in st.session_state:
    C = st.session_state["scanner_v4_candidates"]
    R = st.session_state.get("scanner_v4_all", pd.DataFrame())

    x1,x2,x3,x4 = st.columns(4)
    x1.metric("분석 완료", f"{len(R)}종목")
    x2.metric("매수 후보", f"{len(C)}종목")
    x3.metric("최고 종합점수", "-" if len(C)==0 else int(C["종합점수"].max()))
    x4.metric("시장환경", f"{market_score}/100 {market_label}")

    st.subheader("오늘 종가매매 후보")
    if len(C)==0:
        st.info("현재 설정에서 매수 후보가 없습니다. 조건을 완화하기보다 시장환경과 종목 상태를 함께 확인하세요.")
    else:
        show_cols = [c for c in [
            "종목명","시장","유형","종목점수","시장환경","업종환경","종합점수",
            "진입가","초기손절선","손절폭%","1차익절선(+10%)","2차익절선(+20%)",
            "1차손익비R","2차손익비R","RSI14","종가위치%","거래량배수",
            "시장대비강도%","외국인5일순매수(억)","기관5일순매수(억)","수급동반",
            "과거동일신호","익일승률%","3일승률%","5일승률%","10일내+3%도달%"
        ] if c in C.columns]
        st.dataframe(C[show_cols], use_container_width=True, hide_index=True)

        labels = [f"{r['종목명']} ({r['종목코드']})" for _,r in C.iterrows()]
        choice = st.selectbox("상세 확인할 종목", labels)
        idx = labels.index(choice)
        sel = C.iloc[idx]

        st.subheader(f"{sel['종목명']} 매매 기준선")
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric("진입가", f"{int(sel['진입가']):,}원")
        c2.metric("초기 손절", f"{int(sel['초기손절선']):,}원", f"-{sel['손절폭%']:.2f}%")
        c3.metric("1차 익절", f"{int(sel['1차익절선(+10%)']):,}원", "+10%")
        c4.metric("2차 익절", f"{int(sel['2차익절선(+20%)']):,}원", "+20%")
        c5.metric("종합점수", f"{int(sel['종합점수'])}/100")

        st.write(
            f"**종목점수 {int(sel['종목점수'])} / 시장환경 {int(sel['시장환경'])} / "
            f"업종환경 {int(sel['업종환경'])} / 유형 {sel['유형']} / 업종 {sel['업종분류']}**"
        )
        st.caption(
            f"1차 손익비 {sel['1차손익비R']:.2f}R · 2차 손익비 {sel['2차손익비R']:.2f}R · "
            f"1차 익절 도달 후 남은 물량 방어선: {int(sel['1차익절후손절']):,}원(진입가)"
        )

        try:
            ch = fdr.DataReader(sel["종목코드"], (date.today()-timedelta(days=100)).isoformat())
            ch = ch.tail(60).copy()
            plot = pd.DataFrame(index=ch.index)
            plot["종가"] = ch["Close"]
            plot["진입가"] = float(sel["진입가"])
            plot["초기손절"] = float(sel["초기손절선"])
            plot["1차익절"] = float(sel["1차익절선(+10%)"])
            plot["2차익절"] = float(sel["2차익절선(+20%)"])
            st.line_chart(plot, use_container_width=True)
        except Exception:
            st.info("차트를 불러오지 못했습니다.")

        csv = C.to_csv(index=False).encode("utf-8-sig")
        st.download_button("후보 CSV 다운로드", csv, "krx_close_candidates_v4.csv", "text/csv")

    with st.expander("전체 분석 결과"):
        st.dataframe(R.sort_values(["종합점수","종목점수"], ascending=False), use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "연구/후보 압축용 도구입니다. 종합점수와 시장·업종 가중치는 아직 백테스트로 최적화된 확률값이 아닙니다. "
    "실제 CVD가 아니라 CVD Proxy를 사용합니다. 해외지표/수급/가격 데이터는 기준 시점이 다를 수 있습니다. "
    "백테스트에는 거래비용, 슬리피지, 상장폐지 및 구성종목 생존편향이 완전히 반영되지 않았습니다."
)
