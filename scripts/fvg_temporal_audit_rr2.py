#!/usr/bin/env python3
"""Temporal audit using a simple stateful pending-FVG list.

This intentionally favors correctness/readability over micro-optimization.
It preserves the known FVG lifecycle: form -> wait for midpoint return and
directional confirmation -> resting midpoint order -> later fill -> exits
evaluated only after the fill candle. One active trade, no queue.
"""
from __future__ import annotations
import os,json
from pathlib import Path
from datetime import timedelta
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load,agg,fvg_at,target,pt,PAD,LOOKBACK

START=os.getenv("FVG_ONLY_START_UTC")
END=os.getenv("FVG_ONLY_END_UTC")

def main():
    m5=load()
    if START:m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END:m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15)

    pending=[]
    active=None
    trades=[]
    missed=0
    invalidated=0
    confirmations=0
    i=50

    while i<len(m5):
        c=m5[i]

        # Exit only a trade that was already filled before this candle.
        if active is not None:
            sl=(c["low"]<=active["sl"]) if active["direction"]=="BUY" else (c["high"]>=active["sl"])
            tp=(c["high"]>=active["tp"]) if active["direction"]=="BUY" else (c["low"]<=active["tp"])
            if sl and tp:
                active["outcome"]="AMBIGUOUS";active["exit_time"]=c["openTime"];trades.append(active);active=None
            elif sl:
                active["outcome"]="SL";active["exit_time"]=c["openTime"];trades.append(active);active=None
            elif tp:
                active["outcome"]="TP";active["exit_time"]=c["openTime"];trades.append(active);active=None

        # 1) Existing pending FVGs: invalidate OR confirm.
        confirmed=[]
        still=[]
        for f in pending:
            if (f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"]):
                invalidated+=1
                continue
            if c["low"]<=f["mid"]<=c["high"]:
                ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if ok:
                    confirmations+=1
                    entry=f["mid"]
                    sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                    tp=target(f["direction"],entry,sl,f["ctx"])
                    if tp is not None:
                        rr=(tp-entry)/(entry-sl) if f["direction"]=="BUY" else (entry-tp)/(sl-entry)
                        confirmed.append({**f,"confirmation_index":i,"confirmation_time":c["openTime"],"sl":sl,"tp":tp,"rr":rr})
                    continue
            still.append(f)
        pending=still

        # 2) If a trade is already open at the start of this candle,
        # confirmations are missed. Their old entry is never reused.
        # (active may have just closed above, so we need the start-state flag.)
        # We reconstruct it from entry index.
        # To avoid ambiguity, use a dedicated flag stored before exit.
        # This branch is handled below by checking whether the active trade
        # existed at the start of the candle.
        # NOTE: start_active is initialized at top of loop on first pass below.
        if 'start_active' in locals() and start_active:
            missed+=len(confirmed)
        elif active is None and confirmed:
            confirmed.sort(key=lambda x:(x["confirmation_index"],x["formed_index"]))
            s=confirmed[0]
            # Create a resting order. It may NOT fill on confirmation candle.
            s["status"]="QUEUED_RESTING"
            pending_orders=[s]
            # Keep other confirmed orders as missed; no queue.
            missed+=max(0,len(confirmed)-1)
            # Fill the oldest resting order only on a strictly later candle.
            # Store it separately for the next loop.
            resting=pending_orders[0]
        else:
            # If active exists, confirmed opportunities are missed.
            missed+=len(confirmed)

        # Fill a resting order only on a later candle, then evaluate exits
        # starting from the following candle.
        if 'resting' in locals() and resting is not None and active is None and i>resting["confirmation_index"]:
            active={
                "engine":"FVG_TEMPORAL_AUDIT_RR2",
                "direction":resting["direction"],
                "formation_time":resting["time"],
                "confirmation_time":resting["confirmation_time"],
                "entry":round(resting["mid"],3),
                "sl":round(resting["sl"],3),
                "tp":round(resting["tp"],3),
                "rr":round(resting["rr"],2),
                "confirmation_index":resting["confirmation_index"],
                "entry_index":i,
                "entry_time":c["openTime"],
                "zone_lo":resting["lo"],
                "zone_hi":resting["hi"],
            }
            resting=None

        # New FVG forms at the end of this closed candle.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"])
            ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
            pending.append({**f,"ctx":ctx,"formed_index":i})

        start_active=active is not None
        i+=1

    if active is not None:
        active["outcome"]="OPEN_AT_DATA_END";active["exit_time"]=None;trades.append(active)

    tp_n=sum(x["outcome"]=="TP" for x in trades);sl_n=sum(x["outcome"]=="SL" for x in trades)
    amb_n=sum(x["outcome"]=="AMBIGUOUS" for x in trades);open_n=sum(x["outcome"]=="OPEN_AT_DATA_END" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)
    by={}
    for d in ("BUY","SELL"):
        ss=[x for x in trades if x["direction"]==d];w=sum(x["outcome"]=="TP" for x in ss);l=sum(x["outcome"]=="SL" for x in ss)
        by[d]={"signals":len(ss),"tp":w,"sl":l,"ambiguous":sum(x["outcome"]=="AMBIGUOUS" for x in ss),"open_at_data_end":sum(x["outcome"]=="OPEN_AT_DATA_END" for x in ss),"win_rate":round(100*w/(w+l),2) if w+l else None,"net_r":round(sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in ss),2)}
    result={"status":"COMPLETED","research_only":True,"validation_start_utc":START,"validation_end_utc":END,
      "audit_rule":{"confirmation_candle_cannot_fill":True,"earliest_fill":"strictly_later_closed_candle","exit_evaluation":"starts_after_fill","one_active_trade":True,"queue":False,"lookahead":False},
      "overall":{"signals":len(trades),"tp":tp_n,"sl":sl_n,"ambiguous":amb_n,"open_at_data_end":open_n,"win_rate":round(100*tp_n/(tp_n+sl_n),2) if tp_n+sl_n else None,"net_r":round(net,2),"conservative_net_r":round(net-amb_n,2),"avg_rr":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else None},
      "by_direction":by,
      "lifecycle":{"confirmations":confirmations,"missed_or_competing_opportunities":missed,"invalidated_pending_fvgs":invalidated,"remaining_pending_fvgs":len(pending)},
      "trades":trades}
    p=Path("backtest/fvg_temporal_audit_results.json");p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(result,indent=2),encoding="utf-8");print(json.dumps(result,indent=2))

if __name__=="__main__":main()
