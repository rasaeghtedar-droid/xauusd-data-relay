# FVG Experiment Rules — XAUUSD

Signal-only research module. This experiment is isolated from the main Liquidity Hunter.

## Valid FVG
A three-candle imbalance is valid when:
- Bullish: candle[i-2].high < candle[i].low
- Bearish: candle[i-2].low > candle[i].high
- All three candles are CLOSED.

## Quality filters
1. The middle candle must have displacement:
   - body >= 1.25x the mean body of the previous 7 closed candles, and
   - body/range >= 0.55.
2. FVG must be fresh: no candle after formation has fully traversed the entire gap before the setup return.
3. Entry is the 50% midpoint of the FVG.
4. A setup requires a later M5 candle to enter the FVG and close back in the expected direction.
5. Stop is beyond the far edge of the FVG by 0.5 price units.
6. Target is the first meaningful opposing M15 swing/equal-liquidity level that gives RR 2.0–2.5.
7. No signal if RR < 2.0 or no target exists.
8. One active setup at a time in the backtest.
9. This experiment tests M5 FVGs first. M15 FVG support can be added as a separate A/B experiment after this baseline.
10. No automatic execution.

The experiment must report win rate, net R, average RR, max drawdown, number of setups, and no-trade reasons. Results are research only and do not guarantee future profitability.
