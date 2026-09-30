"""Experimental FVG detector. Not used by the main trading engine."""

from __future__ import annotations

from typing import Any

MIN_DISPLACEMENT_MULTIPLIER = 1.5
MIN_FVG_ATR_FRACTION = 0.10
ATR_LOOKBACK = 14


def body(c: dict[str, Any]) -> float:
    return abs(c["close"] - c["open"])


def rng(c: dict[str, Any]) -> float:
    return max(c["high"] - c["low"], 0.0)


def direction(c: dict[str, Any]) -> int:
    return 1 if c["close"] > c["open"] else -1 if c["close"] < c["open"] else 0


def atr(bars: list[dict[str, Any]], period: int = ATR_LOOKBACK) -> float:
    sample = bars[-period:]
    if not sample:
        return 0.0
    return sum(rng(c) for c in sample) / len(sample)


def detect_fvg(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect closed 3-candle FVGs using only information available at candle close.

    Bullish: candle1.high < candle3.low.
    Bearish: candle1.low > candle3.high.
    The middle candle must provide displacement relative to recent bodies.
    """
    found: list[dict[str, Any]] = []
    if len(bars) < ATR_LOOKBACK + 3:
        return found

    for i in range(ATR_LOOKBACK + 2, len(bars)):
        c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
        recent_bodies = [body(x) for x in bars[i - ATR_LOOKBACK - 1:i - 1]]
        avg_body = sum(recent_bodies) / len(recent_bodies) if recent_bodies else 0.0
        if avg_body <= 0 or body(c2) < avg_body * MIN_DISPLACEMENT_MULTIPLIER:
            continue

        a = atr(bars[:i], ATR_LOOKBACK)
        if a <= 0:
            continue

        if c1["high"] < c3["low"]:
            gap_low, gap_high = c1["high"], c3["low"]
            if gap_high - gap_low >= a * MIN_FVG_ATR_FRACTION and direction(c2) == 1:
                found.append({
                    "direction": "BUY",
                    "time": c3["openTime"],
                    "low": gap_low,
                    "high": gap_high,
                    "size": gap_high - gap_low,
                    "atr": a,
                    "middle_body": body(c2),
                })

        if c1["low"] > c3["high"]:
            gap_low, gap_high = c3["high"], c1["low"]
            if gap_high - gap_low >= a * MIN_FVG_ATR_FRACTION and direction(c2) == -1:
                found.append({
                    "direction": "SELL",
                    "time": c3["openTime"],
                    "low": gap_low,
                    "high": gap_high,
                    "size": gap_high - gap_low,
                    "atr": a,
                    "middle_body": body(c2),
                })

    return found


def is_fresh_at(fvg: dict[str, Any], bars_after: list[dict[str, Any]]) -> bool:
    """Fresh means price has not fully crossed the FVG after it formed."""
    for c in bars_after:
        if fvg["direction"] == "BUY":
            if c["low"] <= fvg["low"]:
                return False
        else:
            if c["high"] >= fvg["high"]:
                return False
    return True
