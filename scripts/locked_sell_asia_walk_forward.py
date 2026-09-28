#!/usr/bin/env python3
"""Locked walk-forward check for the SELL+Asia candidate.

The candidate rule is frozen before this run:
- signal == SELL
- session == Asia (00:00-06:59 UTC)
- Target Cap 2.5
No thresholds are tuned from the result.
We report sequential periods to reduce the risk of reading one aggregate number.
"""
import json
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
tr=sorted(src["details"],key=lambda x:x["candle_time"])

def session(x):
    h=int(x["candle_time"][11:13])
    return "Asia" if 0<=h<7 else "Other"

def summary(xs):
    tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    eq=peak=dd=0
    for x in sorted(xs,key=lambda z:z["candle_time"]):
        r=x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0
        eq+=r; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {"trades":len(xs),"tp":tp,"sl":sl,
            "win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),
            "avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0,
            "max_drawdown_R":round(dd,2)}

periods={
 "P1_2026-03_to_2026-05":("2026-03","2026-05"),
 "P2_2026-06_to_2026-07":("2026-06","2026-07"),
 "P3_2026-08_to_2026-09":("2026-08","2026-09"),
}
res={"locked_rule":{"direction":"SELL","session":"Asia UTC 00:00-06:59","target_cap_RR":2.5},
     "periods":{}}
for name,(a,b) in periods.items():
    subset=[x for x in tr if a<=x["candle_time"][:7]<=b]
    candidate=[x for x in subset if x["signal"]=="SELL" and session(x)=="Asia"]
    sell=[x for x in subset if x["signal"]=="SELL"]
    allx=subset
    res["periods"][name]={"all":summary(allx),"sell_only":summary(sell),"sell_asia_locked":summary(candidate)}
# Aggregate only as a descriptive summary, not a tuned score.
cand=[x for x in tr if x["signal"]=="SELL" and session(x)=="Asia"]
res["aggregate_locked_candidate"]=summary(cand)
res["note"]="Candidate was locked before this run; no threshold or session boundary was optimized from these results. This is validation, not proof of future performance."
out=ROOT/"backtest"/"locked_sell_asia_walk_forward.json"
out.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
