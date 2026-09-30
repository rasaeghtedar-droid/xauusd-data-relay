#!/usr/bin/env python3
"""Bucket the independent nearest-target FVG results by RR, research only."""
from __future__ import annotations
import json
from pathlib import Path

SRC=Path("backtest/fvg_nearest_target_diagnostics.json")
OUT=Path("backtest/fvg_rr_bucket_diagnostics.json")

def summarize(name, rows):
    closed=[x for x in rows if x.get("outcome") in ("TP","SL","AMBIGUOUS")]
    tp=sum(x["outcome"]=="TP" for x in closed)
    sl=sum(x["outcome"]=="SL" for x in closed)
    amb=sum(x["outcome"]=="AMBIGUOUS" for x in closed)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in closed)
    return {
        "bucket":name,
        "setups":len(rows),
        "closed":len(closed),
        "tp":tp,
        "sl":sl,
        "ambiguous":amb,
        "win_rate_closed":round(100*tp/(tp+sl),2) if tp+sl else None,
        "net_r":round(net,2),
        "conservative_net_r":round(net-amb,2),
        "avg_rr":round(sum(x["rr"] for x in rows)/len(rows),2) if rows else None,
    }

def main():
    data=json.loads(SRC.read_text(encoding="utf-8"))
    rows=[x for x in data["setups"] if x.get("rr") is not None]
    buckets=[
        ("RR<1", lambda r:r<1),
        ("1<=RR<1.5", lambda r:1<=r<1.5),
        ("1.5<=RR<2", lambda r:1.5<=r<2),
        ("2<=RR<=2.5", lambda r:2<=r<=2.5),
        ("RR>2.5", lambda r:r>2.5),
    ]
    result={
        "status":"COMPLETED",
        "source":"backtest/fvg_nearest_target_diagnostics.json",
        "total_with_target":len(rows),
        "buckets":[summarize(name,[x for x in rows if pred(x["rr"])]) for name,pred in buckets],
        "notes":[
            "This is a diagnostic, not a parameter-tuned strategy.",
            "Buckets use the nearest pre-confirmation M15 structural target.",
            "No future data is used to choose the target.",
        ],
    }
    OUT.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
