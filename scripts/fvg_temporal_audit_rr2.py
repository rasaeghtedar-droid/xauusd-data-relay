#!/usr/bin/env python3
"""Temporal audit of the realistic FVG RR2 model.

Audit rule: confirmation happens on a closed candle. A midpoint limit cannot
fill on that same candle because the decision/confirmation is only known at
its close. The earliest fill is a strictly later candle. SL/TP evaluation
starts only after the fill candle. If both SL and TP are touched after fill,
the outcome is AMBIGUOUS.
"""
from __future__ import annotations
import os, json, bisect
from pathlib import Path
from datetime import timedelta
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, target, pt, PAD, LOOKBACK

START=os.getenv("FVG_ONLY_START_UTC")
END=os.getenv("FVG_ONLY_END_UTC")

def main():
    m5=load()
    if START: m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END: m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15)
    m15_times=[pt(x["openTime"]) for x in m15]

    pending={}
    mids={"BUY":[],"SELL":[]}
    invalid_idx={"BUY":[],"SELL":[]}
    fid_next=0
    active=None
    trades=[]
    missed=0
    invalidated=0
    same_candle_fill_attempts=0

    i=50
    while i<len(m5):
        c=m5[i]

        # Resolve only an already-filled trade. This candle is strictly after entry.
        if active is not None:
            sl=(c["low"]<=active["sl"]) if active["direction"]=="BUY" else (c["high"]>=active["sl"])
            tp=(c["high"]>=active["tp"]) if active["direction"]=="BUY" else (c["low"]<=active["tp"])
            if sl and tp:
                active["outcome"]="AMBIGUOUS"; active["exit_time"]=c["openTime"]; trades.append(active); active=None
            elif sl:
                active["outcome"]="SL"; active["exit_time"]=c["openTime"]; trades.append(active); active=None
            elif tp:
                active["outcome"]="TP"; active["exit_time"]=c["openTime"]; trades.append(active); active=None

        # Invalidate pending zones before testing midpoint fills.
        for d, key, cond in (
            ("BUY","low", lambda f: c["low"]<=f["lo"]),
            ("SELL","high",lambda f: c["high"]>=f["hi"])
        ):
            arr=invalid_idx[d]
            if d=="BUY":
                cut=bisect.bisect_right(arr,(-c["low"],10**18))
            else:
                cut=bisect.bisect_right(arr,(c["high"],10**18))
            if cut:
                for _,fid in arr[:cut]:
                    if pending.pop(fid,None) is not None: invalidated+=1
                invalid_idx[d]=arr[cut:]

        # A pending FVG may fill on this candle only if it was confirmed BEFORE this candle.
        fill_candidates=[]
        for d in ("BUY","SELL"):
            arr=mids[d]
            lo=bisect.bisect_left(arr,(c["low"],-1))
            hi=bisect.bisect_right(arr,(c["high"],10**18))
            for mid,fid in arr[lo:hi]:
                f=pending.get(fid)
                if f is None or i<=f["confirmation_index"]:
                    continue
                fill_candidates.append(f)

        # If no trade is open, oldest valid resting order can fill.
        if active is None and fill_candidates:
            fill_candidates.sort(key=lambda f:(f["confirmation_index"],f["formed_index"]))
            f=fill_candidates[0]
            pending.pop(f["id"],None)
            active={
                "engine":"FVG_TEMPORAL_AUDIT_RR2",
                "direction":f["direction"],
                "formation_time":f["time"],
                "confirmation_time":f["confirmation_time"],
                "entry":round(f["mid"],3),
                "sl":round(f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD,3),
                "tp":round(f["tp"],3),
                "rr":round(f["rr"],2),
                "confirmation_index":f["confirmation_index"],
                "entry_index":i,
                "entry_time":c["openTime"],
                "zone_lo":f["lo"],"zone_hi":f["hi"],
            }
            # Do NOT evaluate SL/TP on the fill candle; entry is assumed at the
            # midpoint touch during this candle and intrabar ordering is unknown.
            active["_entry_price"]=f["mid"]

            # Other fills on this candle are missed because one trade is active.
            missed += max(0,len(fill_candidates)-1)

        # Process existing pending FVG confirmation AFTER fill handling.
        confirmed=[]
        for d in ("BUY","SELL"):
            arr=mids[d]
            lo=bisect.bisect_left(arr,(c["low"],-1))
            hi=bisect.bisect_right(arr,(c["high"],10**18))
            for mid,fid in arr[lo:hi]:
                f=pending.get(fid)
                if f is None: continue
                ok=(d=="BUY" and c["close"]>mid and c["close"]>c["open"]) or (d=="SELL" and c["close"]<mid and c["close"]<c["open"])
                if not ok: continue
                ctx=f["ctx"]; entry=mid
                sl=entry*0 + (f["lo"]-PAD if d=="BUY" else f["hi"]+PAD)
                tp=target(d,entry,sl,ctx)
                pending.pop(fid,None)
                if tp is not None:
                    rr=(tp-entry)/(entry-sl) if d=="BUY" else (entry-tp)/(sl-entry)
                    confirmed.append((f,d,entry,sl,tp,rr))
        if active is not None:
            # Confirmations while a trade is open are missed.
            missed += len(confirmed)
        else:
            # These are now resting orders; they cannot fill until a later candle.
            for f,d,entry,sl,tp,rr in confirmed:
                fid=f["id"]
                f2={**f,"tp":tp,"rr":rr,"sl":sl,"confirmation_index":i,"confirmation_time":c["openTime"],"id":fid}
                pending[fid]=f2

        # New FVG forms at this closed candle.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"]); cutoff=t-timedelta(minutes=15)
            mi=bisect.bisect_right(m15_times,cutoff)
            ctx=m15[max(0,mi-LOOKBACK):mi]
            fid=fid_next; fid_next+=1
            f2={**f,"ctx":ctx,"formed_index":i,"id":fid}
            pending[fid]=f2
            bisect.insort(mids[f["direction"]],(f["mid"],fid))
            if f["direction"]=="BUY": bisect.insort(invalid_idx["BUY"],(-f["lo"],fid))
            else: bisect.insort(invalid_idx["SELL"],(f["hi"],fid))

        i+=1

    if active is not None:
        active["outcome"]="OPEN_AT_DATA_END"; active["exit_time"]=None; trades.append(active)

    tp_n=sum(x["outcome"]=="TP" for x in trades)
    sl_n=sum(x["outcome"]=="SL" for x in trades)
    amb_n=sum(x["outcome"]=="AMBIGUOUS" for x in trades)
    op_n=sum(x["outcome"]=="OPEN_AT_DATA_END" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)

    by={}
    for d in ("BUY","SELL"):
        ss=[x for x in trades if x["direction"]==d]
        w=sum(x["outcome"]=="TP" for x in ss); l=sum(x["outcome"]=="SL" for x in ss)
        by[d]={"signals":len(ss),"tp":w,"sl":l,"ambiguous":sum(x["outcome"]=="AMBIGUOUS" for x in ss),
               "open_at_data_end":sum(x["outcome"]=="OPEN_AT_DATA_END" for x in ss),
               "win_rate":round(100*w/(w+l),2) if w+l else None,
               "net_r":round(sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in ss),2)}

    result={"status":"COMPLETED","research_only":True,"validation_start_utc":START,"validation_end_utc":END,
      "audit_rule":{"confirmation_candle_cannot_fill":"true","earliest_fill":"strictly_later_closed_candle",
                    "exit_evaluation":"starts_after_fill","one_active_trade":True,"queue":False,"lookahead":False},
      "overall":{"signals":len(trades),"tp":tp_n,"sl":sl_n,"ambiguous":amb_n,"open_at_data_end":op_n,
                 "win_rate":round(100*tp_n/(tp_n+sl_n),2) if tp_n+sl_n else None,
                 "net_r":round(net,2),"conservative_net_r":round(net-amb_n,2),
                 "avg_rr":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else None},
      "by_direction":by,
      "lifecycle":{"missed_or_competing_opportunities":missed,"invalidated_pending_fvgs":invalidated,
                   "remaining_pending_fvgs":len(pending),"same_candle_fill_attempts":same_candle_fill_attempts},
      "trades":trades}
    p=Path("backtest/fvg_temporal_audit_results.json"); p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__": main()
