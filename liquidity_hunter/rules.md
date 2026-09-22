# XAUUSD Liquidity Hunter Rules

## Purpose
Signal-only analysis for XAUUSD. No order execution.

## Data
- Primary timeframes: H1, M15, M5.
- Use closed candles only.
- Ignore the currently open candle.

## 1. Liquidity Zones
Identify meaningful liquidity around:
- Recent swing highs and swing lows.
- Clear equal highs and equal lows.
- Important session highs and lows when available.
- Nearby clustered highs/lows that are visually obvious.

Prefer zones that have not already been swept.

## 2. Liquidity Sweep
A sweep is considered when price trades through a meaningful liquidity level/zone and then closes back inside or clearly rejects the level.

Bullish sweep:
- Price takes sell-side liquidity below a meaningful low.
- Price rejects the lower area and returns above the swept level.

Bearish sweep:
- Price takes buy-side liquidity above a meaningful high.
- Price rejects the upper area and returns below the swept level.

A simple touch without taking liquidity is not a sweep.

## 3. M5 Reversal Confirmation
After a sweep, require at least ONE reasonable M5 confirmation:
- CHOCH/MSS or short-term structure break in the reversal direction.
- Clear displacement away from the swept area.
- Formation and usable reaction from an FVG.
- Strong rejection followed by directional continuation.

Do not require all ICT concepts simultaneously.

## 4. Entry
Entry should be based on the post-sweep reversal structure, preferably:
- Retest of the confirmation area,
- FVG/OB reaction,
- Or a controlled entry after confirmation close.

Avoid chasing an extended candle when a logical retest is available.

## 5. Stop Loss
SL must invalidate the setup logically.
- BUY: below the sweep low/reversal invalidation area.
- SELL: above the sweep high/reversal invalidation area.
Do not use an arbitrary fixed-distance SL.

## 6. Take Profit
TP must be based on a real logical target:
- Opposite liquidity,
- Significant swing,
- Structural target,
- Or another clearly identifiable price objective.

Do not manufacture a TP only to satisfy R:R.

## 7. Risk/Reward
Only issue a trade setup when a logical target provides at least 1:2 R:R.

## 8. No Trade Conditions
Return NO TRADE when:
- No meaningful liquidity was swept.
- Sweep occurred but there is no reasonable M5 reversal confirmation.
- Price is only approaching liquidity without taking it.
- Entry/SL/TP cannot be defined logically.
- Logical R:R is below 1:2.
- The setup has already been reported.

## 9. Duplicate Protection
The same liquidity sweep should generate at most one signal.
Track the swept level/time/direction sufficiently to prevent repeated alerts.

## 10. Output
For a valid setup:

🥇 طلا — سیگنال
🟢 BUY / 🔴 SELL
📍 ورود:
🛑 SL:
🎯 TP:
📊 R:R:
🔑 دلیل:
❌ ابطال:

If no valid setup exists:
NO TRADE

## Important
This document defines analysis rules only. It does not authorize automatic trading or order execution.
