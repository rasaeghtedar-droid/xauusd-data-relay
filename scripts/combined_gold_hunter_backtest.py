#!/usr/bin/env python3
"""Combined Gold Hunter validation: Liquidity + FVG, fixed rules, independent data."""
from __future__ import annotations
import csv,io,json,urllib.request,os
from datetime import datetime,timedelta
from pathlib import Path

URL=os.getenv("COMBINED_SOURCE_URL","https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/sample-2026-07-31/XAUUSD_5m.csv")
MIN_RR,MAX_RR=2.0,2.5
PAD=0.5
LOOKBACK=30
SWING=3
TOL=1.5
FVG_SWEEP_LOOKBACK=6
FVG_ATR_LOOKBACK=14

def pt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
def body(c): return abs(c["close"]-c["open"])
def rng(c): return c["high"]-c["low"]
def swings(bs,side,lb=SWING):
    out=[]
    for i in range(lb,len(bs)-lb):
        v=bs[i][side]; vals=[x[side] for x in bs[i-lb:i+lb+1]]
        if (v>=max(vals) if side=="high" else v<=min(vals)): out.append(v)
    return out
def equals(bs,side):
    vals=[b[side] for b in bs]; out=[]
    for i,v in enumerate(vals):
        near=[x for j,x in enumerate(vals) if j!=i and abs(x-v)<=TOL]
        if near: out.append((v+sum(near))/(len(near)+1))
    return out
def uniq(xs):
    out=[]
    for x in sorted(xs):
        if not out or abs(x-out[-1])>TOL: out.append(x)
    return out
def quality(c,level,d):
    r=rng(c)
    if r<=0:return False
    b=body(c); pen=(c["high"]-level) if d=="SELL" else (level-c["low"])
    if pen<=0 or pen<.10*r:return False
    if d=="SELL":
        wick=c["high"]-max(c["open"],c["close"]); pos=(c["close"]-c["low"])/r
        return wick>=max(.5*b,.15*r) and pos<=.60
    wick=min(c["open"],c["close"])-c["low"]; pos=(c["close"]-c["low"])/r
    return wick>=max(.5*b,.15*r) and pos>=.40
def m5_conf(bs,d):
    if len(bs)<4:return None
    a,b,c=bs[-4],bs[-3],bs[-2]; z=bs[-1]
    if d=="BUY" and z["close"]>max(a["high"],b["high"],c["high"]):return "structure break"
    if d=="SELL" and z["close"]<min(a["low"],b["low"],c["low"]):return "structure break"
    recent=[body(x) for x in bs[-8:-1] if rng(x)>0]; avg=sum(recent)/len(recent) if recent else 0
    if avg and body(z)>=1.5*avg:
        if d=="BUY" and z["close"]>z["open"]:return "displacement"
        if d=="SELL" and z["close"]<z["open"]:return "displacement"
    if d=="BUY" and z["close"]>z["open"] and z["close"]>c["high"]:return "continuation"
    if d=="SELL" and z["close"]<z["open"] and z["close"]<c["low"]:return "continuation"
    return None
def target(d,entry,sl,ctx):
    levels=uniq(swings(ctx,"high")+equals(ctx,"high")) if d=="BUY" else uniq(swings(ctx,"low")+equals(ctx,"low"))
    if d=="BUY":
        v=[x for x in sorted(x for x in levels if x>entry) if MIN_RR<=(x-entry)/(entry-sl)<=MAX_RR]
    else:
        v=[x for x in sorted((x for x in levels if x<entry),reverse=True) if MIN_RR<=(entry-x)/(sl-entry)<=MAX_RR]
    return v[0] if v else None
def liquidity_setup(m5,m15,i):
    t=pt(m5[i]["openTime"]); ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
    if len(ctx)<10:return None
    highs=uniq(swings(ctx,"high")+equals(ctx,"high")); lows=uniq(swings(ctx,"low")+equals(ctx,"low")); c=m5[i]
    found=None
    for lv in sorted(highs,reverse=True):
        if c["high"]>lv and c["close"]<lv and quality(c,lv,"SELL"):found=("SELL",lv,"buy-side sweep");break
    if not found:
        for lv in sorted(lows):
            if c["low"]<lv and c["close"]>lv and quality(c,lv,"BUY"):found=("BUY",lv,"sell-side sweep");break
    if not found:return None
    d,lv,typ=found; conf=m5_conf(m5[:i+1],d)
    if not conf:return None
    entry=c["close"]; sl=max(c["high"],lv)+PAD if d=="SELL" else min(c["low"],lv)-PAD
    tp=target(d,entry,sl,ctx)
    if tp is None:return None
    rr=(entry-tp)/(sl-entry) if d=="SELL" else (tp-entry)/(entry-sl)
    return {"engine":"LIQUIDITY","direction":d,"entry":entry,"sl":sl,"tp":tp,"rr":rr,"trigger":conf,"time":c["openTime"],"location":typ}
def atr(bs,i):
    vals=[rng(x) for x in bs[max(0,i-FVG_ATR_LOOKBACK):i] if rng(x)>0]
    return sum(vals)/len(vals) if vals else None
def fvg_at(bs,i):
    if i<9:return None
    recent=[body(x) for x in bs[i-8:i-1] if rng(x)>0]
    if not recent:return None
    b=bs[i-1]
    if body(b)<1.25*sum(recent)/len(recent) or body(b)/rng(b)<.55:return None
    a,c=bs[i-2],bs[i]
    if a["high"]<c["low"]:lo,hi,d=a["high"],c["low"],"BUY"
    elif a["low"]>c["high"]:lo,hi,d=c["high"],a["low"],"SELL"
    else:return None
    aatr=atr(bs,i)
    if not aatr or hi-lo<.10*aatr:return None
    return {"lo":lo,"hi":hi,"mid":(lo+hi)/2,"atr":aatr,"direction":d,"time":c["openTime"]}
def near_liq(f,m15):
    t=pt(f["time"]); ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
    if len(ctx)<10:return False
    levels=uniq(swings(ctx,"low")+equals(ctx,"low")) if f["direction"]=="BUY" else uniq(swings(ctx,"high")+equals(ctx,"high"))
    return any(abs(f["mid"]-x)<=f["atr"] for x in levels)
def recent_sweep(bs,i,d):
    for j in range(max(5,i-FVG_SWEEP_LOOKBACK),i):
        prior=bs[max(0,j-30):j]; c=bs[j]
        highs=uniq(swings(prior,"high")+equals(prior,"high")); lows=uniq(swings(prior,"low")+equals(prior,"low"))
        levels=lows if d=="BUY" else highs
        for lv in levels:
            if d=="BUY" and c["low"]<lv and c["close"]>lv and quality(c,lv,d):return True
            if d=="SELL" and c["high"]>lv and c["close"]<lv and quality(c,lv,d):return True
    return False
def fvg_setup(m5,m15,i,pending=None):
    """Process one FVG lifecycle using only closed candles up to index i."""
    c=m5[i]
    if pending is not None:
        f=pending
        if (f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"]):
            pending=None
        elif c["low"]<=f["mid"]<=c["high"]:
            ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
            if ok:
                entry=f["mid"]; sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                tp=target(f["direction"],entry,sl,f["ctx"])
                if tp is not None:
                    rr=(tp-entry)/(entry-sl) if f["direction"]=="BUY" else (entry-tp)/(sl-entry)
                    return {"engine":"FVG","direction":f["direction"],"entry":entry,"sl":sl,"tp":tp,"rr":rr,"trigger":"FVG return + directional confirmation","time":c["openTime"],"location":"FVG+LIQUIDITY","entry_index":i},None
                pending=None
    if pending is None:
        f=fvg_at(m5,i)
        if f and (near_liq(f,m15) or recent_sweep(m5,i,f["direction"])):
            t=pt(f["time"])
            ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
            pending={**f,"ctx":ctx,"formed_index":i}
    return None,pending
def load():
    raw=urllib.request.urlopen(URL,timeout=60).read().decode()
    rows=[]
    for z in csv.DictReader(io.StringIO(raw)):
        t=z["datetime"]; t=t if t.endswith("+00:00") else t+"+00:00"
        rows.append({"openTime":t,"open":float(z["open"]),"high":float(z["high"]),"low":float(z["low"]),"close":float(z["close"])})
    return sorted(rows,key=lambda x:x["openTime"])
def agg(bs,mins):
    out=[];cur=None
    for c in bs:
        dt=pt(c["openTime"]); k=dt.replace(minute=(dt.minute//mins)*mins,second=0,microsecond=0).isoformat().replace("+00:00","Z")
        if not cur or cur["openTime"]!=k:
            if cur:out.append(cur)
            cur={"openTime":k,"open":c["open"],"high":c["high"],"low":c["low"],"close":c["close"]}
        else:
            cur["high"]=max(cur["high"],c["high"]);cur["low"]=min(cur["low"],c["low"]);cur["close"]=c["close"]
    if cur:out.append(cur)
    return out
def outcome(s,m5,start):
    for k in range(start+1,len(m5)):
        b=m5[k];sl=b["low"]<=s["sl"] if s["direction"]=="BUY" else b["high"]>=s["sl"];tp=b["high"]>=s["tp"] if s["direction"]=="BUY" else b["low"]<=s["tp"]
        if sl and tp:return "AMBIGUOUS"
        if tp:return "TP"
        if sl:return "SL"
    return "OPEN_AT_DATA_END"
def main():
    m5=load()
    start=os.getenv("COMBINED_START_UTC"); end=os.getenv("COMBINED_END_UTC")
    if start: m5=[x for x in m5 if pt(x["openTime"])>=pt(start)]
    if end: m5=[x for x in m5 if pt(x["openTime"])<=pt(end)]
    m15=agg(m5,15); signals=[];active_until=None;pending_fvg=None
    reason={"liquidity":0,"fvg":0,"both_same_cycle":0}
    i=50
    while i<len(m5):
        if active_until and pt(m5[i]["openTime"])<=active_until:
            pending_fvg=None
            i+=1
            continue
        f,pending_fvg=fvg_setup(m5,m15,i,pending_fvg)
        l=liquidity_setup(m5,m15,i)
        chosen=None
        if l and f:
            chosen=l;chosen["engine"]="CONFLUENCE";reason["both_same_cycle"]+=1
        else:
            chosen=l or f
        if not chosen:
            i+=1
            continue
        reason["liquidity" if l else "fvg"]+=1
        entry_index=chosen.get("entry_index",i)
        chosen={**chosen,"rr":round(chosen["rr"],2),"entry":round(chosen["entry"],3),"sl":round(chosen["sl"],3),"tp":round(chosen["tp"],3)}
        out=outcome(chosen,m5,entry_index);chosen["outcome"]=out;signals.append(chosen)
        pending_fvg=None
        if out in ("TP","SL","AMBIGUOUS"):
            for k in range(entry_index+1,len(m5)):
                b=m5[k];sl=b["low"]<=chosen["sl"] if chosen["direction"]=="BUY" else b["high"]>=chosen["sl"];tp=b["high"]>=chosen["tp"] if chosen["direction"]=="BUY" else b["low"]<=chosen["tp"]
                if sl or tp:active_until=pt(b["openTime"]);break
        i+=1
    w=sum(x["outcome"]=="TP" for x in signals);l=sum(x["outcome"]=="SL" for x in signals);a=sum(x["outcome"]=="AMBIGUOUS" for x in signals)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in signals)
    by={}
    for e in ("LIQUIDITY","FVG","CONFLUENCE"):
        ss=[x for x in signals if x["engine"]==e];ww=sum(x["outcome"]=="TP" for x in ss);ll=sum(x["outcome"]=="SL" for x in ss)
        by[e]={"signals":len(ss),"tp":ww,"sl":ll,"ambiguous":sum(x["outcome"]=="AMBIGUOUS" for x in ss),"win_rate":round(100*ww/(ww+ll),2) if ww+ll else None,"net_r":round(sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in ss),2)}
    conservative_net=net-a
    conservative_win_rate=round(100*w/(w+l+a),2) if w+l+a else None
    result={"status":"COMPLETED","source":URL,"validation_start_utc":start,"validation_end_utc":end,"data":{"m5":len(m5),"m15":len(m15),"first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"]},"overall":{"signals":len(signals),"tp":w,"sl":l,"ambiguous":a,"win_rate":round(100*w/(w+l),2) if w+l else None,"net_r":round(net,2),"avg_rr":round(sum(x["rr"] for x in signals)/len(signals),2) if signals else None,"current_treatment":{"ambiguous_excluded":True,"net_r":round(net,2),"win_rate_excluding_ambiguous":round(100*w/(w+l),2) if w+l else None},"conservative_treatment":{"ambiguous_as_sl":True,"net_r":round(conservative_net,2),"win_rate_including_ambiguous_as_loss":conservative_win_rate}},"by_engine":by,"router_counts":reason,"signals":signals,"notes":["Combined fixed-rule validation only.","One active setup at a time; if Liquidity and FVG trigger on the same cycle, one CONFLUENCE signal is counted.","No parameter tuning; research only.","FVG lifecycle is stateful: each FVG is formed once, expires on invalidation, and can trigger at most once.","Future-candle scanning/lookahead is prohibited.","Ambiguous outcomes are reported in two treatments: excluded from current Net R, or counted as -1R in conservative treatment."]}
    p=Path("backtest/combined_gold_hunter_results.json");p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(result,indent=2),encoding="utf-8");print(json.dumps(result,indent=2))

if __name__=="__main__":main()
