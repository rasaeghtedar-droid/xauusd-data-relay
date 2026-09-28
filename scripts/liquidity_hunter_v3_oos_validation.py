#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
tr=src["details"]
oos=[x for x in tr if x["candle_time"][:7] in ("2026-08","2026-09")]
def s(xs):
 tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
 net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
 return {"trades":len(xs),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}
res={"period":"2026-08 through 2026-09","all":s(oos),
     "score_2":s([x for x in oos if x["quality_score"]==2]),
     "score_3_plus":s([x for x in oos if x["quality_score"]>=3]),
     "score_3":s([x for x in oos if x["quality_score"]==3]),
     "score_4":s([x for x in oos if x["quality_score"]==4])}
out=ROOT/"backtest"/"v3_quality_oos_validation.json"; out.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
