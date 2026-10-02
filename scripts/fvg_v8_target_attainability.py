#!/usr/bin/env python3
"""Target attainability diagnostics for the locked executable FVG V8 cohort.
Research only. Uses the exact locked V8 trades and future candles only for measurement.
"""
from __future__ import annotations
import json
from pathlib import Path
from fvg_only_gold_hunter_backtest import load, pt

SRC=Path("backtest/fvg_executable_audit_v8_results.json")
OUT=Path("backtest/fvg_v8_target_attainability.json")

def main():
    d=json.loads(SRC.read_text(encoding="utf-8"))
    if d.get("status")!="COMPLETED":
        raise RuntimeError("V8 result is not COMPLETED")
    m5=load()
    start=d.get("validation_start_utc"); end=d.get("validation_end_utc")
    if start: m5=[x for x in m5 if pt(x["openTime"])>=pt(start)]
    if end: m5=[x for x in m5 if pt(x["openTime"])<=pt(end)]
    rows=[]
    for t in d["trades"]:
        e=t.get("_raw_entry",t["entry"]); s=t.get("_raw_sl",t["sl"]); risk=abs(e-s)
        end_idx=len(m5)-1
        if t["exit_time"]:
            hits=[i for i,x in enumerate(m5) if x["openTime"]==t["exit_time"]]
            if not hits: raise RuntimeError("exit candle not found")
            end_idx=hits[0]
        highs=[m5[i]["high"] for i in range(t["entry_index"]+1,end_idx+1)]
        lows=[m5[i]["low"] for i in range(t["entry_index"]+1,end_idx+1)]
        mfe=((max(highs)-e)/risk if t["direction"]=="BUY" else (e-min(lows))/risk) if highs else 0
        rows.append({"direction":t["direction"],"formation_time":t["formation_time"],"rr":t["rr"],"outcome":t["outcome"],"mfe_r":round(mfe,2)})
    levels=(2,2.5,3,4,5)
    attain={str(x)+"R":sum(r["mfe_r"]>=x for r in rows) for x in levels}
    result={"status":"COMPLETED","research_only":True,"signals":len(rows),
            "purpose":"Measure how often each locked V8 trade reached key R thresholds before its recorded exit; no target or entry rules changed.",
            "attainability":attain,
            "attainability_pct":{k:round(100*v/len(rows),2) for k,v in attain.items()},
            "by_direction":{d:{str(x)+"R":sum(r["direction"]==d and r["mfe_r"]>=x for r in rows) for x in levels} for d in ("BUY","SELL")},
            "by_outcome":{o:{str(x)+"R":sum(r["outcome"]==o and r["mfe_r"]>=x for r in rows) for x in levels} for o in ("TP","SL","AMBIGUOUS")},
            "invariants":{"same_cohort":len(rows)==len(d["trades"]),"no_rule_changes":True}}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))
if __name__=="__main__": main()
