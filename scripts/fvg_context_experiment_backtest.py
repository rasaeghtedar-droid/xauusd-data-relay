#!/usr/bin/env python3
"""Controlled FVG + liquidity-context experiment. Isolated from main engine."""

from __future__ import annotations
import importlib.util, json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
OUT = ROOT / "backtest" / "fvg_context_experiment_results.json"
MIN_RR, MAX_RR = 2.0, 2.5
FVG_PAD = 0.5
ATR_LOOKBACK = 14
LIQUIDITY_LOOKBACK_M15 = 30
SWEEP_LOOKBACK_M5 = 6

def load(name):
    return json.loads((HISTORY / name).read_text(encoding="utf-8"))

def pt(x):
    return datetime.fromisoformat(x.replace("Z","+00:00"))

def load_engine():
    p = ROOT / "liquidity_hunter" / "liquidity_hunter.py"
    s = importlib.util.spec_from_file_location("lh", p)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def body(c): return abs(c["close"]-c["open"])
def rng(c): return c["high"]-c["low"]

def atr(m5, end_index, lookback=ATR_LOOKBACK):
    vals=[rng(x) for x in m5[max(0,end_index-lookback):end_index] if rng(x)>0]
    return sum(vals)/len(vals) if vals else None

def displacement(m5, i):
    if i < 9 or rng(m5[i-1]) <= 0: return False
    prev=[body(x) for x in m5[i-8:i-1] if rng(x)>0]
    return bool(prev) and body(m5[i-1]) >= 1.25*(sum(prev)/len(prev)) and body(m5[i-1])/rng(m5[i-1]) >= .55

def make_fvg(m5, i):
    if i < 9 or not displacement(m5,i): return None
    a,b,c=m5[i-2],m5[i-1],m5[i]
    if a["high"] < c["low"]:
        lo,hi=a["high"],c["low"]; direction="BUY"
    elif a["low"] > c["high"]:
        lo,hi=c["high"],a["low"]; direction="SELL"
    else: return None
    aatr=atr(m5,i)
    if not aatr or hi-lo < 0.10*aatr: return None
    return {"time":c["openTime"],"direction":direction,"lo":lo,"hi":hi,"mid":(lo+hi)/2,"atr":aatr}

def target_for(direction, entry, sl, ctx, engine):
    if direction=="BUY":
        levels=engine.unique_levels(engine.recent_swing_highs(ctx)+engine.equal_levels(ctx,"high"))
        cand=sorted(x for x in levels if x>entry)
        viable=[x for x in cand if MIN_RR <= (x-entry)/(entry-sl) <= MAX_RR]
        return viable[0] if viable else None
    levels=engine.unique_levels(engine.recent_swing_lows(ctx)+engine.equal_levels(ctx,"low"))
    cand=sorted((x for x in levels if x<entry),reverse=True)
    viable=[x for x in cand if MIN_RR <= (entry-x)/(sl-entry) <= MAX_RR]
    return viable[0] if viable else None

def recent_m5_sweep(m5, i, direction, engine):
    start=max(5,i-SWEEP_LOOKBACK)
    recent=m5[start:i]
    if len(recent)<3: return False
    # Use only levels formed before each candidate sweep candle.
    for j in range(start,i):
        prior=m5[max(0,j-30):j]
        highs=engine.unique_levels(engine.recent_swing_highs(prior)+engine.equal_levels(prior,"high"))
        lows=engine.unique_levels(engine.recent_swing_lows(prior)+engine.equal_levels(prior,"low"))
        c=m5[j]
        if direction=="BUY":
            for level in lows:
                if c["low"] < level and c["close"] > level and engine.sweep_quality(c,level,"BUY"):
                    return True
        else:
            for level in highs:
                if c["high"] > level and c["close"] < level and engine.sweep_quality(c,level,"SELL"):
                    return True
    return False

def near_m15_liquidity(f, m15, formation_index, engine):
    t=pt(f["time"])
    ctx=[x for x in m15 if pt(x["openTime"]) <= t-timedelta(minutes=15)][-LIQUIDITY_LOOKBACK_M15:]
    if len(ctx)<10: return False
    if f["direction"]=="BUY":
        levels=engine.unique_levels(engine.recent_swing_lows(ctx)+engine.equal_levels(ctx,"low"))
    else:
        levels=engine.unique_levels(engine.recent_swing_highs(ctx)+engine.equal_levels(ctx,"high"))
    return any(abs(f["mid"]-level) <= f["atr"] for level in levels)

def main():
    m5=sorted([x for x in load("xauusd_5m.json") if not x.get("isOpen")],key=lambda x:x["openTime"])
    m15=sorted([x for x in load("xauusd_15m.json") if not x.get("isOpen")],key=lambda x:x["openTime"])
    eng=load_engine()
    signals=[]; reasons={}; active_until=None
    formed=0; location_counts={"M15_LIQUIDITY":0,"RECENT_SWEEP":0,"BOTH":0,"NONE":0}
    for i in range(9,len(m5)):
        t=pt(m5[i]["openTime"])
        if active_until and t <= active_until: continue
        f=make_fvg(m5,i)
        if not f: continue
        formed += 1
        near=near_m15_liquidity(f,m15,i,eng)
        sweep=recent_m5_sweep(m5,i,f["direction"],eng)
        loc="BOTH" if near and sweep else "M15_LIQUIDITY" if near else "RECENT_SWEEP" if sweep else "NONE"
        location_counts[loc]+=1
        # Controlled context rule: at least one meaningful liquidity relationship.
        if loc=="NONE":
            reasons["FVG without liquidity context"]=reasons.get("FVG without liquidity context",0)+1
            continue
        for j in range(i+1,len(m5)):
            c=m5[j]
            if active_until and pt(c["openTime"])<=active_until: break
            touched=c["low"] <= f["mid"] <= c["high"]
            if not touched:
                if (f["direction"]=="BUY" and c["low"] <= f["lo"]) or (f["direction"]=="SELL" and c["high"] >= f["hi"]):
                    reasons["FVG fully filled before confirmation"]=reasons.get("FVG fully filled before confirmation",0)+1
                    break
                continue
            confirmed=(f["direction"]=="BUY" and c["close"]>f["mid"] and c["close"]>c["open"]) or (f["direction"]=="SELL" and c["close"]<f["mid"] and c["close"]<c["open"])
            if not confirmed:
                reasons["FVG retest without directional close"]=reasons.get("FVG retest without directional close",0)+1
                continue
            ctx=[x for x in m15 if pt(x["openTime"]) <= t-timedelta(minutes=15)][-30:]
            if len(ctx)<10:
                reasons["insufficient M15 context"]=reasons.get("insufficient M15 context",0)+1; break
            entry=f["mid"]; sl=(f["lo"]-FVG_PAD) if f["direction"]=="BUY" else (f["hi"]+FVG_PAD)
            tp=target_for(f["direction"],entry,sl,ctx,eng)
            if tp is None:
                reasons["no logical target with RR 2-2.5"]=reasons.get("no logical target with RR 2-2.5",0)+1; break
            rr=(tp-entry)/(entry-sl) if f["direction"]=="BUY" else (entry-tp)/(sl-entry)
            sig={"time":c["openTime"],"direction":f["direction"],"entry":round(entry,3),"sl":round(sl,3),"tp":round(tp,3),"rr":round(rr,2),"location":loc,"fvg_formed":f["time"]}
            outcome="OPEN_AT_DATA_END"; resolve=None
            for k in range(j+1,len(m5)):
                b=m5[k]
                hit_sl=b["low"]<=sl if f["direction"]=="BUY" else b["high"]>=sl
                hit_tp=b["high"]>=tp if f["direction"]=="BUY" else b["low"]<=tp
                if hit_sl and hit_tp: outcome="AMBIGUOUS_SAME_CANDLE"; resolve=k; break
                if hit_tp: outcome="TP"; resolve=k; break
                if hit_sl: outcome="SL"; resolve=k; break
            sig["outcome"]=outcome; signals.append(sig)
            if resolve is not None: active_until=pt(m5[resolve]["openTime"])
            break
    wins=sum(s["outcome"]=="TP" for s in signals); losses=sum(s["outcome"]=="SL" for s in signals); amb=sum(s["outcome"]=="AMBIGUOUS_SAME_CANDLE" for s in signals)
    net=sum((s["rr"] if s["outcome"]=="TP" else -1 if s["outcome"]=="SL" else 0) for s in signals)
    by_loc={}
    for loc in ("M15_LIQUIDITY","RECENT_SWEEP","BOTH"):
        ss=[s for s in signals if s["location"]==loc]; w=sum(s["outcome"]=="TP" for s in ss); l=sum(s["outcome"]=="SL" for s in ss)
        by_loc[loc]={"signals":len(ss),"tp":w,"sl":l,"ambiguous":sum(s["outcome"]=="AMBIGUOUS_SAME_CANDLE" for s in ss),"win_rate_determinate":round(100*w/(w+l),2) if w+l else None,"net_r":round(sum((s["rr"] if s["outcome"]=="TP" else -1 if s["outcome"]=="SL" else 0) for s in ss),2)}
    result={"status":"COMPLETED","data":{"m5":len(m5),"m15":len(m15),"first_m5":m5[0]["openTime"] if m5 else None,"last_m5":m5[-1]["openTime"] if m5 else None},"fvg_formed":formed,"location_counts":location_counts,"signals":len(signals),"tp":wins,"sl":losses,"ambiguous":amb,"win_rate_determinate":round(100*wins/(wins+losses),2) if wins+losses else None,"net_r":round(net,2),"avg_rr":round(sum(s["rr"] for s in signals)/len(signals),2) if signals else None,"by_location":by_loc,"no_trade_reasons":reasons,"signal_details":signals,"notes":["Controlled FVG + liquidity-context experiment.","At least one pre-existing M15 liquidity relationship OR recent M5 sweep required.","No changes to main Liquidity Hunter.","Research only; short sample."]}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
