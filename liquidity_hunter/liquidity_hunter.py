"""Deterministic, signal-only XAUUSD Liquidity Hunter.

Reads data/xauusd_analysis.json and evaluates the latest CLOSED M5 candle
using H1/M15 context. No broker connection and no order execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "xauusd_analysis.json"
MIN_RR = 2.0
SWING_LOOKBACK = 3
LIQUIDITY_LOOKBACK = 30
LEVEL_TOLERANCE = 1.5


def load_data(path: Path = DATA_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def closed_bars(data: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
    bars = data["intervals"][timeframe]["bars"]
    return [b for b in bars if b.get("isOpen") is False]


def candle_direction(c: dict[str, Any]) -> int:
    if c["close"] > c["open"]:
        return 1
    if c["close"] < c["open"]:
        return -1
    return 0


def body(c: dict[str, Any]) -> float:
    return abs(c["close"] - c["open"])


def range_size(c: dict[str, Any]) -> float:
    return c["high"] - c["low"]


def recent_swing_highs(bars: list[dict[str, Any]], lookback: int = SWING_LOOKBACK) -> list[float]:
    levels: list[float] = []
    for i in range(lookback, len(bars) - lookback):
        h = bars[i]["high"]
        if h >= max(x["high"] for x in bars[i - lookback:i + lookback + 1]):
            levels.append(h)
    return levels


def recent_swing_lows(bars: list[dict[str, Any]], lookback: int = SWING_LOOKBACK) -> list[float]:
    levels: list[float] = []
    for i in range(lookback, len(bars) - lookback):
        lo = bars[i]["low"]
        if lo <= min(x["low"] for x in bars[i - lookback:i + lookback + 1]):
            levels.append(lo)
    return levels


def equal_levels(bars: list[dict[str, Any]], side: str) -> list[float]:
    levels: list[float] = []
    values = [b["high"] if side == "high" else b["low"] for b in bars]
    for i, value in enumerate(values):
        nearby = [v for j, v in enumerate(values) if j != i and abs(v - value) <= LEVEL_TOLERANCE]
        if nearby:
            levels.append(sum([value] + nearby) / (len(nearby) + 1))
    return levels


def unique_levels(levels: list[float], tolerance: float = LEVEL_TOLERANCE) -> list[float]:
    result: list[float] = []
    for level in sorted(levels):
        if not result or abs(level - result[-1]) > tolerance:
            result.append(level)
    return result


def sweep_quality(c: dict[str, Any], level: float, direction: str) -> bool:
    """Require a meaningful liquidity raid/rejection, not a tiny level cross."""
    rng = range_size(c)
    if rng <= 0:
        return False

    b = body(c)
    penetration = (c["high"] - level) if direction == "SELL" else (level - c["low"])
    if penetration <= 0:
        return False

    # The sweep should take a meaningful part of the candle's range.
    if penetration < rng * 0.10:
        return False

    if direction == "SELL":
        upper_wick = c["high"] - max(c["open"], c["close"])
        close_position = (c["close"] - c["low"]) / rng
        return upper_wick >= max(b * 0.5, rng * 0.15) and close_position <= 0.60

    lower_wick = min(c["open"], c["close"]) - c["low"]
    close_position = (c["close"] - c["low"]) / rng
    return lower_wick >= max(b * 0.5, rng * 0.15) and close_position >= 0.40


def find_latest_sweep(m5: list[dict[str, Any]], liquidity_highs: list[float], liquidity_lows: list[float]) -> dict[str, Any] | None:
    if len(m5) < 5:
        return None

    current = m5[-1]

    # Bearish reversal: buy-side liquidity taken, then close back below it.
    for level in sorted(liquidity_highs, reverse=True):
        if current["high"] > level and current["close"] < level and sweep_quality(current, level, "SELL"):
            return {"direction": "SELL", "level": level, "candle": current, "type": "buy-side sweep"}

    # Bullish reversal: sell-side liquidity taken, then close back above it.
    for level in sorted(liquidity_lows):
        if current["low"] < level and current["close"] > level and sweep_quality(current, level, "BUY"):
            return {"direction": "BUY", "level": level, "candle": current, "type": "sell-side sweep"}

    return None


def m5_confirmation(m5: list[dict[str, Any]], direction: str) -> str | None:
    if len(m5) < 4:
        return None

    c0, c1, c2, c3 = m5[-4:]

    # Short-term structure break.
    if direction == "BUY" and c3["close"] > max(c0["high"], c1["high"], c2["high"]):
        return "short-term bullish structure break"
    if direction == "SELL" and c3["close"] < min(c0["low"], c1["low"], c2["low"]):
        return "short-term bearish structure break"

    # Displacement: current body is materially larger than recent bodies.
    recent_bodies = [body(x) for x in m5[-8:-1] if range_size(x) > 0]
    avg_body = sum(recent_bodies) / len(recent_bodies) if recent_bodies else 0.0
    if avg_body > 0 and body(c3) >= avg_body * 1.5:
        if direction == "BUY" and candle_direction(c3) == 1:
            return "bullish displacement"
        if direction == "SELL" and candle_direction(c3) == -1:
            return "bearish displacement"

    # Simple rejection/continuation.
    if direction == "BUY" and candle_direction(c3) == 1 and c3["close"] > c2["high"]:
        return "bullish continuation"
    if direction == "SELL" and candle_direction(c3) == -1 and c3["close"] < c2["low"]:
        return "bearish continuation"

    return None


def h1_bias(h1: list[dict[str, Any]]) -> str:
    if len(h1) < 4:
        return "NEUTRAL"
    last = h1[-1]
    prev = h1[-4:]
    if last["close"] > max(x["high"] for x in prev[:-1]):
        return "BULLISH"
    if last["close"] < min(x["low"] for x in prev[:-1]):
        return "BEARISH"
    return "NEUTRAL"


def build_trade(sweep: dict[str, Any], m5: list[dict[str, Any]], m15: list[dict[str, Any]], bias: str) -> dict[str, Any] | None:
    c = sweep["candle"]
    direction = sweep["direction"]

    context = m15[-LIQUIDITY_LOOKBACK:]
    if direction == "BUY":
        entry = c["close"]
        sl = min(c["low"], sweep["level"]) - 0.5
        # Target the next meaningful opposing liquidity/swing that actually
        # provides the required R:R, rather than the nearest tiny M15 high.
        target_levels = unique_levels(
            recent_swing_highs(context) + equal_levels(context, "high")
        )
        candidates = sorted(x for x in target_levels if x > entry)
        if sl >= entry or not candidates:
            return None
        viable = [
            x for x in candidates
            if (x - entry) / (entry - sl) >= MIN_RR
        ]
        if not viable:
            return None
        tp = viable[0]
        rr = (tp - entry) / (entry - sl)
    else:
        entry = c["close"]
        sl = max(c["high"], sweep["level"]) + 0.5
        # Mirror the BUY logic for sell-side targets.
        target_levels = unique_levels(
            recent_swing_lows(context) + equal_levels(context, "low")
        )
        candidates = sorted((x for x in target_levels if x < entry), reverse=True)
        if sl <= entry or not candidates:
            return None
        viable = [
            x for x in candidates
            if (entry - x) / (sl - entry) >= MIN_RR
        ]
        if not viable:
            return None
        tp = viable[0]
        rr = (entry - tp) / (sl - entry)

    return {
        "signal": direction,
        "entry": round(entry, 3),
        "sl": round(sl, 3),
        "tp": round(tp, 3),
        "rr": round(rr, 2),
        "liquidity": sweep["type"],
        "liquidity_level": round(sweep["level"], 3),
        "confirmation": None,
        "h1_bias": bias,
        "candle_time": c["openTime"],
    }


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    h1 = closed_bars(data, "1h")
    m15 = closed_bars(data, "15m")
    m5 = closed_bars(data, "5m")

    if len(m5) < 10 or len(m15) < 10 or len(h1) < 4:
        return {"status": "NO TRADE", "reason": "insufficient closed candle data"}

    context = m15[-LIQUIDITY_LOOKBACK:]
    highs = recent_swing_highs(context) + equal_levels(context, "high")
    lows = recent_swing_lows(context) + equal_levels(context, "low")

    highs = unique_levels(highs)
    lows = unique_levels(lows)

    sweep = find_latest_sweep(m5, highs, lows)
    if not sweep:
        return {"status": "NO TRADE", "reason": "no fresh liquidity sweep on latest M5 candle"}

    confirmation = m5_confirmation(m5, sweep["direction"])
    if not confirmation:
        return {"status": "NO TRADE", "reason": "sweep found but no M5 reversal confirmation"}

    bias = h1_bias(h1)
    trade = build_trade(sweep, m5, m15, bias)
    if not trade:
        return {"status": "NO TRADE", "reason": "no logical target with minimum 1:2 R:R"}

    trade["confirmation"] = confirmation
    trade["status"] = "SETUP FOUND"
    return trade


if __name__ == "__main__":
    result = analyze(load_data())
    print(json.dumps(result, ensure_ascii=False, indent=2))
