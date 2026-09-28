#!/usr/bin/env python3
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
src=ROOT/"backtest/historical_trade_analysis.json"
out=ROOT/"backtest/forward_validation.json"
data=json.loads(src.read_text(encoding="utf-8"))

# Fixed split chosen before this run:
# Development window: 2026-03 through 2026-07
# Forward window:      2026-08 through 2026-09
DEV=set(["2026-03","2026-04","2026-05","2026-06","2026-07"])
OOS=set(["2026-08","2026-09"])

def summ(xs):
    tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    return {"trades":len(xs),"tp":tp,"sl":sl,
            "win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),
            "avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}

def period(xs, months): return [x for x in xs if x["candle_time"][:7] in months]

def candidate(xs):
    # Pre-specified diagnostic hypothesis from the prior analysis:
    # SELL + short-term bearish structure break.
    return [x for x in xs if x["signal"]=="SELL" and x["confirmation"]=="short-term bearish structure break"]

def cap_candidate(xs):
    # Same directional/confirmation hypothesis on the 2.5-cap engine.
    return [x for x in xs if x["signal"]=="SELL" and x["confirmation"]=="short-term bearish structure break"]

result={"split":{"development":"2026-03..2026-07","forward":"2026-08..2026-09"},
        "note":"Forward validation of fixed hypotheses; main engine is not modified.",
        "base":{}, "target_cap_2_5":{}}

for name in ("base","target_cap_2_5"):
    trades=data[name]["details"]
    dev=period(trades,DEV); oos=period(trades,OOS)
    result[name]={
      "development_all":summ(dev),
      "forward_all":summ(oos),
      "development_candidate_sell_bearish_structure":summ(candidate(dev)),
      "forward_candidate_sell_bearish_structure":summ(candidate(oos)),
    }

out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
