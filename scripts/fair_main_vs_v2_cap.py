#!/usr/bin/env python3
from pathlib import Path
import json, importlib.util, urllib.request, csv
from datetime import datetime, timezone, timedelta

ROOT=Path(__file__).resolve().parents[1]
URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
OUT=ROOT/"backtest"/"fair_main_vs_v2_cap.json"

def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
def load():
    req=urllib.request.Request(URL,headers={"User-Agent":"xauusd-fair-compare/1.0"})
    with urllib.request.urlopen(req,timeout=120) as r: raw=r.read()
    out=[]
    for x in csv.DictReader(raw.decode().splitlines()):
        t=x["datetime"]
        if not t.endswith("Z") and "+" not in t:t+="+00:00"
        out.append({"openTime":dt(t).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"]),
          "volume":float(x.get("volume") or 0),"tickVolume":float(x.get("volume") or 0),"isOpen":False})
    return out

def agg(m5,n):
    out=[]; key=None; sec=n*60
    for b in m5:
        ts=int(dt(b["openTime"]).timestamp()); start=datetime.fromtimestamp(ts-ts%sec,tz=timezone.utc)
        k=start.isoformat()
        if k!=key:
            out.append({"openTime":start.strftime("%Y-%m-%dT%H:%M:%SZ"),"open":b["open"],"high":b["high"],"low":b["low"],"close":b["close"],"volume":0,"tickVolume":0,"isOpen":False}); key=k
        else:
            out[-1]["high"]=max(out[-1]["high"],b["high"]); out[-1]["low"]=min(out[-1]["low"],b["low"]); out[-1]["close"]=b["close"]
        out[-1]["volume"]+=b["volume"]; out[-1]["tickVolume"]+=b["tickVolume"]
    return out

def mod():
    p=ROOT/"liquidity_hunter"/"liquidity_hunter.py"; spec=importlib.util.spec_from_file_location("lh",p)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def outcome(sig,future):
    for b in future:
        sl=b["low"]<=sig["sl"] if sig["signal"]=="BUY" else b["high"]>=sig["sl"]
        tp=b["high"]>=sig["tp"] if sig["signal"]=="BUY" else b["low"]<=sig["tp"]
        if sl and tp:return "AMBIGUOUS_SAME_CANDLE"
        if tp:return "TP"
        if sl:return "SL"
    return "OPEN_AT_DATA_END"

def run(m5,m15,h1,m,mode):
    m.MAX_TARGET_RR=2.5
    trades=[]; active_end=None
    for i in range(30,len(m5)):
        t=dt(m5[i]["openTime"])
        if active_end and t<=active_end: continue
        c15=[x for x in m15 if dt(x["openTime"])<=t-timedelta(minutes=15)][-120:]
        c1=[x for x in h1 if dt(x["openTime"])<=t-timedelta(hours=1)][-60:]
        if len(c15)<10 or len(c1)<4: continue
        data={"intervals":{"5m":{"bars":m5[max(0,i-499):i+1]},"15m":{"bars":c15},"1h":{"bars":c1}}}
        r=m.analyze(data)
        if r.get("status")!="SETUP FOUND": continue
        if mode=="v2" and r.get("signal")=="SELL" and r.get("confirmation")!="short-term bearish structure break": continue
        r={**r,"candle_time":m5[i]["openTime"],"outcome":outcome(r,m5[i+1:])}
        trades.append(r)
        for b in m5[i+1:]:
            sl=b["low"]<=r["sl"] if r["signal"]=="BUY" else b["high"]>=r["sl"]
            tp=b["high"]>=r["tp"] if r["signal"]=="BUY" else b["low"]<=r["tp"]
            if sl or tp: active_end=dt(b["openTime"]); break
    tp=sum(x["outcome"]=="TP" for x in trades); sl=sum(x["outcome"]=="SL" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)
    return {"summary":{"trades":len(trades),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else 0},"details":trades}

m5=load(); m15=agg(m5,15); h1=agg(m5,60); m=mod()
result={"data":{"m5":len(m5),"m15":len(m15),"h1":len(h1),"first":m5[0]["openTime"],"last":m5[-1]["openTime"]},
 "main_cap_2_5":run(m5,m15,h1,m,"main"),"v2_cap_2_5":run(m5,m15,h1,m,"v2")}
# Compare timestamps that appear only in main, only in v2, and common.
a={x["candle_time"]:x for x in result["main_cap_2_5"]["details"]}
b={x["candle_time"]:x for x in result["v2_cap_2_5"]["details"]}
common=set(a)&set(b)
result["comparison"]={"main_only":len(set(a)-set(b)),"v2_only":len(set(b)-set(a)),"common":len(common),
 "main_only_net_R":round(sum(a[k]["rr"] if a[k]["outcome"]=="TP" else -1 for k in set(a)-set(b)),2),
 "v2_only_net_R":round(sum(b[k]["rr"] if b[k]["outcome"]=="TP" else -1 for k in set(b)-set(a)),2)}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"data":result["data"],"main":result["main_cap_2_5"]["summary"],"v2":result["v2_cap_2_5"]["summary"],"comparison":result["comparison"]},ensure_ascii=False,indent=2))
