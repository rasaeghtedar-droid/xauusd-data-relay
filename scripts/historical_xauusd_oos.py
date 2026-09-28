#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
src=ROOT/"backtest/historical_external_comparison.json"
out=ROOT/"backtest/historical_xauusd_oos.json"
data=json.loads(src.read_text(encoding="utf-8"))

# Locked OOS period: Aug 1 through Sep 25, 2026.
OOS_START="2026-08"
OOS_END="2026-09"

def pick(trades):
    return [x for x in trades if OOS_START <= x["candle_time"][:7] <= OOS_END]

def summary(xs):
    tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    return {"trades":len(xs),"tp":tp,"sl":sl,
            "win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),
            "avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}

def group(xs,key):
    d=defaultdict(list)
    for x in xs:d[key(x)].append(x)
    return {str(k):summary(v) for k,v in sorted(d.items(),key=lambda z:str(z[0]))}

def session(x):
    h=int(x["candle_time"][11:13])
    if 7<=h<12:return "London"
    if 12<=h<17:return "New York"
    if 17<=h<22:return "New York late"
    if 0<=h<7:return "Asia"
    return "Off-session"

def analyze(xs):
    return {
      "overall":summary(xs),
      "direction":group(xs,lambda x:x["signal"]),
      "confirmation":group(xs,lambda x:x["confirmation"]),
      "h1_bias":group(xs,lambda x:x["h1_bias"]),
      "session_utc":group(xs,session),
      "liquidity":group(xs,lambda x:x["liquidity"]),
      "month":group(xs,lambda x:x["candle_time"][:7])
    }

base=pick(data["base"]["details"])
cap=pick(data["target_cap_2_5"]["details"])
result={
 "period":{"development":"2026-03 through 2026-07","out_of_sample":"2026-08-01 through 2026-09-25",
           "note":"Rules were not changed for this evaluation; August-September trades are evaluated as a locked holdout."},
 "base":analyze(base),
 "target_cap_2_5":analyze(cap)
}
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"base":result["base"]["overall"],"target_cap_2_5":result["target_cap_2_5"]["overall"]},ensure_ascii=False,indent=2))
