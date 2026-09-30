#!/usr/bin/env python3
"""OOS validation of Combined Gold Hunter on data published after the original Jul-31 sample."""
from __future__ import annotations
from datetime import datetime, timezone
import scripts.combined_gold_hunter_backtest as base

RAW_URL = "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 9, 23, 59, tzinfo=timezone.utc)

_original_load = base.load

def load_oos():
    base.URL = RAW_URL
    rows = _original_load()
    return [x for x in rows if START <= base.pt(x["openTime"]) <= END]

base.load = load_oos

if __name__ == "__main__":
    base.main()
