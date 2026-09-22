# Historical XAUUSD dataset

This directory is built automatically from Biquote closed OHLC candles.

Files:
- `xauusd_5m.json`
- `xauusd_15m.json`
- `xauusd_1h.json`

Only candles with `isOpen=false` are stored. The dataset grows forward over time and is intended for deterministic Liquidity Hunter replay/backtesting.

Important: this collector cannot recreate historical candles that were never archived. It builds a real dataset from the time it is enabled onward.
