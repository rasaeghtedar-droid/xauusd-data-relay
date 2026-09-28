#!/usr/bin/env python3
import csv, io, json, urllib.request, importlib.util
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
DATA_START="2026-08-01T00:00:00+00:00"
EVAL_START="2026-09-12T00:00:00+00:00"
END="2026-09-25T23:59:59+00:00"
OUT=ROOT/"backtest"/"locked_sell_asia_sep12_25_external.json"

def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
def bar(t,o,h,l,c,v): return {"openTime":t.isoformat().replace("+00:00","Z"),"open":o,"high":h,"low":l,"close":c,"volume":v,"tickVolume":v,"isOpen":False}

def aggregate(m5,minutes):
    buckets=defaultdict(list); size=minutes*60
    for b in m5:
        e=int(dt(b["openTime"]).timestamp()); buckets[(e//size)*size].append(b)
    out=[]; need=minutes//5
    for ts,xs in sorted(buckets.items()):
        xs=sorted(xs,key=lambda x:x["openTime"])
        if len(xs)!=need: continue
        out.append(bar(dt(xs[0]["openTime"]),xs[0]["open"],max(x["high"] for x in xs),min(x["low"] for x in xs),xs[-1]["close"],sum(x["volume"] for x in xs)))
    return out

def load_engine():
    p=ROOT/"liquidity_hunter"/"liquidity_hunter.py"
    s=importlib.util.spec_from_file_location("lh",p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def outcome(sig,future):
    for b in future:
        sl=b["low"]<=sig["sl"] if sig["signal"]=="BUY" else b["high"]>=sig["sl"]
        tp=b["high"]>=sig["tp"] if sig["signal"]=="BUY" else b["low"]<=sig["tp"]
        if sl and tp:return "AMBIGUOUS_SAME_CANDLE"
        if tp:return "TP"
        if sl:return "SL"
    return "OPEN_AT_DATA_END"

def main():
    raw=urllib.request.urlopen(URL,timeout=60).read().decode("utf-8")
    rows=[]
    for r in csv.DictReader(io.StringIO(raw)):
        t=dt(r["datetime"])
        if DATA_START <= t.isoformat() <= END:
            rows.append(bar(t,float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"]),float(r["volume"])))
    rows.sort(key=lambda x:x["openTime"]); m15=aggregate(rows,15); h1=aggregate(rows,60); eng=load_engine()
    signals=[]; active_until=None; reasons={}
    for i,c in enumerate(rows):
        t=dt(c["openTime"])
        if t < dt(EVAL_START): continue
        if active_until and t<=active_until: continue
        c15=[x for x in m15 if dt(x["openTime"])<=t-timedelta(minutes=15)]
        c1=[x for x in h1 if dt(x["openTime"])<=t-timedelta(hours=1)]
        if len(c15)<10 or len(c1)<4 or i<10: continue
        r=eng.analyze({"intervals":{"5m":{"bars":rows[:i+1]},"15m":{"bars":c15},"1h":{"bars":c1}}})
        if r.get("status")=="SETUP FOUND":
            r["outcome"]=outcome(r,rows[i+1:]); signals.append(r)
            for b in rows[i+1:]:
                hit=(b["low"]<=r["sl"] if r["signal"]=="BUY" else b["high"]>=r["sl"]) or (b["high"]>=r["tp"] if r["signal"]=="BUY" else b["low"]<=r["tp"])
                if hit: active_until=dt(b["openTime"]); break
        else: reasons[r.get("reason","unknown")]=reasons.get(r.get("reason","unknown"),0)+1
    cand=[x for x in signals if x["signal"]=="SELL" and dt(x["candle_time"]).hour<7]
    tp=sum(x["outcome"]=="TP" for x in cand); sl=sum(x["outcome"]=="SL" for x in cand)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in cand)
    eq=peak=dd=0
    for x in sorted(cand,key=lambda z:z["candle_time"]):
        eq += x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0
        peak=max(peak,eq); dd=max(dd,peak-eq)
    res={"locked_rule":{"direction":"SELL","session":"Asia UTC 00:00-06:59","target_cap_RR":2.5},
         "source":URL,"source_period":{"data_start":DATA_START,"evaluation_start":EVAL_START,"requested_end":END,"requested_end":END},
         "data":{"m5":len(rows),"m15":len(m15),"h1":len(h1),"first_m5":rows[0]["openTime"] if rows else None,"last_m5":rows[-1]["openTime"] if rows else None},
         "result":{"trades":len(cand),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in cand)/len(cand),2) if cand else 0,"max_drawdown_R":round(dd,2)},
         "all_engine_signals":len(signals),"no_trade_reasons":reasons,"candidate_details":cand,
         "note":"Independent out-of-sample validation; no parameter tuning. Source is not LiteFinance."}
    OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(res,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
