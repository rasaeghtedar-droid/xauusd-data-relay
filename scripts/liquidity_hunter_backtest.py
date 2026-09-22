#!/usr/bin/env python3
"""Deterministic Liquidity Hunter historical replay.

Replays closed M5 candles chronologically. At each candle it evaluates only
information available up to that candle, then records the engine decision.
This is a diagnostic backtest, not an execution simulator.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
ENGINE = ROOT / "liquidity_hunter" / "liquidity_hunter.py"
OUT = ROOT / "backtest" / "liquidity_hunter_results.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    m5 = load(HISTORY / "xauusd_5m.json")
    m15 = load(HISTORY / "xauusd_15m.json")
    h1 = load(HISTORY / "xauusd_1h.json")

    # Keep this first replay intentionally dependency-light. It validates that
    # the archived dataset is chronologically usable before deeper statistics.
    m5 = sorted([x for x in m5 if x.get("isOpen") is False],
                key=lambda x: x["openTime"])

    results = {
        "status": "DATA_READY" if m5 else "NO_DATA",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "counts": {"m5": len(m5), "m15": len(m15), "h1": len(h1)},
        "firstM5": m5[0]["openTime"] if m5 else None,
        "lastM5": m5[-1]["openTime"] if m5 else None,
        "notes": [
            "This run validates the archived closed-candle dataset.",
            "No trade performance metrics are claimed yet.",
            "Next stage can replay the Liquidity Hunter engine candle-by-candle."
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
