#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
tr=sorted(src["details"],key=lambda x:x["candle_time"])
def session(x):
    h=int(x["candle_time"][11:13])
    return 0<=h<7
def summary(xs):
    tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    eq=peak=dd=0
    for x in sorted(xs,key=lambda z:z["candle_time"]):
        r=x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0
        eq+=r; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {"trades":len(xs),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0,"max_drawdown_R":round(dd,2)}
pre=[x for x in tr if x["candle_time"][:7] < "2026-03"]
candidate=[x for x in pre if x["signal"]=="SELL" and session(x)]
res={"locked_rule":{"direction":"SELL","session":"Asia UTC 00:00-06:59","target_cap_RR":2.5},
     "available_data":{"first":tr[0]["candle_time"] if tr else None,"last":tr[-1]["candle_time"] if tr else None,"pre_march_count":len(pre)},
     "result":summary(candidate),
     "status":"VALIDATE" if pre else "NO_PRE_MARCH_DATA",
     "note":"No parameter tuning. This is an untouched historical check only if data before 2026-03 is present."}
(ROOT/"backtest/locked_sell_asia_pre_march.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
