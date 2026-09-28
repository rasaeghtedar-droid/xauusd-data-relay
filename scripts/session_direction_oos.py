#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
tr=[x for x in src["details"] if x["candle_time"][:7] in ("2026-08","2026-09")]
def s(xs):
 tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
 net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
 return {"trades":len(xs),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}
def sess(t):
 h=int(t["candle_time"][11:13])
 if 7<=h<12:return "London"
 if 12<=h<17:return "New York"
 if 17<=h<22:return "New York late"
 if 0<=h<7:return "Asia"
 return "Off-session"
d=defaultdict(list)
for x in tr:d[(x["signal"],sess(x))].append(x)
res={"period":"2026-08 through 2026-09","session_direction":{str(k):s(v) for k,v in sorted(d.items())},
     "session":{k:s([x for x in tr if sess(x)==k]) for k in ["Asia","London","New York","New York late","Off-session"]}}
(ROOT/"backtest/session_direction_oos.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
