#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
tr=src["details"]
def session(x):
    h=int(x["candle_time"][11:13])
    if 0<=h<7:return "Asia"
    if 7<=h<12:return "London"
    if 12<=h<17:return "New York"
    if 17<=h<22:return "New York late"
    return "Off-session"
def summary(xs):
    tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    eq=0; peak=0; maxdd=0
    for x in sorted(xs,key=lambda z:z["candle_time"]):
        r=x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0
        eq+=r; peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
    return {"trades":len(xs),"tp":tp,"sl":sl,
            "win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),
            "avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0,
            "max_drawdown_R":round(maxdd,2)}
sets={
 "all_main_cap_2_5":tr,
 "sell_only":[x for x in tr if x["signal"]=="SELL"],
 "sell_asia_only":[x for x in tr if x["signal"]=="SELL" and session(x)=="Asia"]
}
res={"period":{"first":src["data"]["first_m5"],"last":src["data"]["last_m5"]},
     "comparison":{k:summary(v) for k,v in sets.items()},
     "definition":"All sets use the same V3-generated trades, Target Cap 2.5, closed candles and non-overlapping trade handling. This is a candidate diagnostic; production rules are unchanged."}
(ROOT/"backtest/sell-asia-candidate-comparison.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
