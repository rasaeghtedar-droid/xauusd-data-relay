#!/usr/bin/env python3
"""Locked V8 target-vs-MFE diagnostic. Research only; no strategy changes."""
from __future__ import annotations
import json
from pathlib import Path
from fvg_only_gold_hunter_backtest import load, pt

SRC=Path("backtest/fvg_executable_audit_v8_results.json")
OUT=Path("backtest/fvg_v8_target_vs_mfe.json")

def main():
    d=json.loads(SRC.read_text(encoding="utf-8"))
    if d.get("status")!="COMPLETED": raise RuntimeError("V8 result is not COMPLETED")
    m5=load()
    start,end=d.get("validation_start_utc"),d.get("validation_end_utc")
    if start: m5=[x for x in m5 if pt(x["openTime"])>=pt(start)]
    if end: m5=[x for x in m5 if pt(x["openTime"])<=pt(end)]
    rows=[]
    for t in d["trades"]:
        e=t.get("_raw_entry",t["entry"]); s=t.get("_raw_sl",t["sl"]); q=t.get("_raw_tp",t["tp"])
        risk=abs(e-s); end_idx=len(m5)-1
        if t["exit_time"]:
            hit=next((i for i,x in enumerate(m5) if x["openTime"]==t["exit_time"]),None)
            if hit is None: raise RuntimeError("exit candle not found")
            end_idx=hit
        highs=[m5[i]["high"] for i in range(t["entry_index"]+1,end_idx+1)]
        lows=[m5[i]["low"] for i in range(t["entry_index"]+1,end_idx+1)]
        mfe=((max(highs)-e)/risk if t["direction"]=="BUY" else (e-min(lows))/risk) if highs else 0
        target_rr=((q-e)/risk if t["direction"]=="BUY" else (e-q)/risk)
        rows.append({"direction":t["direction"],"target_rr":round(target_rr,2),"mfe_r":round(mfe,2),"outcome":t["outcome"],"target_reached":mfe+1e-9>=target_rr,"mfe_gap_r":round(target_rr-mfe,2)})
    closed=[r for r in rows if r["outcome"]!="OPEN_AT_DATA_END"]
    result={"status":"COMPLETED","research_only":True,"signals":len(rows),
      "summary":{
        "target_reached_count":sum(r["target_reached"] for r in closed),
        "target_reached_pct":round(100*sum(r["target_reached"] for r in closed)/len(closed),2) if closed else None,
        "target_not_reached_count":sum(not r["target_reached"] for r in closed),
        "avg_target_rr":round(sum(r["target_rr"] for r in rows)/len(rows),2),
        "avg_mfe_r":round(sum(r["mfe_r"] for r in rows)/len(rows),2),
        "avg_mfe_gap_r":round(sum(r["mfe_gap_r"] for r in rows)/len(rows),2)},
      "by_direction":{d:{"signals":sum(r["direction"]==d for r in rows),"target_reached":sum(r["direction"]==d and r["target_reached"] for r in closed),"avg_target_rr":round(sum(r["target_rr"] for r in rows if r["direction"]==d)/sum(r["direction"]==d for r in rows),2),"avg_mfe_r":round(sum(r["mfe_r"] for r in rows if r["direction"]==d)/sum(r["direction"]==d for r in rows),2)} for d in ("BUY","SELL")},
      "rr_buckets":{b:{"signals":sum(r["target_rr"]>=lo and r["target_rr"]<hi for r in rows),"target_reached":sum(r["target_rr"]>=lo and r["target_rr"]<hi and r["target_reached"] for r in closed)} for b,lo,hi in (("2-2.5",2,2.5),("2.5-3",2.5,3),("3-4",3,4),("4-5",4,5),("5+",5,10**9))},
      "invariants":{"same_cohort":len(rows)==len(d["trades"]),"no_rule_changes":True},"trades":rows}
    OUT.write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))
if __name__=="__main__": main()
