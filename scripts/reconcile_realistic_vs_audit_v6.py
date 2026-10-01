#!/usr/bin/env python3
"""Reconcile Realistic RR2 vs Executable Audit V6 at the first divergence.

This does not change either strategy. It compares the persisted trade sequences
and classifies the earliest mismatch by formation/confirmation, entry, SL/TP/RR,
or outcome timing.
"""
from __future__ import annotations
import json
from pathlib import Path

REAL=Path("backtest/fvg_realistic_rr2_results.json")
AUDIT=Path("backtest/fvg_executable_audit_v6_results.json")

def key(t): return (t.get("direction"),t.get("formation_time"),t.get("confirmation_time"))

def main():
    r=json.loads(REAL.read_text()); a=json.loads(AUDIT.read_text())
    rt=r["trades"]; at=a["trades"]
    common=min(len(rt),len(at)); first=None
    mismatches=[]
    for i in range(common):
        rk,ak=key(rt[i]),key(at[i])
        if rk!=ak:
            first={"position":i,"type":"LIFECYCLE","realistic":rk,"audit_v6":ak}
            break
        fields={}
        for f in ("entry","sl","tp","rr","outcome","entry_time","exit_time"):
            if rt[i].get(f)!=at[i].get(f):
                fields[f]={"realistic":rt[i].get(f),"audit_v6":at[i].get(f)}
        if fields and first is None:
            first={"position":i,"type":"TRADE_FIELDS","identity":rk,"differences":fields}
            break
    if first is None and len(rt)!=len(at):
        first={"position":common,"type":"COUNT_ONLY","realistic_count":len(rt),"audit_v6_count":len(at)}
    # Show first 5 identities from each side to make the divergence obvious.
    out={
      "status":"COMPLETED",
      "realistic_signals":len(rt),
      "audit_v6_signals":len(at),
      "first_divergence":first,
      "first_5_realistic":[key(x) for x in rt[:5]],
      "first_5_audit_v6":[key(x) for x in at[:5]],
      "interpretation":"A lifecycle mismatch means the entry/target eligibility or pending-state rules diverged before the recorded trade. A trade-fields mismatch with identical identity isolates the difference to entry/SL/TP/RR or exit timing.",
    }
    p=Path("backtest/fvg_realistic_vs_audit_v6_reconciliation.json")
    p.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))

if __name__=="__main__": main()
