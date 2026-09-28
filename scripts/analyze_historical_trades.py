#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
src=ROOT/"backtest/historical_external_comparison.json"
out=ROOT/"backtest/historical_trade_analysis.json"

def summarize(xs):
    tp=sum(x["outcome"]=="TP" for x in xs)
    sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    return {"trades":len(xs),"tp":tp,"sl":sl,
            "win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),
            "avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}

def group(trades,keyfn):
    d=defaultdict(list)
    for x in trades:d[keyfn(x)].append(x)
    return {str(k):summarize(v) for k,v in sorted(d.items(), key=lambda z:str(z[0]))}

def session(t):
    h=int(t["candle_time"][11:13])
    # UTC sessions, simple fixed windows for diagnostic comparison only.
    if 7 <= h < 12:return "London"
    if 12 <= h < 17:return "New York"
    if 17 <= h < 22:return "New York late"
    if 0 <= h < 7:return "Asia"
    return "Off-session"

def rr_bucket(x):
    r=x["rr"]
    if r < 2.25:return "2.00-2.24"
    if r < 2.50:return "2.25-2.49"
    if r < 3.00:return "2.50-2.99"
    if r < 4.00:return "3.00-3.99"
    return "4.00+"

def analyze(trades):
    return {
      "overall":summarize(trades),
      "direction":group(trades,lambda x:x["signal"]),
      "confirmation":group(trades,lambda x:x["confirmation"]),
      "h1_bias":group(trades,lambda x:x["h1_bias"]),
      "session_utc":group(trades,session),
      "rr_bucket":group(trades,rr_bucket),
      "liquidity":group(trades,lambda x:x["liquidity"]),
      "month":group(trades,lambda x:x["candle_time"][:7]),
      "month_direction":group(trades,lambda x:(x["candle_time"][:7],x["signal"])),
      "month_confirmation":group(trades,lambda x:(x["candle_time"][:7],x["confirmation"])),
      "month_h1_bias":group(trades,lambda x:(x["candle_time"][:7],x["h1_bias"])),
      "month_session":group(trades,lambda x:(x["candle_time"][:7],session(x)))
    }

data=json.loads(src.read_text(encoding="utf-8"))
result={"source_data":data["data"],
        "base":analyze(data["base"]["details"]),
        "target_cap_2_5":analyze(data["target_cap_2_5"]["details"])}
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"base":result["base"]["overall"],"target_cap_2_5":result["target_cap_2_5"]["overall"]},ensure_ascii=False,indent=2))
