#!/usr/bin/env python3
"""FVG-only realistic one-active-trade backtest.

A confirmed FVG is tradable only if no trade is open on that candle.
Confirmed opportunities during an open trade are missed, not queued.
Pending/unconfirmed FVGs remain alive and may confirm after the active
trade closes. No historical entry is reused after its confirmation candle.
"""
from __future__ import annotations
import os, json
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

    pending=[]
    trades=[]
    active=None
    missed_confirmations=0
    invalidated_pending=0

    i=50
    while i<len(m5):
        c=m5[i]
        had_active_at_open=active is not None

        # First resolve any already-open trade using this closed candle.
        if active is not None:
            sl=(c["low"]<=active["sl"]) if active["direction"]=="BUY" else (c["high"]>=active["sl"])
            tp=(c["high"]>=active["tp"]) if active["direction"]=="BUY" else (c["low"]<=active["tp"])
            if sl and tp:
                active["outcome"]="AMBIGUOUS"
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None
            elif sl:
                active["outcome"]="SL"
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None
            elif tp:
                active["outcome"]="TP"
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None

        # Update all still-unconfirmed FVGs.
        confirmed=[]
        new_pending=[]
        for f in pending:
            invalid=(f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"])
            if invalid:
                invalidated_pending += 1
                continue
            if c["low"]<=f["mid"]<=c["high"]:
                ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (
                    f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if ok:
                    entry=f["mid"]
                    sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                    tp=target(f["direction"],entry,sl,f["ctx"])
                    if tp is not None:
                        rr=(tp-entry)/(entry-sl) if f["direction"]=="BUY" else (entry-tp)/(sl-entry)
                        confirmed.append({
                            "engine":"FVG_REALISTIC_RR2",
                            "direction":f["direction"],
                            "formation_time":f["time"],
                            "confirmation_time":c["openTime"],
                            "entry":round(entry,3),
                            "sl":round(sl,3),
                            "tp":round(tp,3),
                            "rr":round(rr,2),
                            "zone_lo":f["lo"],
                            "zone_hi":f["hi"],
                            "confirmation_index":i,
                        })
                    continue
            new_pending.append(f)
        pending=new_pending

        # A new FVG can start its lifecycle on this closed candle.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"])
            ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
            pending.append({**f,"ctx":ctx})

        # Crucial live-parity rule: if a trade was open at the START of this
        # candle, all confirmations on this candle are missed. They are never
        # queued and their old entry is never reused.
        if had_active_at_open:
            missed_confirmations += len(confirmed)
            i += 1
            continue

        # No active trade at candle start. At most one newly confirmed FVG
        # can become a trade; choose oldest formation deterministically.
        if active is None and confirmed:
            confirmed.sort(key=lambda x:(x["confirmation_time"], x["formation_time"]))
            s=confirmed[0]
            s["entry_index"]=i
            s["entry_time"]=c["openTime"]
            active=s

            # If entry and exit are both touched on the same closed candle,
            # use conservative OHLC handling.
            sl=(c["low"]<=s["sl"]) if s["direction"]=="BUY" else (c["high"]>=s["sl"])
            tp=(c["high"]>=s["tp"]) if s["direction"]=="BUY" else (c["low"]<=s["tp"])
            if sl and tp:
                s["outcome"]="AMBIGUOUS"; s["exit_time"]=c["openTime"]
                trades.append(s); active=None
            elif sl:
                s["outcome"]="SL"; s["exit_time"]=c["openTime"]
                trades.append(s); active=None
            elif tp:
                s["outcome"]="TP"; s["exit_time"]=c["openTime"]
                trades.append(s); active=None

            # Other confirmations on the same candle are also missed.
            missed_confirmations += max(0,len(confirmed)-1)

    if active is not None:
        active["outcome"]="OPEN_AT_DATA_END"
        active["exit_time"]=None
        trades.append(active)

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
        "status":"COMPLETED",
        "research_only":True,
        "source":os.getenv("COMBINED_SOURCE_URL"),
        "validation_start_utc":START,
        "validation_end_utc":END,
        "rule":{
            "fvg_only":True,"min_rr":2.0,"max_rr":None,
            "one_active_trade":True,
            "queue":False,
            "confirmed_while_active":"MISSED",
            "pending_fvg_survives_active_trade":True,
            "entry_model":"confirmed FVG midpoint at confirmation candle close",
            "lookahead":False,
            "same_candle":"SL if entry+SL; TP if entry+TP; AMBIGUOUS if entry+SL+TP",
        },
        "overall":{
            "signals":len(trades),"tp":tp_n,"sl":sl_n,"ambiguous":amb_n,
            "open_at_data_end":open_n,
            "win_rate":round(100*tp_n/(tp_n+sl_n),2) if tp_n+sl_n else None,
            "net_r":round(net,2),
            "conservative_net_r":round(net-amb_n,2),
            "avg_rr":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else None,
        },
        "by_direction":by,
        "lifecycle":{
            "missed_confirmations_while_active":missed_confirmations,
            "invalidated_pending_fvgs":invalidated_pending,
            "remaining_pending_fvgs":len(pending),
        },
        "trades":trades,
        "notes":[
            "Research-only; no production trading changes.",
            "Liquidity is not used.",
            "Only one active trade is allowed.",
            "Confirmed setups during an active trade are missed, never queued and never retroactively entered.",
            "Unconfirmed FVGs remain pending through an active trade and can confirm after it closes.",
            "No future-candle scanning/lookahead.",
            "RR >= 2.0 with no upper RR cap.",
        ],
    }
    p=Path("backtest/fvg_realistic_rr2_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
