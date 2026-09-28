#!/usr/bin/env python3
import csv, io, json, urllib.request, importlib.util
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/sample-2026-07-31/XAUUSD_5m.csv"
START="2026-02-02T00:00:00+00:00"
END="2026-03-25T23:59:59+00:00"
OUT=ROOT/"backtest"/"locked_sell_asia_pre_march_external.json"

def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
def bar(t,o,h,l,c,v):
    return {"openTime":t.isoformat().replace("+00:00","Z"),"open":o,"high":h,"low":l,"close":c,"volume":v,"tickVolume":v,"isOpen":False}

def aggregate(m5, minutes):
    buckets=defaultdict(list)
    step=timedelta(minutes=minutes)
    for b in m5:
        t=dt(b["openTime"])
        epoch=int(t.timestamp())
        size=minutes*60
        start_ts=(epoch//size)*size
        buckets[start_ts].append(b)
    out=[]
    need=minutes//5
    for ts, xs in sorted(buckets.items()):
        xs=sorted(xs,key=lambda x:x["openTime"])
        if len(xs)!=need:
            continue
        t=xs[0]["openTime"]
        out.append(bar(dt(t),xs[0]["open"],max(x["high"] for x in xs),min(x["low"] for x in xs),xs[-1]["close"],sum(x["volume"] for x in xs)))
    return out

def load_engine():
    path=ROOT/"liquidity_hunter"/"liquidity_hunter.py"
    spec=importlib.util.spec_from_file_location("lh_external",path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def evaluate(signal,future):
    for b in future:
        sl=b["low"]<=signal["sl"] if signal["signal"]=="BUY" else b["high"]>=signal["sl"]
        tp=b["high"]>=signal["tp"] if signal["signal"]=="BUY" else b["low"]<=signal["tp"]
        if sl and tp:return "AMBIGUOUS_SAME_CANDLE"
        if tp:return "TP"
        if sl:return "SL"
    return "OPEN_AT_DATA_END"

def main():
    raw=urllib.request.urlopen(URL,timeout=60).read().decode("utf-8")
    rows=[]
    for r in csv.DictReader(io.StringIO(raw)):
        t=dt(r["datetime"])
        if START <= r["datetime"] <= END:
            rows.append(bar(t,float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"]),float(r["volume"])))
    rows.sort(key=lambda x:x["openTime"])
    m15=aggregate(rows,15); h1=aggregate(rows,60)
    eng=load_engine()
    signals=[]; reasons={}; active_until=None
    for i,c in enumerate(rows):
        t=dt(c["openTime"])
        if active_until is not None and t<=active_until: continue
        ctx15=[x for x in m15 if dt(x["openTime"]) <= t-timedelta(minutes=15)]
        ctx1=[x for x in h1 if dt(x["openTime"]) <= t-timedelta(hours=1)]
        if len(ctx15)<10 or len(ctx1)<4 or i<10: continue
        result=eng.analyze({"intervals":{"5m":{"bars":rows[:i+1]},"15m":{"bars":ctx15},"1h":{"bars":ctx1}}})
        if result.get("status")=="SETUP FOUND":
            result["outcome"]=evaluate(result,rows[i+1:])
            signals.append(result)
            for b in rows[i+1:]:
                hit_sl=b["low"]<=result["sl"] if result["signal"]=="BUY" else b["high"]>=result["sl"]
                hit_tp=b["high"]>=result["tp"] if result["signal"]=="BUY" else b["low"]<=result["tp"]
                if hit_sl or hit_tp:
                    active_until=dt(b["openTime"]); break
        else:
            reasons[result.get("reason","unknown")]=reasons.get(result.get("reason","unknown"),0)+1
    candidate=[x for x in signals if x["signal"]=="SELL" and 0<=dt(x["candle_time"]).hour<7]
    tp=sum(x["outcome"]=="TP" for x in candidate); sl=sum(x["outcome"]=="SL" for x in candidate)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in candidate)
    eq=peak=dd=0
    for x in sorted(candidate,key=lambda z:z["candle_time"]):
        r=x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0
        eq+=r; peak=max(peak,eq); dd=max(dd,peak-eq)
    res={"locked_rule":{"direction":"SELL","session":"Asia UTC 00:00-06:59","target_cap_RR":2.5},
         "source":URL,"source_period":{"requested_start":START,"requested_end":END},
         "data":{"m5":len(rows),"m15":len(m15),"h1":len(h1),"first_m5":rows[0]["openTime"] if rows else None,"last_m5":rows[-1]["openTime"] if rows else None},
         "result":{"trades":len(candidate),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in candidate)/len(candidate),2) if candidate else 0,"max_drawdown_R":round(dd,2)},
         "all_engine_signals":len(signals),"no_trade_reasons":reasons,
         "candidate_details":candidate,
         "note":"Independent source validation. No parameter tuning. Source is not LiteFinance, so this is cross-source validation rather than broker-identical validation."}
    OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(res,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
