#!/usr/bin/env python3
"""Optimized realistic FVG-only RR2 backtest.

Rules:
- FVG only; no liquidity prerequisite.
- One active trade.
- Confirmed FVG while a trade is open is missed, never queued.
- Unconfirmed FVGs remain alive until invalidation or confirmation.
- Entry is the FVG midpoint on the confirmation candle.
- RR >= 2, no upper RR cap.
- No lookahead.
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

    # Precompute time -> M15 index. This replaces a full M15 scan for every FVG.
    m15_times=[pt(x["openTime"]) for x in m15]
    pending={}
    mid_index={"BUY":[], "SELL":[]}  # sorted (mid, id)
    buy_invalid_index=[]  # sorted (-lo, id): BUY invalidates when candle low <= lo
    sell_invalid_index=[]  # sorted (hi, id): SELL invalidates when candle high >= hi
    next_id=0

    trades=[]
    active=None
    missed_confirmations=0
    invalidated_pending=0

    i=50
    while i < len(m5):
        c=m5[i]
        had_active=active is not None

        # Resolve active trade using this closed candle only.
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

        # Invalidate pending FVGs first using indexed zone boundaries.
        buy_cut=bisect.bisect_right(buy_invalid_index,(-c["low"],10**18))
        for neg_lo,fid in buy_invalid_index[:buy_cut]:
            f=pending.pop(fid,None)
            if f is not None:
                invalidated_pending += 1
        if buy_cut:
            buy_invalid_index=buy_invalid_index[buy_cut:]

        sell_cut=bisect.bisect_right(sell_invalid_index,(c["high"],10**18))
        for hi,fid in sell_invalid_index[:sell_cut]:
            f=pending.pop(fid,None)
            if f is not None:
                invalidated_pending += 1
        if sell_cut:
            sell_invalid_index=sell_invalid_index[sell_cut:]

        # Only inspect pending FVGs whose midpoint can actually be inside this candle.
        for d in ("BUY","SELL"):
            arr=mid_index[d]
            lo_pos=bisect.bisect_left(arr,(c["low"],-1))
            hi_pos=bisect.bisect_right(arr,(c["high"],10**18))
            for mid,fid in arr[lo_pos:hi_pos]:
                f=pending.get(fid)
                if f is None: continue
                # Invalidation has priority over confirmation.
                invalid=(d=="BUY" and c["low"]<=f["lo"]) or (d=="SELL" and c["high"]>=f["hi"])
                if invalid:
                    pending.pop(fid,None)
                    invalidated_pending += 1
                    continue
                ok=(d=="BUY" and c["close"]>mid and c["close"]>c["open"]) or (
                    d=="SELL" and c["close"]<mid and c["close"]<c["open"])
                if not ok: continue

                entry=mid
                sl=f["lo"]-PAD if d=="BUY" else f["hi"]+PAD
                tp=target(d,entry,sl,f["ctx"])
                pending.pop(fid,None)
                if tp is not None:
                    rr=(tp-entry)/(entry-sl) if d=="BUY" else (entry-tp)/(sl-entry)
                    confirmed.append({
                        "engine":"FVG_REALISTIC_RR2",
                        "direction":d,
                        "formation_time":f["time"],
                        "confirmation_time":c["openTime"],
                        "entry":round(entry,3),
                        "sl":round(sl,3),
                        "tp":round(tp,3),
                        "rr":round(rr,2),
                        "zone_lo":f["lo"],
                        "zone_hi":f["hi"],
                        "confirmation_index":i,
                        "_formation_index":f["formed_index"],
                    })

        # Detect a newly formed FVG after processing existing ones.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"])
            # M15 context index is the latest M15 candle strictly before formation-15m.
            cutoff=t-timedelta(minutes=15)
            mi=bisect.bisect_right(m15_times,cutoff)
            ctx=m15[max(0,mi-LOOKBACK):mi]
            fid=next_id; next_id+=1
            pending[fid]={**f,"ctx":ctx,"formed_index":i}
            bisect.insort(mid_index[f["direction"]],(f["mid"],fid))
            if f["direction"]=="BUY":
                bisect.insort(buy_invalid_index,(-f["lo"],fid))
            else:
                bisect.insort(sell_invalid_index,(f["hi"],fid))

        # Confirmations occurring while a trade was already open are missed.
        if had_active:
            missed_confirmations += len(confirmed)
            i += 1
            continue

        # If free, take the oldest confirmed setup from this candle.
        if active is None and confirmed:
            confirmed.sort(key=lambda x:(x["_formation_index"],x["confirmation_time"]))
            s=confirmed[0]
            s["entry_index"]=i
            s["entry_time"]=c["openTime"]
            s.pop("_formation_index",None)

            # The confirmation/entry candle cannot also resolve the trade.
            # Exit evaluation starts on the first strictly later closed M5 candle.
            active=s
            missed_confirmations += max(0,len(confirmed)-1)

        i += 1

    if active is not None:
        active["outcome"]="OPEN_AT_DATA_END"; active["exit_time"]=None; trades.append(active)

    tp_n=sum(x["outcome"]=="TP" for x in trades)
    sl_n=sum(x["outcome"]=="SL" for x in trades)
    amb_n=sum(x["outcome"]=="AMBIGUOUS" for x in trades)
    open_n=sum(x["outcome"]=="OPEN_AT_DATA_END" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)

    by={}
    for d in ("BUY","SELL"):
        ss=[x for x in trades if x["direction"]==d]
        w=sum(x["outcome"]=="TP" for x in ss); l=sum(x["outcome"]=="SL" for x in ss)
        by[d]={
            "signals":len(ss),"tp":w,"sl":l,
            "ambiguous":sum(x["outcome"]=="AMBIGUOUS" for x in ss),
            "open_at_data_end":sum(x["outcome"]=="OPEN_AT_DATA_END" for x in ss),
            "win_rate":round(100*w/(w+l),2) if w+l else None,
            "net_r":round(sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in ss),2),
        }

    result={
        "status":"COMPLETED","research_only":True,
        "source":os.getenv("COMBINED_SOURCE_URL"),
        "validation_start_utc":START,"validation_end_utc":END,
        "rule":{
            "fvg_only":True,"min_rr":2.0,"max_rr":None,
            "one_active_trade":True,"queue":False,
            "confirmed_while_active":"MISSED",
            "pending_fvg_survives_active_trade":True,
            "entry_model":"confirmed FVG midpoint on confirmation candle",
            "lookahead":False,
            "same_candle_exit":"PROHIBITED; exit evaluation starts on the first closed M5 candle strictly after entry"
        },
        "overall":{
            "signals":len(trades),"tp":tp_n,"sl":sl_n,"ambiguous":amb_n,
            "open_at_data_end":open_n,
            "win_rate":round(100*tp_n/(tp_n+sl_n),2) if tp_n+sl_n else None,
            "net_r":round(net,2),"conservative_net_r":round(net-amb_n,2),
            "avg_rr":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else None
        },
        "by_direction":by,
        "lifecycle":{
            "missed_confirmations_while_active":missed_confirmations,
            "invalidated_pending_fvgs":invalidated_pending,
            "remaining_pending_fvgs":len(pending)
        },
        "trades":trades,
        "notes":[
            "Research-only; no production trading changes.",
            "Optimized pending-FVG lookup using midpoint index and precomputed M15 time index.",
            "Liquidity is not used.",
            "Confirmed setups during an active trade are missed, never queued or retroactively entered.",
            "Unconfirmed FVGs remain pending through an active trade.",
            "No future-candle scanning/lookahead.",
            "RR >= 2.0 with no upper RR cap."
        ]
    }
    p=Path("backtest/fvg_realistic_rr2_results.json"); p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__": main()
