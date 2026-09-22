#!/usr/bin/env python3
"""Archive closed XAUUSD candles from Biquote into history JSON files.

This collector is additive: it never overwrites existing historical candles.
It is intended to build a real forward-growing dataset for Liquidity Hunter
backtests. Biquote's public OHLC endpoint is used; no API key is required.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
SYMBOL = "XAUUSD"
BASE = "https://biquote.io/api/XAUUSD/ohlc"
INTERVALS = {"5m": 1000, "15m": 1000, "1h": 1000}


def fetch(interval: str, limit: int) -> list[dict]:
    query = urllib.parse.urlencode({"interval": interval, "limit": limit})
    req = urllib.request.Request(
        f"{BASE}?{query}",
        headers={"User-Agent": "xauusd-data-relay-history/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    return payload.get("bars", [])


def merge(interval: str, bars: list[dict]) -> int:
    HISTORY.mkdir(parents=True, exist_ok=True)
    path = HISTORY / f"xauusd_{interval}.json"

    existing = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))

    by_time = {
        str(b["openTime"]): b
        for b in existing
        if b.get("isOpen") is False and b.get("openTime")
    }

    for bar in bars:
        if bar.get("isOpen") is False and bar.get("openTime"):
            by_time[str(bar["openTime"])] = bar

    merged = [by_time[k] for k in sorted(by_time)]
    path.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(merged)


def main() -> None:
    counts = {}
    for interval, limit in INTERVALS.items():
        counts[interval] = merge(interval, fetch(interval, limit))

    print("Archived closed XAUUSD candles:", counts)


if __name__ == "__main__":
    main()
