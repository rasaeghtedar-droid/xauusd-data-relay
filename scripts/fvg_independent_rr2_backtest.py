#!/usr/bin/env python3
"""Independent FVG research backtest: every confirmed FVG with RR >= 2.0."""
from __future__ import annotations
import json, os
from pathlib import Path

from scripts.fvg_only_gold_hunter_backtest import (
    agg, fvg_at, load, outcome, pt, target
)

START=os.getenv("FVG_ONLY_START_UTC")
END=os.getenv("FVG_ONLY_END_UTC")

def main():
    m5=load()
    if START:
        m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END:
        m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15)

    # Research-only: keep every independently pending FVG.
    # This deliberately does NOT enforce the production one-open-trade rule.
    pending=[]
    signals=[]
    i=50

    while i < len(m5):
        c=m5[i]
        next_pending=[]

        for f in pending:
            if (f["direction"]=="BUY" and c["low"]<=f["lo"]) or (
                f["direction"]=="SELL" and c["high"]>=f["hi"]
            ):
                continue

            if c["low"]<=f["mid"]<=c["high"]:
                ok=(
                    f["direction"]=="BUY"
                    and c["close"]>f["mid"]
                    and c["close"]>c["open"]
                ) or (
                    f["direction"]=="SELL"
                    and c["close"]<f["mid"]
                    and c["close"]<c["open"]
                )
                if ok:
                    entry=f["mid"]
                    sl=f["lo"]-0.5 if f["direction"]=="BUY" else f["hi"]+0.5
                    tp=target(f["direction"],entry,sl,f["ctx"])
                    if tp is not None:
                        rr=(
                            (tp-entry)/(entry-sl)
                            if f["direction"]=="BUY"
                            else (entry-tp)/(sl-entry)
                        )
                        s={
                            "engine":"FVG_INDEPENDENT_RR2",
                            "direction":f["direction"],
                            "formation_time":f["time"],
                            "confirmation_time":c["openTime"],
                            "entry":round(entry,3),
                            "sl":round(sl,3),
                            "tp":round(tp,3),
                            "rr":round(rr,2),
                            "trigger":"FVG return + directional confirmation",
                            "formation_index":f["formed_index"],
                            "entry_index":i,
                        }
                        s["outcome"]=outcome(s,m5,i)
                        signals.append(s)
                    # Confirmed FVG is consumed once, whether target exists or not.
                    continue

            next_pending.append(f)

        pending=next_pending

        # A new FVG is only created after processing existing pending FVGs.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"])
            ctx=[x for x in m15 if pt(x["openTime"]) <= t.replace(tzinfo=t.tzinfo)][:0]
            # Use only M15 candles that closed before the FVG formation candle.
            from datetime import timedelta
            ctx=[x for x in m15 if pt(x["openTime"]) <= t-timedelta(minutes=15)][-30:]
            pending.append({**f,"ctx":ctx,"formed_index":i})

        i+=1

    w=sum(x["outcome"]=="TP" for x in signals)
    l=sum(x["outcome"]=="SL" for x in signals)
    a=sum(x["outcome"]=="AMBIGUOUS" for x in signals)
    o=sum(x["outcome"]=="OPEN_AT_DATA_END" for x in signals)
    net=sum(
        x["rr"] if x["outcome"]=="TP"
        else -1 if x["outcome"]=="SL"
        else 0
        for x in signals
    )

    by={}
    for d in ("BUY","SELL"):
        ss=[x for x in signals if x["direction"]==d]
        ww=sum(x["outcome"]=="TP" for x in ss)
        ll=sum(x["outcome"]=="SL" for x in ss)
        by[d]={
            "signals":len(ss),
            "tp":ww,
            "sl":ll,
            "ambiguous":sum(x["outcome"]=="AMBIGUOUS" for x in ss),
            "open_at_data_end":sum(x["outcome"]=="OPEN_AT_DATA_END" for x in ss),
            "win_rate":round(100*ww/(ww+ll),2) if ww+ll else None,
            "net_r":round(sum(
                x["rr"] if x["outcome"]=="TP"
                else -1 if x["outcome"]=="SL"
                else 0
                for x in ss
            ),2),
        }

    result={
        "status":"COMPLETED",
        "research_only":True,
        "source":os.getenv(
            "COMBINED_SOURCE_URL",
            "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
        ),
        "validation_start_utc":START,
        "validation_end_utc":END,
        "data":{
            "m5":len(m5),
            "m15":len(m15),
            "first_m5":m5[0]["openTime"],
            "last_m5":m5[-1]["openTime"],
        },
        "rule":{
            "fvg_only":True,
            "min_rr":2.0,
            "max_rr":None,
            "target":"nearest pre-confirmation M15 structural level with RR >= 2.0",
            "one_active_trade":False,
            "lookahead":False,
        },
        "overall":{
            "signals":len(signals),
            "tp":w,
            "sl":l,
            "ambiguous":a,
            "open_at_data_end":o,
            "win_rate":round(100*w/(w+l),2) if w+l else None,
            "net_r":round(net,2),
            "conservative_net_r":round(net-a,2),
            "avg_rr":round(sum(x["rr"] for x in signals)/len(signals),2) if signals else None,
        },
        "by_direction":by,
        "signals":signals,
        "notes":[
            "Research-only diagnostic to evaluate every independently confirmed FVG.",
            "Liquidity is not used as a prerequisite.",
            "Multiple overlapping FVGs are evaluated independently.",
            "The production one-open-trade rule is intentionally NOT enforced.",
            "Each FVG is consumed after confirmation and can trigger at most once.",
            "No future-candle scanning is used to select the target or trigger.",
            "Target is the nearest pre-confirmation M15 structural level whose RR is at least 2.0.",
            "There is no upper RR cap.",
            "This result must not be treated as live-strategy performance until a separate execution-policy test is approved.",
        ],
    }

    p=Path("backtest/fvg_independent_rr2_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
