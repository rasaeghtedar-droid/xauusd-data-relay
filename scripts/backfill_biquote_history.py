#!/usr/bin/env python3
"""Backfill XAUUSD OHLC history using Biquote date-range support."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
BASE = "https://biquote.io/api/XAUUSD/ohlc"

# Fast, bounded chunks stay below Biquote's 1000-bar request cap.
CHUNKS = {"5m": timedelta(days=3), "15m": timedelta(days=10), "1h": timedelta(days=35)}
START = datetime.now(timezone.utc) - timedelta(days=35)
END = datetime.now(timezone.utc)


def fetch(interval: str, start: datetime, end: datetime) -> list[dict]:
    q = urllib.parse.urlencode({
        "interval": interval,
        "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 1000,
    })
    req = urllib.request.Request(f"{BASE}?{q}", headers={"User-Agent": "xauusd-history-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response).get("bars", [])


def merge(interval: str, bars: list[dict]) -> int:
    HISTORY.mkdir(parents=True, exist_ok=True)
    path = HISTORY / f"xauusd_{interval}.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    by_time = {
        str(b["openTime"]): b for b in existing
        if b.get("isOpen") is False and b.get("openTime")
    }
    for b in bars:
        if b.get("isOpen") is False and b.get("openTime"):
            by_time[str(b["openTime"])] = b
    merged = [by_time[k] for k in sorted(by_time)]
    path.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(merged)


def main():
    counts = {}
    for interval, chunk in CHUNKS.items():
        cursor = START
        collected = []
        while cursor < END:
            nxt = min(cursor + chunk, END)
            collected.extend(fetch(interval, cursor, nxt))
            cursor = nxt
        counts[interval] = merge(interval, collected)
    print("Backfilled XAUUSD history:", counts)


if __name__ == "__main__":
    main()
