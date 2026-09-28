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
def group(xs,key):
 d=defaultdict(list)
 for x in xs:d[key(x)].append(x)
 return {str(k):s(v) for k,v in sorted(d.items(),key=lambda z:str(z[0]))}
res={"period":"2026-08 through 2026-09","direction":group(tr,lambda x:x["signal"]),
"direction_confirmation":group(tr,lambda x:(x["signal"],x["confirmation"])),
"direction_h1_bias":group(tr,lambda x:(x["signal"],x["h1_bias"])),
"direction_rr":group(tr,lambda x:(x["signal"],"2.00-2.49" if x["rr"]<2.5 else "2.50+")),
"direction_liquidity":group(tr,lambda x:(x["signal"],x["liquidity"]))}
(ROOT/"backtest/direction_confirmation_oos.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
