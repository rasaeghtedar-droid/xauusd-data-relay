#!/usr/bin/env python3
"""Controlled Liquidity Hunter v2 experiment.

v2 rule: keep BUY logic unchanged; for SELL, require a short-term bearish
structure break as the primary M5 confirmation. Run uncapped and target-cap 2.5.
This does not modify the production engine.
"""
from __future__ import annotations
import csv, importlib.util, json, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
OUT=ROOT/"backtest"/"liquidity_hunter_v2_experiment.json"

def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
def load():
    req=urllib.request.Request(URL,headers={"User-Agent":"xauusd-v2-experiment/1.0"})
    with urllib.request.urlopen(req,timeout=120) as r: raw=r.read()
    rows=[]
    for x in csv.DictReader(raw.decode("utf-8").splitlines()):
        t=x["datetime"]
        if not t.endswith("Z") and "+" not in t:t+="+00:00"
        rows.append({"openTime":dt(t).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),
          "close":float(x["close"]),"volume":float(x.get("volume") or 0),
          "tickVolume":float(x.get("volume") or 0),"isOpen":False})
    return rows

def agg(m5,mins):
    out=[]; key=None
    for b in m5:
        t=dt(b["openTime"]); size=mins*60; start=datetime.fromtimestamp(int(t.timestamp())-(int(t.timestamp())%size),tz=timezone.utc)
        k=start.isoformat()
        if k!=key:
            out.append({"openTime":start.strftime("%Y-%m-%dT%H:%M:%SZ"),"open":b["open"],"high":b["high"],"low":b["low"],"close":b["close"],"volume":0,"tickVolume":0,"isOpen":False}); key=k
        else:
            out[-1]["high"]=max(out[-1]["high"],b["high"]); out[-1]["low"]=min(out[-1]["low"],b["low"]); out[-1]["close"]=b["close"]
        out[-1]["volume"]+=b["volume"]; out[-1]["tickVolume"]+=b["tickVolume"]
    return out

def engine():
    p=ROOT/"liquidity_hunter"/"liquidity_hunter.py"
    spec=importlib.util.spec_from_file_location("lhv2base",p); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def v2_analyze(mod,data):
    r=mod.analyze(data)
    if r.get("status")=="SETUP FOUND" and r.get("signal")=="SELL" and r.get("confirmation")!="short-term bearish structure break":
        return {"status":"NO TRADE","reason":"v2 SELL requires bearish structure break"}
    return r

def outcome(sig,future):
    for b in future:
        sl=b["low"]<=sig["sl"] if sig["signal"]=="BUY" else b["high"]>=sig["sl"]
        tp=b["high"]>=sig["tp"] if sig["signal"]=="BUY" else b["low"]<=sig["tp"]
        if sl and tp:return "AMBIGUOUS_SAME_CANDLE"
        if tp:return "TP"
        if sl:return "SL"
    return "OPEN_AT_DATA_END"

def run(m5,m15,h1,mod,cap):
    mod.MAX_TARGET_RR=2.5 if cap else 999999.0
    trades=[]
    active_until=None
    for i in range(30,len(m5)):
        t=dt(m5[i]["openTime"])
        if active_until and t<=active_until: continue
        c15=[x for x in m15 if dt(x["openTime"])<=t-timedelta(minutes=15)][-120:]
        c1=[x for x in h1 if dt(x["openTime"])<=t-timedelta(hours=1)][-60:]
        if len(c15)<10 or len(c1)<4: continue
        data={"intervals":{"5m":{"bars":m5[max(0,i-499):i+1]},"15m":{"bars":c15},"1h":{"bars":c1}}}
        r=v2_analyze(mod,data)
        if r.get("status")=="SETUP FOUND":
            oc=outcome(r,m5[i+1:])
            trades.append({**r,"candle_time":m5[i]["openTime"],"outcome":oc})
            for b in m5[i+1:]:
                sl=b["low"]<=r["sl"] if r["signal"]=="BUY" else b["high"]>=r["sl"]
                tp=b["high"]>=r["tp"] if r["signal"]=="BUY" else b["low"]<=r["tp"]
                if sl or tp: active_until=dt(b["openTime"]); break
    tp=sum(x["outcome"]=="TP" for x in trades); sl=sum(x["outcome"]=="SL" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)
    return {"trades":len(trades),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else 0,
            "details":trades}

def main():
    m5=load(); m15=agg(m5,15); h1=agg(m5,60); mod=engine()
    result={"data":{"m5":len(m5),"m15":len(m15),"h1":len(h1),"first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"]},
      "v2_uncapped":run(m5,m15,h1,mod,False),"v2_target_cap_2_5":run(m5,m15,h1,mod,True)}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:{x:v[x] for x in ("trades","tp","sl","win_rate","net_R","avg_RR")} if k!="data" else v for k,v in result.items()},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
