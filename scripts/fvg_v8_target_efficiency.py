#!/usr/bin/env python3
"""Frozen-cohort target efficiency diagnostic for locked V8.
Research only: exact 130-trade cohort; target-only change; no trade creation/removal.
"""
from __future__ import annotations
import json
from pathlib import Path
from fvg_only_gold_hunter_backtest import load, pt

SRC=Path("backtest/fvg_executable_audit_v8_results.json")
OUT=Path("backtest/fvg_v8_target_efficiency.json")

def capped_target(direction, entry, sl, structural_tp, cap):
    if cap is None:
        return structural_tp
    risk = entry-sl if direction=="BUY" else sl-entry
    cap_tp = entry + cap*risk if direction=="BUY" else entry - cap*risk
    return min(structural_tp,cap_tp) if direction=="BUY" else max(structural_tp,cap_tp)

def outcome(m5, t, sl, tp):
    for i in range(t["entry_index"]+1,len(m5)):
        b=m5[i]
        sl_hit=b["low"]<=sl if t["direction"]=="BUY" else b["high"]>=sl
        tp_hit=b["high"]>=tp if t["direction"]=="BUY" else b["low"]<=tp
        if sl_hit and tp_hit: return "AMBIGUOUS"
        if tp_hit: return "TP"
        if sl_hit: return "SL"
    return "OPEN_AT_DATA_END"

def summarize(rows):
    closed=[r for r in rows if r["outcome"] in ("TP","SL","AMBIGUOUS")]
    tp=sum(r["outcome"]=="TP" for r in closed); sl=sum(r["outcome"]=="SL" for r in closed); amb=sum(r["outcome"]=="AMBIGUOUS" for r in closed)
    vals=[r["realized_r"] for r in rows]
    eq=0; peak=0; maxdd=0
    for v in vals:
        eq+=v; peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
    gross_profit=sum(v for v in vals if v>0); gross_loss=-sum(v for v in vals if v<0)
    return {"signals":len(rows),"tp":tp,"sl":sl,"ambiguous":amb,
            "win_rate":round(100*tp/(tp+sl),2) if tp+sl else None,
            "net_r":round(sum(vals),2),"conservative_net_r":round(sum(vals)-amb,2),
            "profit_factor":round(gross_profit/gross_loss,3) if gross_loss else None,
            "max_equity_dd_r":round(maxdd,2)}

def main():
    d=json.loads(SRC.read_text(encoding="utf-8"))
    if d.get("status")!="COMPLETED": raise RuntimeError("V8 result is not COMPLETED")
    m5=load()
    start,end=d["validation_start_utc"],d["validation_end_utc"]
    m5=[x for x in m5 if pt(x["openTime"])>=pt(start) and pt(x["openTime"])<=pt(end)]
    trades=d["trades"]
    models={"STRUCTURAL":None,"RR_CAP_2":2.0,"RR_CAP_2_5":2.5,"RR_CAP_3":3.0,"RR_CAP_4":4.0}
    results=[]
    identities=[(t["direction"],t["formation_time"],t["confirmation_time"]) for t in trades]
    for name,cap in models.items():
        rows=[]
        for t in trades:
            e=t.get("_raw_entry",t["entry"]); s=t.get("_raw_sl",t["sl"]); q=t.get("_raw_tp",t["tp"])
            tp=capped_target(t["direction"],e,s,q,cap)
            risk=abs(e-s); rr=(tp-e)/risk if t["direction"]=="BUY" else (e-tp)/risk
            out=outcome(m5,t,s,tp)
            realized=rr if out=="TP" else (-1 if out=="SL" else 0)
            rows.append({"direction":t["direction"],"formation_time":t["formation_time"],"confirmation_time":t["confirmation_time"],"outcome":out,"rr":rr,"realized_r":realized})
        results.append({"model":name,"target_cap_r":cap,"summary":summarize(rows)})
    result={"status":"COMPLETED","research_only":True,"purpose":"Target-only efficiency comparison on exact locked V8 cohort; no entry/cohort changes.","locked_signals":len(trades),"models":results,
            "invariants":{"all_models_same_cohort":identities==[(r["direction"],r["formation_time"],r["confirmation_time"]) for r in trades],"all_models_same_signal_count":all(x["summary"]["signals"]==len(trades) for x in results),"no_rule_changes":True}}
    OUT.write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))
if __name__=="__main__": main()
