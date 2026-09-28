#!/usr/bin/env python3
import json, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE="https://biquote.io/api/XAUUSD/ohlc"
OUT=ROOT/"backtest"/"biquote_ohlc_history_diagnostic.json"

CASES=[
 {"name":"latest_500","params":{"interval":"5m","limit":500}},
 {"name":"august_500","params":{"interval":"5m","limit":500,"from":"2026-08-01T00:00:00Z","to":"2026-08-05T23:59:59Z"}},
 {"name":"sep_12_15_500","params":{"interval":"5m","limit":500,"from":"2026-09-12T00:00:00Z","to":"2026-09-15T23:59:59Z"}},
 {"name":"sep_26_28_500","params":{"interval":"5m","limit":500,"from":"2026-09-26T00:00:00Z","to":"2026-09-28T23:59:59Z"}},
]

def call(params):
    u=BASE+"?"+urllib.parse.urlencode(params)
    with urllib.request.urlopen(u,timeout=60) as r:
        return u,json.load(r)

def main():
    out={"source":BASE,"tests":[]}
    for case in CASES:
        try:
            u,d=call(case["params"])
            bars=d.get("bars",[])
            closed=[b for b in bars if not b.get("isOpen",False)]
            out["tests"].append({
                "name":case["name"],"url":u,"http_ok":True,
                "returned_bars":len(bars),"closed_bars":len(closed),
                "first":bars[-1]["openTime"] if bars else None,
                "last":bars[0]["openTime"] if bars else None,
                "symbol":d.get("symbol"),"interval":d.get("interval"),
                "first5_closed":[b["openTime"] for b in closed[-5:]],
                "last5_closed":[b["openTime"] for b in closed[:5]]
            })
        except Exception as e:
            out["tests"].append({"name":case["name"],"http_ok":False,"error":str(e)})
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
