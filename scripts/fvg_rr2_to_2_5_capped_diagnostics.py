#!/usr/bin/env python3
"""FVG nearest-target diagnostic, capped to 2.5R and rescored on raw M5."""
from __future__ import annotations
import csv,io,json,urllib.request,os
from datetime import datetime,timedelta
from pathlib import Path
from fvg_only_gold_hunter_backtest import fvg_at,agg,pt,swings,equals,uniq,PAD,LOOKBACK,load

def nearest_target(d,entry,ctx):
    levels=uniq(swings(ctx,"high")+equals(ctx,"high")) if d=="BUY" else uniq(swings(ctx,"low")+equals(ctx,"low"))
    valid=[x for x in levels if x>entry] if d=="BUY" else [x for x in levels if x<entry]
    return (min(valid) if valid else None)

def score(m5,d,sl,tp,start):
    for k in range(start+1,len(m5)):
        b=m5[k]
        sh=b["low"]<=sl if d=="BUY" else b["high"]>=sl
        th=b["high"]>=tp if d=="BUY" else b["low"]<=tp
        if sh and th:return "AMBIGUOUS"
        if th:return "TP"
        if sh:return "SL"
    return "OPEN_AT_DATA_END"

def main():
    m5=load()
    start=os.getenv("FVG_ONLY_START_UTC"); end=os.getenv("FVG_ONLY_END_UTC")
    if start:m5=[x for x in m5 if pt(x["openTime"])>=pt(start)]
    if end:m5=[x for x in m5 if pt(x["openTime"])<=pt(end)]
    m15=agg(m5,15)
    setups=[]; pending=None
    for i in range(50,len(m5)):
        c=m5[i]
        if pending is not None:
            f=pending
            invalid=(f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"])
            if invalid: pending=None
            elif c["low"]<=f["mid"]<=c["high"]:
                ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if ok:
                    entry=f["mid"]; sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                    tp0=nearest_target(f["direction"],entry,f["ctx"])
                    if tp0 is not None:
                        risk=(entry-sl) if f["direction"]=="BUY" else (sl-entry)
                        rr0=((tp0-entry)/risk) if f["direction"]=="BUY" else ((entry-tp0)/risk)
                        if rr0>=2.0:
                            tp=min(tp0,entry+2.5*risk) if f["direction"]=="BUY" else max(tp0,entry-2.5*risk)
                            rr=abs(tp-entry)/risk
                            setups.append({"direction":f["direction"],"formation_time":f["time"],"confirmation_time":c["openTime"],"entry":round(entry,3),"sl":round(sl,3),"tp_original":round(tp0,3),"tp":round(tp,3),"original_rr":round(rr0,2),"capped_rr":round(rr,2),"outcome":score(m5,f["direction"],sl,tp,i)})
                    pending=None
        if pending is None:
            f=fvg_at(m5,i)
            if f:
                t=pt(f["time"]); ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
                pending={**f,"ctx":ctx}
    closed=[x for x in setups if x["outcome"] in ("TP","SL","AMBIGUOUS")]
    tp=sum(x["outcome"]=="TP" for x in closed); sl=sum(x["outcome"]=="SL" for x in closed); amb=sum(x["outcome"]=="AMBIGUOUS" for x in closed)
    net=sum(x["capped_rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in closed)
    result={"status":"COMPLETED","validation_start_utc":start,"validation_end_utc":end,"eligible_setups":len(setups),"outcomes":{"tp":tp,"sl":sl,"ambiguous":amb},"win_rate_closed":round(100*tp/(tp+sl),2) if tp+sl else None,"net_r":round(net,2),"conservative_net_r":round(net-amb,2),"avg_capped_rr":round(sum(x["capped_rr"] for x in setups)/len(setups),2) if setups else None,"original_rr_above_2_5":sum(x["original_rr"]>2.5 for x in setups),"setups":setups,"notes":["Nearest pre-confirmation M15 structural target.","Only original RR >= 2.0 is eligible.","Targets above 2.5R are capped at exactly 2.5R.","Outcomes are rescored from raw M5 candles.","Diagnostic only; no strategy parameters changed."]}
    p=Path("backtest/fvg_rr2_to_2_5_capped_diagnostics.json"); p.parent.mkdir(exist_ok=True); p.write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))

if __name__=="__main__":main()
