#!/usr/bin/env python3
"""FVG target-band sensitivity research.
Evaluates the same confirmed FVGs under several RR acceptance bands.
No baseline strategy change.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import timedelta
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, pt, PAD, swings, equals, uniq

BANDS = [(2.0,2.5),(1.5,2.5),(2.0,3.0),(1.5,3.0),(1.0,3.0)]

def outcome(m5,direction,sl,tp,start):
    for k in range(start+1,len(m5)):
        b=m5[k]
        sl_hit=b["low"]<=sl if direction=="BUY" else b["high"]>=sl
        tp_hit=b["high"]>=tp if direction=="BUY" else b["low"]<=tp
        if sl_hit and tp_hit:return "AMBIGUOUS"
        if tp_hit:return "TP"
        if sl_hit:return "SL"
    return "OPEN_AT_DATA_END"

def main():
    m5=load(); start=os.getenv("FVG_ONLY_START_UTC"); end=os.getenv("FVG_ONLY_END_UTC")
    if start:m5=[x for x in m5 if pt(x["openTime"])>=pt(start)]
    if end:m5=[x for x in m5 if pt(x["openTime"])<=pt(end)]
    m15=agg(m5,15); confirmations=[]
    for i in range(50,len(m5)):
        f=fvg_at(m5,i)
        if not f: continue
        t=pt(f["time"]); ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-30:]
        levels=uniq(swings(ctx,"high")+equals(ctx,"high")) if f["direction"]=="BUY" else uniq(swings(ctx,"low")+equals(ctx,"low"))
        for j in range(i+1,len(m5)):
            c=m5[j]
            invalid=(f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"])
            if invalid: break
            if c["low"]<=f["mid"]<=c["high"]:
                ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if not ok: continue
                entry=f["mid"]; sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                risk=entry-sl if f["direction"]=="BUY" else sl-entry
                vals=[x for x in levels if x>entry] if f["direction"]=="BUY" else [x for x in levels if x<entry]
                candidates=[(x,((x-entry)/risk if f["direction"]=="BUY" else (entry-x)/risk)) for x in vals]
                confirmations.append({"direction":f["direction"],"time":c["openTime"],"entry":entry,"sl":sl,"start":j,"candidates":candidates})
                break
    results=[]
    for lo,hi in BANDS:
        ss=[]
        for r in confirmations:
            eligible=[x for x in r["candidates"] if lo<=x[1]<=hi]
            if not eligible: continue
            target=min(eligible,key=lambda z:z[1])
            out=outcome(m5,r["direction"],r["sl"],target[0],r["start"])
            ss.append((r,target,out))
        tp=sum(x[2]=="TP" for x in ss); sl=sum(x[2]=="SL" for x in ss); amb=sum(x[2]=="AMBIGUOUS" for x in ss)
        net=sum(x[1][1] if x[2]=="TP" else -1 if x[2]=="SL" else 0 for x in ss)
        closed=tp+sl
        results.append({"rr_min":lo,"rr_max":hi,"eligible":len(ss),"tp":tp,"sl":sl,"ambiguous":amb,"win_rate_closed":round(100*tp/closed,2) if closed else None,"net_r":round(net,2),"conservative_net_r":round(net-amb,2)})
    result={"status":"COMPLETED","confirmation_count":len(confirmations),"bands":results,"notes":["Sensitivity only; baseline remains RR 2.0-2.5.","For each band, the nearest eligible target level is selected.","No parameter or production rule has been changed."]}
    Path("backtest").mkdir(exist_ok=True); Path("backtest/fvg_target_band_sensitivity.json").write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))
if __name__=="__main__":main()
