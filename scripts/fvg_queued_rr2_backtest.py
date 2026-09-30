#!/usr/bin/env python3
"""FVG-only queued-limit backtest: one active trade, but valid FVG opportunities are queued."""
from __future__ import annotations
import os, sys, json
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, target, pt, PAD, LOOKBACK

START=os.getenv("FVG_ONLY_START_UTC")
END=os.getenv("FVG_ONLY_END_UTC")

def main():
    m5=load()
    if START: m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END: m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15)

    pending_fvgs=[]
    orders=[]
    trades=[]
    active=None
    i=50

    while i < len(m5):
        c=m5[i]
        now=pt(c["openTime"])

        # 1) Resolve the currently active trade using only this closed candle.
        if active is not None:
            sl_hit=(c["low"]<=active["sl"]) if active["direction"]=="BUY" else (c["high"]>=active["sl"])
            tp_hit=(c["high"]>=active["tp"]) if active["direction"]=="BUY" else (c["low"]<=active["tp"])
            if sl_hit and tp_hit:
                active["outcome"]="AMBIGUOUS"
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None
            elif tp_hit:
                active["outcome"]="TP"
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None
            elif sl_hit:
                active["outcome"]="SL"
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None

        # 2) Update FVG lifecycle. Confirmed FVGs become resting limit orders.
        new_pending=[]
        for f in pending_fvgs:
            invalid=(f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"])
            if invalid:
                continue
            if c["low"]<=f["mid"]<=c["high"]:
                ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (
                    f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if ok:
                    entry=f["mid"]
                    sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                    tp=target(f["direction"],entry,sl,f["ctx"])
                    if tp is not None:
                        rr=((tp-entry)/(entry-sl) if f["direction"]=="BUY" else (entry-tp)/(sl-entry))
                        orders.append({
                            "engine":"FVG_QUEUED_RR2",
                            "direction":f["direction"],
                            "formation_time":f["time"],
                            "confirmation_time":c["openTime"],
                            "entry":round(entry,3),
                            "sl":round(sl,3),
                            "tp":round(tp,3),
                            "rr":round(rr,2),
                            "zone_lo":f["lo"],
                            "zone_hi":f["hi"],
                            "order_created_index":i,
                            "status":"QUEUED",
                        })
                    continue
            new_pending.append(f)
        pending_fvgs=new_pending

        # 3) New FVG is created after existing lifecycle processing.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"])
            ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
            pending_fvgs.append({**f,"ctx":ctx})

        # 4) If no active trade, fill the oldest valid queued limit order touched by this candle.
        #    The original one-trade rule is respected; queued opportunities are not discarded
        #    merely because another trade was active.
        if active is None and orders:
            orders.sort(key=lambda x:(x["order_created_index"], x["confirmation_time"]))
            chosen=None
            for o in orders:
                touches_entry=(i>o["order_created_index"] and c["low"]<=o["entry"]<=c["high"])
                invalid=(o["direction"]=="BUY" and c["low"]<=o["zone_lo"]) or (o["direction"]=="SELL" and c["high"]>=o["zone_hi"])
                if touches_entry:
                    chosen=o
                    chosen["_fill_candle_invalidates"]=invalid
                    break
                if invalid:
                    o["status"]="CANCELLED_INVALIDATION"
            orders=[o for o in orders if o.get("status")=="QUEUED"]
            if chosen is not None:
                chosen["status"]="FILLED"
                chosen["entry_index"]=i
                chosen["fill_time"]=c["openTime"]
                if chosen.pop("_fill_candle_invalidates",False):
                    # OHLC cannot establish intrabar order; mark conservatively ambiguous.
                    chosen["outcome"]="AMBIGUOUS"
                    chosen["exit_time"]=c["openTime"]
                    trades.append(chosen)
                    active=None
                else:
                    active=chosen

        i+=1

    # Any still-open trade at data end is not counted as TP/SL.
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
            "opportunity_queue":True,
            "entry_model":"confirmed FVG creates a resting midpoint limit order; order may fill on a later closed candle if price retests midpoint before zone invalidation",
            "queue_priority":"oldest confirmed order first",
            "lookahead":False,
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
        "queue_end":{"remaining_orders":len(orders),"remaining_pending_fvgs":len(pending_fvgs)},
        "trades":trades,
        "notes":[
            "Research-only; live execution is unchanged.",
            "Liquidity is not used as a prerequisite.",
            "Every confirmed FVG with a structural target RR >= 2.0 can create a queued midpoint limit order.",
            "Only one trade may be active at a time.",
            "Queued opportunities are not discarded merely because another trade is active.",
            "Orders are cancelled if price invalidates the FVG zone before fill.",
            "No upper RR cap.",
            "No future-candle scanning/lookahead.",
            "If a fill candle also reaches the invalidation/stop boundary, outcome is AMBIGUOUS because OHLC cannot establish intrabar order.",
            "This is a research execution-policy test, not yet a production promotion.",
        ],
    }
    p=Path("backtest/fvg_queued_rr2_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
