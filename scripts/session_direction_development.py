#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
tr=[x for x in src["details"] if "2026-03" <= x["candle_time"][:7] <= "2026-07"]
def s(xs):
 tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
 net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
 return {"trades":len(xs),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}
def sess(x):
 h=int(x["candle_time"][11:13])
 if 0<=h<7:return "Asia"
 if 7<=h<12:return "London"
 if 12<=h<17:return "New York"
 if 17<=h<22:return "New York late"
 return "Off-session"
d=defaultdict(list)
for x in tr:d[(x["signal"],sess(x))].append(x)
res={"period":"2026-03 through 2026-07","session_direction":{str(k):s(v) for k,v in sorted(d.items())},"overall":s(tr)}
(ROOT/"backtest/session_direction_development.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
