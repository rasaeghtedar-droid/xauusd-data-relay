#!/usr/bin/env python3
"""Backtest both Liquidity Hunter variants on external historical XAUUSD 5m data."""

from __future__ import annotations
import csv, importlib.util, json, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
CSV_PATH=ROOT/"history"/"external_xauusd_5m.csv"
OUT=ROOT/"backtest"/"historical_external_comparison.json"
MAX_M5=500; MAX_M15=120; MAX_H1=60

def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
def load_bars():
    req=urllib.request.Request(URL,headers={"User-Agent":"xauusd-historical-backtest/1.0"})
    with urllib.request.urlopen(req,timeout=120) as r: raw=r.read()
    CSV_PATH.parent.mkdir(parents=True,exist_ok=True); CSV_PATH.write_bytes(raw)
    rows=[]
    for x in csv.DictReader(raw.decode("utf-8").splitlines()):
        t=x["datetime"]
        if not t.endswith("Z") and "+" not in t: t += "+00:00"
        rows.append({"openTime":dt(t).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),
                     "close":float(x["close"]),"volume":float(x.get("volume") or 0),
                     "tickVolume":float(x.get("volume") or 0),"isOpen":False})
    return rows

def aggregate(m5, minutes):
    out=[]; bucket=None
    for b in m5:
        t=dt(b["openTime"]); epoch=int(t.timestamp()); size=minutes*60
        start=datetime.fromtimestamp(epoch-(epoch%size),tz=timezone.utc)
        key=start.isoformat()
        if key!=bucket:
            if out: out[-1]["isOpen"]=False
            out.append({"openTime":start.strftime("%Y-%m-%dT%H:%M:%SZ"),"open":b["open"],
                        "high":b["high"],"low":b["low"],"close":b["close"],"volume":0,
                        "tickVolume":0,"isOpen":False}); bucket=key
        else:
            out[-1]["high"]=max(out[-1]["high"],b["high"]); out[-1]["low"]=min(out[-1]["low"],b["low"])
            out[-1]["close"]=b["close"]
        out[-1]["volume"]+=b["volume"]; out[-1]["tickVolume"]+=b["tickVolume"]
    return out

def engine(path,name):
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def outcome(sig,future):
    for b in future:
        sl=b["low"]<=sig["sl"] if sig["signal"]=="BUY" else b["high"]>=sig["sl"]
        tp=b["high"]>=sig["tp"] if sig["signal"]=="BUY" else b["low"]<=sig["tp"]
        if sl and tp:return "AMBIGUOUS_SAME_CANDLE"
        if tp:return "TP"
        if sl:return "SL"
    return "OPEN_AT_DATA_END"

def run(m5,m15,h1,mod):
    signals=[]; active_until=None; reasons={}
    for i in range(30,len(m5)):
        t=dt(m5[i]["openTime"])
        if active_until and t<=active_until: continue
        # Only fully closed higher-timeframe candles are available before this M5 candle.
        c15=[x for x in m15 if dt(x["openTime"])<=t-timedelta(minutes=15)][-MAX_M15:]
        c1=[x for x in h1 if dt(x["openTime"])<=t-timedelta(hours=1)][-MAX_H1:]
        if len(c15)<10 or len(c1)<4: continue
        data={"intervals":{"5m":{"bars":m5[max(0,i-MAX_M5+1):i+1]},"15m":{"bars":c15},"1h":{"bars":c1}}}
        res=mod.analyze(data)
        if res.get("status")=="SETUP FOUND":
            oc=outcome(res,m5[i+1:])
            signals.append({**res,"candle_time":m5[i]["openTime"],"outcome":oc})
            active_until=None
            for b in m5[i+1:]:
                sl=b["low"]<=res["sl"] if res["signal"]=="BUY" else b["high"]>=res["sl"]
                tp=b["high"]>=res["tp"] if res["signal"]=="BUY" else b["low"]<=res["tp"]
                if sl or tp: active_until=dt(b["openTime"]); break
    tp=sum(x["outcome"]=="TP" for x in signals); sl=sum(x["outcome"]=="SL" for x in signals)
    net=sum((x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0) for x in signals)
    return {"signals":len(signals),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),"details":signals}

def main():
    m5=load_bars(); m15=aggregate(m5,15); h1=aggregate(m5,60)
    base=engine(ROOT/"liquidity_hunter"/"liquidity_hunter.py","base")
    # The main engine currently uses the 2.5 target cap. Disable it for the uncapped A/B variant.
    base.MAX_TARGET_RR = 999999.0
    cap=engine(ROOT/"liquidity_hunter"/"liquidity_hunter.py","cap")
    result={"source":URL,"data":{"m5":len(m5),"m15":len(m15),"h1":len(h1),
      "first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"]},
      "base":run(m5,m15,h1,base),"target_cap_2_5":run(m5,m15,h1,cap)}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:(v if k=="data" else {x:v[x] for x in ("signals","tp","sl","win_rate","net_R")}) for k,v in result.items() if k!="source"},indent=2))

if __name__=="__main__": main()
