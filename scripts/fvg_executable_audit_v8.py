#!/usr/bin/env python3
"""Executable FVG audit v8.

Strict state-machine audit:
- FVG -> midpoint return + directional confirmation on a closed M5 candle.
- Entry is the FVG midpoint on the confirmation candle.
- SL is FVG boundary +/- PAD.
- TP is selected from M15 context strictly before FVG formation, using the actual entry.
- RR is recomputed from the actual entry and must be >= 2.
- One active trade; confirmations while active are missed.
- No same-candle exit; exits start on the next closed M5 candle.
- No future-candle scanning/lookahead.
"""
from __future__ import annotations
import os,json,bisect
from pathlib import Path
from datetime import timedelta
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load,agg,fvg_at,target,pt,PAD,LOOKBACK

START=os.getenv("FVG_ONLY_START_UTC"); END=os.getenv("FVG_ONLY_END_UTC"); SOURCE=os.getenv("COMBINED_SOURCE_URL")

def main():
    m5=load()
    if START:m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END:m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15); m15_times=[pt(x["openTime"]) for x in m15]

    pending={}; mid_index={"BUY":[],"SELL":[]}; invalid_index={"BUY":[],"SELL":[]}
    next_id=0; active=None; trades=[]; confirmations=0; missed=0; invalidated=0
    i=50
    while i<len(m5):
        c=m5[i]
        had_active=active is not None

        # Resolve only the currently closed candle. Never scan ahead.
        if active is not None:
            sl=(c["low"]<=active["sl"]) if active["direction"]=="BUY" else (c["high"]>=active["sl"])
            tp=(c["high"]>=active["tp"]) if active["direction"]=="BUY" else (c["low"]<=active["tp"])
            if sl and tp:
                active["outcome"]="AMBIGUOUS"; active["exit_time"]=c["openTime"]; trades.append(active); active=None
            elif sl:
                active["outcome"]="SL"; active["exit_time"]=c["openTime"]; trades.append(active); active=None
            elif tp:
                active["outcome"]="TP"; active["exit_time"]=c["openTime"]; trades.append(active); active=None

        confirmed=[]

        # Invalidate before confirmation.
        for d in ("BUY","SELL"):
            arr=invalid_index[d]
            if d=="BUY":
                cut=bisect.bisect_right(arr,(-c["low"],10**18))
            else:
                cut=bisect.bisect_right(arr,(c["high"],10**18))
            for _,fid in arr[:cut]:
                if pending.pop(fid,None) is not None: invalidated+=1
            invalid_index[d]=arr[cut:]

        # Confirm only pending FVGs whose midpoint is inside this candle.
        for d in ("BUY","SELL"):
            arr=mid_index[d]
            lo=bisect.bisect_left(arr,(c["low"],-1)); hi=bisect.bisect_right(arr,(c["high"],10**18))
            for _,fid in arr[lo:hi]:
                f=pending.get(fid)
                if f is None: continue
                if (d=="BUY" and c["low"]<=f["lo"]) or (d=="SELL" and c["high"]>=f["hi"]):
                    pending.pop(fid,None); invalidated+=1; continue
                ok=(d=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (d=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if not ok: continue

                confirmations+=1
                entry=f["mid"]
                sl=f["lo"]-PAD if d=="BUY" else f["hi"]+PAD
                tp=target(d,entry,sl,f["ctx"])
                pending.pop(fid,None)
                if tp is None: continue
                # Use raw execution prices for target/RR; round only for persisted display.
                rr_raw=(tp-entry)/(entry-sl) if d=="BUY" else (entry-tp)/(sl-entry)
                if rr_raw < 2.0: continue
                entry_r=round(entry,3); sl_r=round(sl,3); tp_r=round(tp,3); rr=round(rr_raw,2)
                confirmed.append({**f,"confirmation_index":i,"confirmation_time":c["openTime"],"entry":entry_r,"sl":sl_r,"tp":tp_r,"rr":rr})

        # A setup confirmed while a trade was active is missed, even if that trade just exited.
        if had_active:
            missed+=len(confirmed)
        elif active is None and confirmed:
            confirmed.sort(key=lambda x:(x["formed_index"],x["confirmation_index"]))
            s=confirmed[0]
            active={"engine":"FVG_EXECUTABLE_AUDIT_V8","direction":s["direction"],
                    "formation_time":s["time"],"confirmation_time":s["confirmation_time"],
                    "entry":s["entry"],"sl":s["sl"],"tp":s["tp"],
                    "rr":round(s["rr"],2),"confirmation_index":i,"entry_index":i,
                    "entry_time":c["openTime"],"zone_lo":s["lo"],"zone_hi":s["hi"]}
            missed+=max(0,len(confirmed)-1)

        # Detect new FVG only after processing the current candle.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"]); cutoff=t-timedelta(minutes=15)
            mi=bisect.bisect_right(m15_times,cutoff)
            ctx=m15[max(0,mi-LOOKBACK):mi]
            fid=next_id; next_id+=1
            pending[fid]={**f,"ctx":ctx,"formed_index":i}
            bisect.insort(mid_index[f["direction"]],(f["mid"],fid))
            if f["direction"]=="BUY": bisect.insort(invalid_index["BUY"],(-f["lo"],fid))
            else: bisect.insort(invalid_index["SELL"],(f["hi"],fid))
        i+=1

    if active is not None:
        active["outcome"]="OPEN_AT_DATA_END"; active["exit_time"]=None; trades.append(active)

    # Invariants before publishing.
    errors=[]; prev_exit=None
    for n,t in enumerate(trades):
        d=t["direction"]; e=t["entry"]; s=t["sl"]; q=t["tp"]
        rr=(q-e)/(e-s) if d=="BUY" else (e-q)/(s-e)
        if abs(round(rr,2)-t["rr"])>0.005: errors.append(f"RR_MISMATCH:{n}")
        if rr<2: errors.append(f"RR_LT_2:{n}")
        if t["confirmation_time"]!=t["entry_time"]: errors.append(f"ENTRY_TIME_MISMATCH:{n}")
        if t["outcome"]!="OPEN_AT_DATA_END" and pt(t["exit_time"])<=pt(t["entry_time"]): errors.append(f"EXIT_NOT_LATER:{n}")
        if prev_exit is not None and pt(t["entry_time"])<=prev_exit: errors.append(f"OVERLAP:{n}")
        prev_exit=pt(t["exit_time"]) if t["outcome"]!="OPEN_AT_DATA_END" else prev_exit

    tp_n=sum(x["outcome"]=="TP" for x in trades); sl_n=sum(x["outcome"]=="SL" for x in trades); amb=sum(x["outcome"]=="AMBIGUOUS" for x in trades); op=sum(x["outcome"]=="OPEN_AT_DATA_END" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)
    result={"status":"COMPLETED" if not errors else "FAILED_INVARIANTS","research_only":True,"source":SOURCE,
      "validation_start_utc":START,"validation_end_utc":END,
      "rules":{"entry":"FVG midpoint on confirmation candle","confirmation_candle_can_fill":False,"exit_starts":"first closed candle strictly after entry","one_active_trade":True,"queue":False,"lookahead":False,"target_context":"M15 candles strictly before FVG formation"},
      "data":{"m5":len(m5),"m15":len(m15),"first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"]},
      "overall":{"signals":len(trades),"tp":tp_n,"sl":sl_n,"ambiguous":amb,"open_at_data_end":op,"win_rate":round(100*tp_n/(tp_n+sl_n),2) if tp_n+sl_n else None,"net_r":round(net,2),"conservative_net_r":round(net-amb,2),"avg_rr":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else None},
      "lifecycle":{"confirmations":confirmations,"missed_or_competing_opportunities":missed,"invalidated_pending_fvgs":invalidated,"remaining_pending_fvgs":len(pending)},
      "invariants":{"status":"PASS" if not errors else "FAIL","errors":errors,"rr_consistent":not any(x.startswith("RR_") for x in errors),"no_overlap":not any(x.startswith("OVERLAP") for x in errors)},
      "trades":trades}
    p=Path("backtest/fvg_executable_audit_v8_results.json"); p.parent.mkdir(exist_ok=True); p.write_text(json.dumps(result,indent=2),encoding="utf-8"); print(json.dumps(result,indent=2))
    if errors: raise SystemExit(1)

if __name__=="__main__":
    if not SOURCE: raise RuntimeError("COMBINED_SOURCE_URL is required for executable FVG audit v8")
    main()
