#!/usr/bin/env python3
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/historical_external_comparison.json").read_text(encoding="utf-8"))

def summarize(xs):
    tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
    net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
    return {"trades":len(xs),"tp":tp,"sl":sl,
            "win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
            "net_R":round(net,2),
            "avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}

def dev(xs):
    return [x for x in xs if "2026-03" <= x["candle_time"][:7] <= "2026-07"]

def evaluate(xs):
    return {
      "all":summarize(xs),
      "SELL_only":summarize([x for x in xs if x["signal"]=="SELL"]),
      "buy_side_sweep_only":summarize([x for x in xs if x["liquidity"]=="buy-side sweep"]),
      "bearish_structure_break_only":summarize([x for x in xs if x["confirmation"]=="short-term bearish structure break"]),
      "SELL_plus_buy_side_sweep":summarize([x for x in xs if x["signal"]=="SELL" and x["liquidity"]=="buy-side sweep"]),
      "SELL_plus_bearish_structure_break":summarize([x for x in xs if x["signal"]=="SELL" and x["confirmation"]=="short-term bearish structure break"]),
      "buy_side_sweep_plus_bearish_structure_break":summarize([x for x in xs if x["liquidity"]=="buy-side sweep" and x["confirmation"]=="short-term bearish structure break"]),
      "triple_filter":summarize([x for x in xs if x["signal"]=="SELL" and x["liquidity"]=="buy-side sweep" and x["confirmation"]=="short-term bearish structure break"])
    }

base_dev=dev(src["base"]["details"])
cap_dev=dev(src["target_cap_2_5"]["details"])
result={
 "period":"2026-03 through 2026-07",
 "note":"Diagnostic/control test on the development period only. No rules changed.",
 "base":evaluate(base_dev),
 "target_cap_2_5":evaluate(cap_dev)
}
out=ROOT/"backtest/hypothesis_control_development.json"
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
