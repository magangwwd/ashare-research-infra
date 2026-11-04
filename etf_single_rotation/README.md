# ETF single-asset rotation V1 (research only)

Monthly close signal, next-session execution. At most one ETF or cash. Score = .2 * 20-day return + .5 * 60-day return + .3 * 120-day return; require close > 120-day SMA. Portfolio drawdown alert at 15%; emergency exit at 18%, next executable session; no guaranteed 20% drawdown cap. Avoid survivorship bias and lookahead. Use actual ETF inception dates. This initial scaffold intentionally does not claim backtest results.

Input CSV: data/prices.csv with date,symbol,open,close (real, unadjusted/appropriately adjusted consistently, no invented rows). Run: python etf_single_rotation/backtest.py data/prices.csv. Output: etf_single_rotation/results/equity.csv. Research only, not live trading.
