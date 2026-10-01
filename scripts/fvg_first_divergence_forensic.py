#!/usr/bin/env python3
"""Forensic trace of the first Realistic-vs-Audit-V6 divergence.

Inspects the exact Realistic first trade (2026-08-03 SELL) and records whether
V6 rejected it at FVG formation, invalidation, confirmation, target eligibility,
RR eligibility, or active-trade arbitration. Also computes target/RR using both
midpoint and confirmation-close entries without changing either engine.
"""
from __future__ import annotations
import os,json,bisect
from pathlib import Path
from datetime import timedelta
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load,agg,fvg_at,target,pt,PAD,LOOKBACK

START=os.getenv("FVG_ONLY_START_UTC"); END=os.getenv("FVG_ONLY_END_UTC")
FORMATION="2026-08-03T09:25:00+00:00"
CONFIRM="2026-08-03T09:55:00+00:00"

def main():
    m5=load()
    if START:m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END:m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15); times=[pt(x["openTime"]) for x in m15]
    fi=next(i for i,x in enumerate(m5) if x["openTime"]==FORMATION)
    ci=next(i for i,x in enumerate(m5) if x["openTime"]==CONFIRM)
    f=fvg_at(m5,fi)
    if not f: raise RuntimeError("Expected FVG at first Realistic formation was not found")
    t=pt(f["time"]); cutoff=t-timedelta(minutes=15); mi=bisect.bisect_right(times,cutoff)
    ctx=m15[max(0,mi-LOOKBACK):mi]
    c=m5[ci]
    invalid=(c["high"]>=f["hi"]) if f["direction"]=="SELL" else (c["low"]<=f["lo"])
    directional=(c["close"]<f["mid"] and c["close"]<c["open"]) if f["direction"]=="SELL" else (c["close"]>f["mid"] and c["close"]>c["open"])
    mid_entry=f["mid"]; close_entry=c["close"]
    sl=f["hi"]+PAD if f["direction"]=="SELL" else f["lo"]-PAD
    mid_tp=target(f["direction"],mid_entry,sl,ctx)
    close_tp=target(f["direction"],close_entry,sl,ctx)
    def rr(entry,tp):
        if tp is None:return None
        return (entry-tp)/(sl-entry) if f["direction"]=="SELL" else (tp-entry)/(entry-sl)
    out={
      "status":"COMPLETED","formation_index":fi,"confirmation_index":ci,
      "formation_candle":m5[fi],"confirmation_candle":c,
      "fvg":{"direction":f["direction"],"time":f["time"],"lo":f["lo"],"hi":f["hi"],"mid":f["mid"]},
      "confirmation_checks":{"zone_touched":invalid,"directional_confirmation":directional,
                             "midpoint_inside":c["low"]<=f["mid"]<=c["high"]},
      "sl":sl,
      "entry_comparison":{
        "realistic_midpoint":{"entry":mid_entry,"tp":mid_tp,"rr":rr(mid_entry,mid_tp),"rr_ge_2":rr(mid_entry,mid_tp) is not None and rr(mid_entry,mid_tp)>=2},
        "audit_v6_close":{"entry":close_entry,"tp":close_tp,"rr":rr(close_entry,close_tp),"rr_ge_2":rr(close_entry,close_tp) is not None and rr(close_entry,close_tp)>=2}
      },
      "conclusion":"If both models are eligible here, the first divergence is caused by V6 pending-state/arbitration handling before or at this confirmation; if only midpoint is eligible, the entry-model change explains it."
    }
    p=Path("backtest/fvg_first_divergence_forensic.json");p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding="utf-8");print(json.dumps(out,indent=2))

if __name__=="__main__": main()
