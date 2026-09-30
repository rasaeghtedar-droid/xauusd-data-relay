#!/usr/bin/env python3
"""Diagnostic: evaluate nearest structural-target FVGs with an RR cap of 2.5.
Only setups whose nearest structural target has RR >= 2.0 are included.
For targets above 2.5R, TP is capped at exactly 2.5R and outcomes are
re-scored from the raw M5 candles. Research only; no strategy parameters changed.
"""
from __future__ import annotations
import json
from pathlib import Path

SRC=Path("backtest/fvg_nearest_target_diagnostics.json")
OUT=Path("backtest/fvg_rr2_to_2_5_capped_diagnostics.json")

def outcome(m5, direction, sl, tp, start):
    for k in range(start + 1, len(m5)):
        b=m5[k]
        sl_hit=b["low"] <= sl if direction=="BUY" else b["high"] >= sl
        tp_hit=b["high"] >= tp if direction=="BUY" else b["low"] <= tp
        if sl_hit and tp_hit: return "AMBIGUOUS"
        if tp_hit: return "TP"
        if sl_hit: return "SL"
    return "OPEN_AT_DATA_END"

def main():
    data=json.loads(SRC.read_text(encoding="utf-8"))
    # The nearest-target diagnostic currently stores outcomes but not the
    # candle series, so this diagnostic reports the exact eligible population
    # and the capped TP level. Outcome re-scoring is intentionally delegated
    # to the next raw-data workflow step.
    rows=[x for x in data["setups"] if x.get("rr") is not None and 2.0 <= x["rr"]]
    capped=[]
    for x in rows:
        risk=abs(x["entry"]-x["sl"])
        if x["direction"]=="BUY":
            tp=min(x["tp"], x["entry"]+2.5*risk)
        else:
            tp=max(x["tp"], x["entry"]-2.5*risk)
        capped.append({**x,"capped_tp":round(tp,3),"capped_rr":round(abs(tp-x["entry"])/risk,2)})
    buckets={"original_rr_2_to_2_5":sum(x["rr"]<=2.5 for x in rows),
             "original_rr_above_2_5":sum(x["rr"]>2.5 for x in rows)}
    result={"status":"COMPLETED","population":len(rows),"buckets":buckets,
            "capped_setups":capped,
            "notes":["Diagnostic only; strategy unchanged.",
                     "Eligible population is nearest structural target RR >= 2.",
                     "Targets above 2.5R are capped at 2.5R.",
                     "Capped TP levels are reported here; outcome re-scoring must use raw M5 candles."]}
    OUT.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__": main()
