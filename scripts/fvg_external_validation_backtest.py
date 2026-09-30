#!/usr/bin/env python3
"""External-source FVG + liquidity-context validation.

Downloads the fixed public GetData XAUUSD 5m sample through 2026-07-31,
aggregates closed 15m candles, and applies the same controlled FVG rules
without tuning. Research only; does not touch the main engine.
"""
from __future__ import annotations
import csv, io, json, urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/sample-2026-07-31/XAUUSD_5m.csv"
MIN_RR,MAX_RR=2.0,2.5
PAD=0.5
LOOKBACK=30
SWEEP_LOOKBACK=6

def pt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
def body(c): return abs(c["close"]-c["open"])
def rng(c): return c["high"]-c["low"]

def swings(bars,side,lb=3):
    out=[]
    for i in range(lb,len(bars)-lb):
        v=bars[i][side]
        vals=[x[side] for x in bars[i-lb:i+lb+1]]
        if (v>=max(vals) if side=="high" else v<=min(vals)): out.append(v)
    return out

def equals(bars,side,tol=1.5):
    vals=[b[side] for b in bars]; out=[]
    for i,v in enumerate(vals):
        near=[x for j,x in enumerate(vals) if j!=i and abs(x-v)<=tol]
        if near: out.append((v+sum(near))/(len(near)+1))
    return out

def uniq(levels,tol=1.5):
    out=[]
    for x in sorted(levels):
        if not out or abs(x-out[-1])>tol: out.append(x)
    return out

def sweep_quality(c,level,direction):
    r=rng(c)
    if r<=0: return False
    b=body(c)
    pen=(c["low"]-level) if direction=="BUY" else (c["high"]-level)
    if pen<=0 or pen<r*.10: return False
    if direction=="BUY":
        wick=min(c["open"],c["close"])-c["low"]
        pos=(c["close"]-c["low"])/r
        return wick>=max(b*.5,r*.15) and pos>=.40
    wick=c["high"]-max(c["open"],c["close"])
    pos=(c["close"]-c["low"])/r
    return wick>=max(b*.5,r*.15) and pos<=.60

def parse():
    raw=urllib.request.urlopen(URL,timeout=30).read()
    rows=[]
    for z in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        t=z["datetime"]
        if not t.endswith("+00:00"): t=t+"+00:00"
        rows.append({"openTime":t,"open":float(z["open"]),"high":float(z["high"]),"low":float(z["low"]),"close":float(z["close"])})
    return sorted(rows,key=lambda x:x["openTime"])

def aggregate15(m5):
    out=[]; cur=None
    for c in m5:
        t=pt(c["openTime"]).replace(minute=(pt(c["openTime"]).minute//15)*15,second=0,microsecond=0)
        k=t.isoformat().replace("+00:00","Z")
        if cur is None or cur["openTime"]!=k:
            if cur: out.append(cur)
            cur={"openTime":k,"open":c["open"],"high":c["high"],"low":c["low"],"close":c["close"]}
        else:
            cur["high"]=max(cur["high"],c["high"]); cur["low"]=min(cur["low"],c["low"]); cur["close"]=c["close"]
    if cur: out.append(cur)
    return out

def atr(m5,i,n=14):
    vals=[rng(x) for x in m5[max(0,i-n):i] if rng(x)>0]
    return sum(vals)/len(vals) if vals else None

def fvg(m5,i):
    if i<9: return None
    prev=[body(x) for x in m5[i-8:i-1] if rng(x)>0]
    if not prev or rng(m5[i-1])<=0: return None
    if body(m5[i-1])<1.25*sum(prev)/len(prev) or body(m5[i-1])/rng(m5[i-1])<.55: return None
    a,b,c=m5[i-2],m5[i-1],m5[i]
    if a["high"]<c["low"]: lo,hi,di=a["high"],c["low"],"BUY"
    elif a["low"]>c["high"]: lo,hi,di=c["high"],a["low"],"SELL"
    else: return None
    aatr=atr(m5,i)
    if not aatr or hi-lo<.10*aatr: return None
    return {"time":c["openTime"],"lo":lo,"hi":hi,"mid":(lo+hi)/2,"dir":di,"atr":aatr}

def recent_sweep(m5,i,di):
    for j in range(max(5,i-SWEEP_LOOKBACK),i):
        prior=m5[max(0,j-30):j]
        if len(prior)<10: continue
        highs=uniq(swings(prior,"high")+equals(prior,"high")); lows=uniq(swings(prior,"low")+equals(prior,"low"))
        c=m5[j]
        if di=="BUY" and any(c["low"]<x and c["close"]>x and sweep_quality(c,x,"BUY") for x in lows): return True
        if di=="SELL" and any(c["high"]>x and c["close"]<x and sweep_quality(c,x,"SELL") for x in highs): return True
    return False

def near_liq(f,m15,t):
    ctx=[x for x in m15 if pt(x["openTime"])<=t][-LOOKBACK:]
    if len(ctx)<10:return False
    levels=uniq(swings(ctx,"low")+equals(ctx,"low")) if f["dir"]=="BUY" else uniq(swings(ctx,"high")+equals(ctx,"high"))
    return any(abs(f["mid"]-x)<=f["atr"] for x in levels)

def target(f,entry,sl,ctx):
    levels=uniq(swings(ctx,"high")+equals(ctx,"high")) if f["dir"]=="BUY" else uniq(swings(ctx,"low")+equals(ctx,"low"))
    if f["dir"]=="BUY":
        cand=sorted(x for x in levels if x>entry)
        v=[x for x in cand if MIN_RR<=(x-entry)/(entry-sl)<=MAX_RR]
    else:
        cand=sorted((x for x in levels if x<entry),reverse=True)
        v=[x for x in cand if MIN_RR<=(entry-x)/(sl-entry)<=MAX_RR]
    return v[0] if v else None

def main():
    m5=parse(); m15=aggregate15(m5); signals=[]; reasons={}; active_until=None; loc_counts={}
    for i in range(9,len(m5)):
        if active_until and pt(m5[i]["openTime"])<=active_until: continue
        f=fvg(m5,i)
        if not f: continue
        t=pt(f["time"])
        near=near_liq(f,m15,t-timedelta(minutes=15))
        sweep=recent_sweep(m5,i,f["dir"])
        loc="BOTH" if near and sweep else "M15_LIQUIDITY" if near else "RECENT_SWEEP" if sweep else "NONE"
        loc_counts[loc]=loc_counts.get(loc,0)+1
        if loc=="NONE": continue
        for j in range(i+1,len(m5)):
            c=m5[j]
            if active_until and pt(c["openTime"])<=active_until: break
            if not (c["low"]<=f["mid"]<=c["high"]): 
                if (f["dir"]=="BUY" and c["low"]<=f["lo"]) or (f["dir"]=="SELL" and c["high"]>=f["hi"]): break
                continue
            ok=(f["dir"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (f["dir"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
            if not ok: continue
            ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
            entry=f["mid"]; sl=f["lo"]-PAD if f["dir"]=="BUY" else f["hi"]+PAD; tp=target(f,entry,sl,ctx)
            if tp is None: reasons["no_target_rr2_2.5"]=reasons.get("no_target_rr2_2.5",0)+1; break
            rr=(tp-entry)/(entry-sl) if f["dir"]=="BUY" else (entry-tp)/(sl-entry)
            out="OPEN_AT_DATA_END"; end=None
            for k in range(j+1,len(m5)):
                b=m5[k]; hs=b["high"]>=tp if f["dir"]=="BUY" else b["low"]<=tp; ss=b["low"]<=sl if f["dir"]=="BUY" else b["high"]>=sl
                if hs and ss: out="AMBIGUOUS"; end=k; break
                if hs: out="TP"; end=k; break
                if ss: out="SL"; end=k; break
            signals.append({"time":c["openTime"],"dir":f["dir"],"rr":round(rr,2),"location":loc,"outcome":out})
            if end is not None: active_until=pt(m5[end]["openTime"])
            break
    def summary(ss):
        w=sum(x["outcome"]=="TP" for x in ss); l=sum(x["outcome"]=="SL" for x in ss); a=sum(x["outcome"]=="AMBIGUOUS" for x in ss)
        return {"signals":len(ss),"tp":w,"sl":l,"ambiguous":a,"win_rate_determinate":round(100*w/(w+l),2) if w+l else None,"net_r":round(sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in ss),2),"avg_rr":round(sum(x["rr"] for x in ss)/len(ss),2) if ss else None}
    windows={}
    for name,a,b in [("Feb-Mar","2026-02-02T00:00:00Z","2026-03-25T23:59:59Z"),("Mar-May","2026-03-26T00:00:00Z","2026-05-31T23:59:59Z"),("Jun-Jul","2026-06-01T00:00:00Z","2026-07-31T23:59:59Z")]:
        aa=pt(a);bb=pt(b);windows[name]=summary([s for s in signals if aa<=pt(s["time"])<=bb])
    result={"status":"COMPLETED","source":URL,"m5":len(m5),"m15":len(m15),"first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"],"overall":summary(signals),"by_location":{loc:summary([s for s in signals if s["location"]==loc]) for loc in ("M15_LIQUIDITY","RECENT_SWEEP","BOTH")},"windows":windows,"location_counts":loc_counts,"no_trade_reasons":reasons,"signals":signals,"notes":["Fixed parameters; no tuning per window.","Independent external 5m source; M15 aggregated from the same 5m source.","Research validation only; not a profitability guarantee."]}
    out=Path("backtest/fvg_external_validation_results.json");out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(result,indent=2),encoding="utf-8");print(json.dumps(result,indent=2))
if __name__=="__main__": main()
