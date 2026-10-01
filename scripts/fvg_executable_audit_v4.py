#!/usr/bin/env python3
"""Executable FVG Audit v4: corrected one-active-trade confirmation-candle model.

Purpose:
- Re-run the FVG confirmation-candle entry model with exact candle sequencing.
- Entry is allowed on the confirmation candle.
- Exit evaluation starts on the FIRST candle strictly after the entry candle.
- One active trade, no queue, no lookahead.
- Target context is strictly before FVG formation.
- Reconcile the resulting trades against the existing realistic RR2 result.

This is research-only.
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
SOURCE=os.getenv("COMBINED_SOURCE_URL")

def exit_one(active, c):
    sl=(c["low"]<=active["sl"]) if active["direction"]=="BUY" else (c["high"]>=active["sl"])
    tp=(c["high"]>=active["tp"]) if active["direction"]=="BUY" else (c["low"]<=active["tp"])
    if sl and tp: return "AMBIGUOUS"
    if sl: return "SL"
    if tp: return "TP"
    return None

def normalize_trade(t):
    return (
        t.get("direction"), t.get("formation_time"), t.get("confirmation_time"),
        round(float(t.get("entry")),3), round(float(t.get("sl")),3),
        round(float(t.get("tp")),3), round(float(t.get("rr")),2),
        t.get("outcome")
    )

def main():
    m5=load()
    if START: m5=[x for x in m5 if pt(x["openTime"])>=pt(START)]
    if END: m5=[x for x in m5 if pt(x["openTime"])<=pt(END)]
    m15=agg(m5,15)

    pending=[]
    active=None
    trades=[]
    confirmations=0
    missed=0
    invalidated=0
    i=50

    while i < len(m5):
        c=m5[i]

        # IMPORTANT: resolve an existing trade on THIS candle.
        # Therefore an entry created on candle i cannot be exited until i+1.
        if active is not None:
            out=exit_one(active,c)
            if out:
                active["outcome"]=out
                active["exit_time"]=c["openTime"]
                trades.append(active)
                active=None

        confirmed=[]
        still=[]
        for f in pending:
            invalid=(f["direction"]=="BUY" and c["low"]<=f["lo"]) or (f["direction"]=="SELL" and c["high"]>=f["hi"])
            if invalid:
                invalidated += 1
                continue
            if c["low"]<=f["mid"]<=c["high"]:
                ok=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (
                    f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
                if ok:
                    confirmations += 1
                    sl=f["lo"]-PAD if f["direction"]=="BUY" else f["hi"]+PAD
                    entry=c["close"]
                    tp=target(f["direction"],entry,sl,f["ctx"])
                    if tp is not None:
                        rr=(tp-entry)/(entry-sl) if f["direction"]=="BUY" else (entry-tp)/(sl-entry)
                        confirmed.append({**f,"confirmation_index":i,"confirmation_time":c["openTime"],"sl":sl,"tp":tp,"rr":rr})
                    continue
            still.append(f)
        pending=still

        # Confirmation candle can create an entry, but it CANNOT also exit it.
        if active is not None:
            missed += len(confirmed)
        elif confirmed:
            confirmed.sort(key=lambda x:(x["formed_index"],x["confirmation_index"]))
            s=confirmed[0]
            active={
                "engine":"FVG_EXECUTABLE_AUDIT_V4",
                "direction":s["direction"],
                "formation_time":s["time"],
                "confirmation_time":s["confirmation_time"],
                "entry":round(c["close"],3),
                "sl":round(s["sl"],3),
                "tp":round(s["tp"],3),
                "rr":round(s["rr"],2),
                "confirmation_index":i,
                "entry_index":i,
                "entry_time":c["openTime"],
                "zone_lo":s["lo"],
                "zone_hi":s["hi"],
            }
            missed += max(0,len(confirmed)-1)

        # New FVG becomes pending only AFTER current candle has been processed.
        f=fvg_at(m5,i)
        if f:
            t=pt(f["time"])
            ctx=[x for x in m15 if pt(x["openTime"])<=t-timedelta(minutes=15)][-LOOKBACK:]
            pending.append({**f,"ctx":ctx,"formed_index":i})
        i+=1

    if active is not None:
        active["outcome"]="OPEN_AT_DATA_END"
        active["exit_time"]=None
        trades.append(active)

    tp_n=sum(x["outcome"]=="TP" for x in trades)
    sl_n=sum(x["outcome"]=="SL" for x in trades)
    amb_n=sum(x["outcome"]=="AMBIGUOUS" for x in trades)
    open_n=sum(x["outcome"]=="OPEN_AT_DATA_END" for x in trades)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)

    # Reconcile against the realistic engine result produced earlier in this workflow.
    rp=Path("backtest/fvg_realistic_rr2_results.json")
    realistic=json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    rt=realistic.get("trades",[]) if realistic else []
    audit_norm=[normalize_trade(x) for x in trades]
    real_norm=[normalize_trade(x) for x in rt]
    common=min(len(audit_norm),len(real_norm))
    mismatch_positions=[]
    for j in range(common):
        if audit_norm[j] != real_norm[j]:
            mismatch_positions.append({
                "position":j,
                "audit_v4":audit_norm[j],
                "realistic":real_norm[j]
            })
    audit_set=set(audit_norm)
    real_set=set(real_norm)
    result={
        "status":"COMPLETED","research_only":True,"source":SOURCE,
        "validation_start_utc":START,"validation_end_utc":END,
        "rules":{
            "entry":"confirmation candle close after candle fully closes",
            "confirmation_candle_can_fill":False,
            "exit_starts":"first closed candle strictly after entry",
            "one_active_trade":True,"queue":False,"lookahead":False,
            "target_context":"M15 candles strictly before FVG formation"
        },
        "data":{
            "m5":len(m5),"m15":len(m15),
            "first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"]
        },
        "overall":{
            "signals":len(trades),"tp":tp_n,"sl":sl_n,"ambiguous":amb_n,
            "open_at_data_end":open_n,
            "win_rate":round(100*tp_n/(tp_n+sl_n),2) if tp_n+sl_n else None,
            "net_r":round(net,2),
            "conservative_net_r":round(net-amb_n,2),
            "avg_rr":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else None
        },
        "lifecycle":{
            "confirmations":confirmations,
            "missed_or_competing_opportunities":missed,
            "invalidated_pending_fvgs":invalidated,
            "remaining_pending_fvgs":len(pending)
        },
        "reconciliation":{
            "realistic_result_present":realistic is not None,
            "realistic_signals":len(rt),
            "audit_v4_signals":len(trades),
            "signal_count_delta":len(trades)-len(rt),
            "exact_trade_sequence_match":audit_norm==real_norm,
            "common_positions_compared":common,
            "mismatched_positions":len(mismatch_positions),
            "first_mismatches":mismatch_positions[:20],
            "audit_only_trades":len(audit_set-real_set),
            "realistic_only_trades":len(real_set-audit_set),
            "audit_only_examples":list(audit_set-real_set)[:10],
            "realistic_only_examples":list(real_set-audit_set)[:10]
        },
        "trades":trades
    }
    p=Path("backtest/fvg_temporal_audit_v4_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    if not SOURCE: raise RuntimeError("COMBINED_SOURCE_URL is required for executable FVG audit v4")
    main()
