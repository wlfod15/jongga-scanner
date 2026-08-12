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

st.set_page_config(page_title="KRX 종가매매 스캐너 v2", layout="wide")
st.title("KRX 종가매매 스캐너 v2")
st.caption("종가강도 + 돌파/눌림 분리 + 시장 상대강도 + 거래량의 질 + 수급 보강 + 과거 신호 통계")

def calc_rsi(close, period=14):
    d=close.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/period,adjust=False,min_periods=period).mean(); al=l.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    return 100-(100/(1+ag/al.replace(0,np.nan)))

def calc_obv(close, volume): return (np.sign(close.diff()).fillna(0)*volume).cumsum()

def calc_cvdp(df):
    s=(df.High-df.Low).replace(0,np.nan)
    clv=(((df.Close-df.Low)-(df.High-df.Close))/s).fillna(0).clip(-1,1)
    return (clv*df.Volume).cumsum()

def prep(df):
    if df is None or len(df)<80: return pd.DataFrame()
    df=df[["Open","High","Low","Close","Volume"]].dropna().copy()
    df["MA5"]=df.Close.rolling(5).mean(); df["MA10"]=df.Close.rolling(10).mean(); df["MA20"]=df.Close.rolling(20).mean(); df["MA60"]=df.Close.rolling(60).mean()
    df["VOL20"]=df.Volume.rolling(20).mean(); df["VALUE20"]=(df.Close*df.Volume).rolling(20).mean()
    df["RSI"]=calc_rsi(df.Close); df["OBV"]=calc_obv(df.Close,df.Volume); df["OBV10"]=df.OBV.rolling(10).mean()
    df["CVDP"]=calc_cvdp(df); df["CVDP10"]=df.CVDP.rolling(10).mean(); df["HIGH20"]=df.High.shift(1).rolling(20).max(); df["HIGH60"]=df.High.shift(1).rolling(60).max()
    df["RET1"]=df.Close.pct_change()*100
    rng=(df.High-df.Low).replace(0,np.nan)
    df["CLOSE_POS"]=((df.Close-df.Low)/rng*100).fillna(50).clip(0,100)
    df["UPPER_WICK"]=((df.High-np.maximum(df.Open,df.Close))/rng*100).fillna(0).clip(0,100)
    return df

def market_benchmark(market,start):
    code='KS11' if str(market).upper() in ['KOSPI','KOSPI GLOBAL'] else 'KQ11'
    try:
        x=fdr.DataReader(code,start); return x.Close.pct_change()*100
    except: return pd.Series(dtype=float)

def row_features(df, market_ret, i, p):
    r=df.iloc[i]; prev=df.iloc[i-1]; close=float(r.Close)
    vr=float(r.Volume/r.VOL20) if r.VOL20 else np.nan
    gap20=(close/float(r.MA20)-1)*100; highgap=(close/float(r.HIGH20)-1)*100 if r.HIGH20 else np.nan
    mret=float(market_ret.reindex(df.index).iloc[i]) if len(market_ret) and pd.notna(market_ret.reindex(df.index).iloc[i]) else 0.0
    rel=float(r.RET1)-mret
    trend=close>r.MA20 and r.MA20>=r.MA60
    obv=r.OBV>r.OBV10 and r.OBV>prev.OBV; cvd=r.CVDP>r.CVDP10 and r.CVDP>prev.CVDP
    breakout=close>=r.HIGH20*0.995 if pd.notna(r.HIGH20) else False
    pullback=(trend and close>=r.MA10*0.985 and close<=r.MA20*1.06 and r.CLOSE_POS>=60 and float(r.RET1)>0)
    signal_type='돌파형' if breakout else ('눌림형' if pullback else '추세형')
    risk=0
    if r.UPPER_WICK>35: risk+=12
    if gap20>p['max_gap']: risk+=12
    if r.RSI>75: risk+=10
    if float(r.RET1)>12: risk+=10
    score=0
    score += 10 if r.VALUE20>=p['min_value'] else 0
    score += 15 if trend else 0
    score += 10 if obv else 0
    score += 8 if cvd else 0
    score += 12 if vr>=p['min_vr'] else 0
    score += 10 if p['rsi_lo']<=r.RSI<=p['rsi_hi'] else 0
    score += 12 if r.CLOSE_POS>=p['close_pos'] else (6 if r.CLOSE_POS>=65 else 0)
    score += 8 if r.UPPER_WICK<=25 else 0
    score += 8 if rel>=p['min_rel'] else 0
    score += 7 if breakout else (5 if pullback else 0)
    score=max(0,min(100,score-risk))
    hard=(r.VALUE20>=p['min_value'] and close>=p['min_price'] and trend and obv and vr>=p['min_vr'] and p['rsi_lo']<=r.RSI<=p['rsi_hi'] and gap20<=p['max_gap'] and r.CLOSE_POS>=p['close_pos'] and r.UPPER_WICK<=p['max_wick'] and rel>=p['min_rel'])
    return dict(score=int(score),hard=hard,vr=vr,gap20=gap20,highgap=highgap,rel=rel,mret=mret,obv=obv,cvd=cvd,stype=signal_type,risk=risk,closepos=float(r.CLOSE_POS),wick=float(r.UPPER_WICK))

def backtest(df, market_ret, p):
    vals=[]
    for i in range(65,len(df)-10):
        try:
            f=row_features(df,market_ret,i,p)
            if f['hard'] and f['score']>=p['min_score']:
                entry=float(df.Close.iloc[i]); future=df.iloc[i+1:i+11]
                vals.append({'r1':(float(df.Close.iloc[i+1])/entry-1)*100,'r3':(float(df.Close.iloc[i+3])/entry-1)*100,'r5':(float(df.Close.iloc[i+5])/entry-1)*100,'r10':(float(df.Close.iloc[i+10])/entry-1)*100,'mfe':(float(future.High.max())/entry-1)*100,'mae':(float(future.Low.min())/entry-1)*100})
        except: pass
    if not vals: return {'n':0}
    x=pd.DataFrame(vals)
    return {'n':len(x),'win1':(x.r1>0).mean()*100,'avg1':x.r1.mean(),'win3':(x.r3>0).mean()*100,'avg3':x.r3.mean(),'win5':(x.r5>0).mean()*100,'avg5':x.r5.mean(),'hit3':(x.mfe>=3).mean()*100,'mae':x.mae.mean()}

def analyze(sym,name,mkt,start,p,do_bt):
    try:
        df=prep(fdr.DataReader(sym,start));
        if len(df)<80:return None
        mr=market_benchmark(mkt,start); f=row_features(df,mr,-1,p); r=df.iloc[-1]
        bt=backtest(df,mr,p) if do_bt and f['score']>=p['min_score']-10 else {'n':0}
        return {'종목코드':sym,'종목명':name,'시장':mkt,'날짜':df.index[-1].strftime('%Y-%m-%d'),'종가':int(r.Close),'등락률%':round(float(r.RET1),2),'종가베팅점수':f['score'],'신호':'매수후보' if f['hard'] and f['score']>=p['min_score'] else '','유형':f['stype'],'종가위치%':round(f['closepos'],1),'윗꼬리%':round(f['wick'],1),'거래량배수':round(f['vr'],2),'시장대비강도%':round(f['rel'],2),'RSI14':round(float(r.RSI),1),'MA20이격%':round(f['gap20'],2),'20일고점대비%':round(f['highgap'],2),'OBV':'O' if f['obv'] else '','CVD Proxy':'O' if f['cvd'] else '','위험감점':f['risk'],'과거동일신호':bt.get('n',0),'익일승률%':round(bt.get('win1',np.nan),1),'익일평균%':round(bt.get('avg1',np.nan),2),'3일승률%':round(bt.get('win3',np.nan),1),'3일평균%':round(bt.get('avg3',np.nan),2),'5일승률%':round(bt.get('win5',np.nan),1),'5일평균%':round(bt.get('avg5',np.nan),2),'10일내+3%도달%':round(bt.get('hit3',np.nan),1),'10일평균MAE%':round(bt.get('mae',np.nan),2)}
    except:return None

@st.cache_data(ttl=3600,show_spinner=False)
def listing(market): return fdr.StockListing({'전체':'KRX','코스피':'KOSPI','코스닥':'KOSDAQ'}[market])

def col(df,names):
    for n in names:
        if n in df.columns:return n

def investor_flow(symbol):
    if not PYKRX_OK:return None
    try:
        end=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'); start=(datetime.now(ZoneInfo('Asia/Seoul'))-timedelta(days=12)).strftime('%Y%m%d')
        d=krx_stock.get_market_trading_value_by_date(start,end,symbol)
        if d is None or len(d)==0:return None
        last=d.tail(5); inst_cols=[c for c in ['금융투자','보험','투신','사모','은행','기타금융','연기금'] if c in d.columns]
        foreign=float(last['외국인'].sum()) if '외국인' in d.columns else np.nan
        inst=float(last[inst_cols].sum().sum()) if inst_cols else np.nan
        return {'외국인5일순매수(억)':round(foreign/1e8,1),'기관5일순매수(억)':round(inst/1e8,1),'수급동반':'O' if foreign>0 and inst>0 else ''}
    except:return None

with st.sidebar:
    st.header('v2 스캔 조건')
    market=st.selectbox('시장',['전체','코스피','코스닥'])
    max_symbols=st.select_slider('스캔 종목 수',[100,200,300,500,800,1200],value=300)
    min_value=st.number_input('20일 평균 거래대금 최소(억원)',1,value=30,step=10)
    min_price=st.number_input('최소 주가(원)',100,value=2000,step=500)
    min_vr=st.slider('거래량 최소 배수',0.5,5.0,1.5,0.1)
    rlo,rhi=st.slider('RSI 범위',0,100,(50,72))
    close_pos=st.slider('종가 위치 최소(%)',50,100,75)
    max_wick=st.slider('윗꼬리 최대(%)',5,60,35)
    min_rel=st.slider('시장 대비 최소 강도(%p)',-3.0,10.0,0.5,0.5)
    max_gap=st.slider('MA20 최대 이격률(%)',1,30,12)
    min_score=st.slider('최소 점수',50,100,75,5)
    lookback=st.select_slider('데이터 조회 기간(백테스트 포함)',[180,250,365,540],value=365)
    do_bt=st.checkbox('후보 근처 종목 과거 신호 통계 계산',True)
    workers=st.slider('동시 조회 수',2,10,5)

p={'min_value':min_value*1e8,'min_price':min_price,'min_vr':min_vr,'rsi_lo':rlo,'rsi_hi':rhi,'close_pos':close_pos,'max_wick':max_wick,'min_rel':min_rel,'max_gap':max_gap,'min_score':min_score}
st.markdown('''### v2 핵심 로직
**시장보다 강한 종목 → 종가가 고가권에서 끝나는 종목 → 거래량/OBV 확인 → 돌파형·눌림형 분리 → 과열/윗꼬리 감점 → 과거 동일 신호 통계** 순으로 봅니다.

- **종가 위치**: 당일 저가~고가 중 종가가 어디에서 끝났는지 측정
- **시장 대비 강도**: 종목 당일 수익률에서 KOSPI/KOSDAQ 수익률을 차감
- **위험 감점**: 긴 윗꼬리, 과도한 MA20 이격, RSI 과열, 당일 급등
- **수급**: 최종 후보에 한해 최근 5거래일 외국인·기관 순매수를 보강합니다. 데이터 조회 실패 시 비워둡니다.
- **과거 통계는 해당 종목 자체의 과거 동일 조건 표본**이며 미래 수익을 보장하지 않습니다.
''')

if st.button('오늘 종가매매 후보 스캔 v2',type='primary',use_container_width=True):
    L=listing(market).copy(); sc=col(L,['Code','Symbol']); nc=col(L,['Name']); mc=col(L,['Market']); cap=col(L,['Marcap','MarketCap'])
    L[sc]=L[sc].astype(str).str.zfill(6)
    if cap:L=L.sort_values(cap,ascending=False)
    L=L.head(max_symbols); start=(date.today()-timedelta(days=lookback)).isoformat(); rows=[]; bar=st.progress(0); msg=st.empty()
    syms=[(x[sc],x[nc],str(x[mc]) if mc else market) for _,x in L.iterrows()]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fs=[ex.submit(analyze,*s,start,p,do_bt) for s in syms]
        for i,fut in enumerate(as_completed(fs),1):
            z=fut.result(); rows.append(z) if z else None; bar.progress(i/len(fs)); msg.text(f'{i}/{len(fs)} 종목 분석 중')
    bar.empty(); msg.empty()
    if not rows: st.error('분석 결과가 없습니다.'); st.stop()
    R=pd.DataFrame(rows); C=R[(R.신호=='매수후보')].sort_values(['종가베팅점수','시장대비강도%'],ascending=False).copy()
    if len(C):
        flows=[]
        for sym in C.종목코드.head(15): flows.append(investor_flow(sym) or {})
        for key in ['외국인5일순매수(억)','기관5일순매수(억)','수급동반']:
            C.loc[C.index[:len(flows)],key]=[x.get(key,np.nan if key!='수급동반' else '') for x in flows]
    a,b,c=st.columns(3); a.metric('분석 완료',f'{len(R)}종목'); b.metric('매수 후보',f'{len(C)}종목'); c.metric('후보 최고 점수',int(C.종가베팅점수.max()) if len(C) else '-')
    st.subheader('오늘 매수 후보')
    if len(C):
        show=['종목코드','종목명','유형','종가','등락률%','종가베팅점수','종가위치%','윗꼬리%','거래량배수','시장대비강도%','외국인5일순매수(억)','기관5일순매수(억)','수급동반','RSI14','과거동일신호','익일승률%','익일평균%','3일승률%','3일평균%','5일승률%','5일평균%','10일내+3%도달%','10일평균MAE%']
        st.dataframe(C[[x for x in show if x in C.columns]],use_container_width=True,hide_index=True)
        st.download_button('후보 CSV 다운로드',C.to_csv(index=False).encode('utf-8-sig'),'krx_close_candidates_v2.csv','text/csv')
    else: st.info('현재 조건을 모두 통과한 후보가 없습니다.')
    with st.expander('전체 분석 결과'): st.dataframe(R.sort_values('종가베팅점수',ascending=False),use_container_width=True,hide_index=True)

st.divider()
st.caption('연구/후보 압축용 도구입니다. 실제 CVD가 아니라 CVD Proxy를 사용하며, 수급 데이터와 가격 데이터의 기준 시점 차이가 있을 수 있습니다. 백테스트에는 거래비용·슬리피지·상장폐지/구성종목 생존편향 등이 완전히 반영되지 않았습니다.')
