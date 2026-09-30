# Experimental FVG Setups

This branch contains an isolated FVG hypothesis. It does not modify the main Liquidity Hunter.

Rules:
- Closed candles only.
- Three-candle imbalance:
  - Bullish: candle 1 high < candle 3 low.
  - Bearish: candle 1 low > candle 3 high.
- Middle candle must show displacement: body >= 1.5x recent average body.
- FVG size must be >= 10% of recent ATR.
- Freshness is tracked after formation.
- A later trade test must require return into the FVG, confirmation, logical target and RR >= 2.
- Results are experimental; no claim of profitability is made before replay testing.
