# V4 second-stage research: real 131-trade structural diagnosis (2026-10-20)

**Result: identify an early-exit loss pocket and a zero-overlap 15m data gap, NOT a newly validated winning strategy.**

## Immutable evidence
- Frozen V3 original Drive [comparison.json](https://drive.google.com/file/d/1-vQUcBr6jUB0k-fknH7dGa3mv5s4ETNy/view)
- File SHA256 `5bfb82656806017c08c38e75bfc3ffbbe50494c3f2261dc9b10c1152cb0ddd43`
- Gold/PIT SHA256 `56003f3eab3ea4ad405ac692e11cb7ff42bb0b700d177903311b8829cd24ded6`
- Existing independently source-audited Baostock 15m provider manifest has **only 600351.SH** (non-adjusted).
- New `short_swing_3to5_v4_cohort_audit.py`, `test_short_swing_3to5_v4_cohort_audit.py`. [CI 39388137375](https://github.com/magangwwd/ashare-research-infra/actions/runs/39388137375): **14/14 offline tests passed**. Original V1/V2/V3 simulation code unchanged; no schedule or live trading.

| Historic frozen V2 unguarded baseline | BALANCED | LOOSE |
|---|---:|---:|
| Completed trades | 128 | 131 |
| Distinct traded stocks | 120 | 127 |
| Stocks covered by 600351.SH minute data | 0 | 0 |
| Intraday stop/target fills lacking even same-symbol minute evidence | 63 | 62 |
| Exited in 2 sessions | 53 | 51 |
| Aggregate PnL on those 2-session exits | -68,340.27 CNY | -74,679.53 CNY |
| DEFERRED_STOP_OR_TIME exits | 26 | 33 |
| Aggregate deferred-exit PnL | -34,340.20 CNY | -49,855.89 CNY |
| TARGET_GAP exits | 12 | 21 |
| Aggregate target-gap PnL | +46,485.94 CNY | +73,586.01 CNY |
| All trades realized PnL | -7,151.81 CNY | +5,083.05 CNY |

LOOSE entry-year 2-session early exits: 2024: 17 exits, -23,873.94 CNY; 2025: 18 exits, -27,301.01 CNY; 2026: 16 exits, -23,504.58 CNY. **These are sums of trade outcomes, NOT a portfolio max drawdown.** Post-hoc deletion of these trades would be lookahead bias and is forbidden.

## Candidate problem: breakout ranking overextension
V1 source `build_signals()` sorts descending by:
`strength = adjusted_close / prev_20_high - 1 + (volume_ratio - 1)*0.005`.
V2 LOOSE train-only 2024-25 median score `0.05255378`: below threshold 47 already-executed trades sum +6,325.26 CNY, above threshold 48 trades sum -10,894.98 CNY. Label the 2026 cohort by that fixed historical threshold: below 21 trades +13,569.78 CNY, above 15 trades -3,917.01 CNY.

**This is NOT backtested alternative strategy performance.** After the actual trades, selecting score slices does not reselect the next stock, adjust cash or recompute drawdown. In 2026 both groups' *median trade return* is about -3.21%; a 50,000-shuffle exploratory two-sided permutation test of the difference in average per-trade returns gives p≈0.32, far too weak to claim a robust improvement. The upstream V2 parameters were already researched using 2026 data; this is not truly untouched forward data.

## Controlled next experiment
1. Source and daily-Gold-cross-check actual trade symbols' 15m data (prioritize early-stop losses and large gap winners), verify complete 16 bars/session, consistent unadjusted OHLC, timestamps, T+1 and auction limits. If unavailable mark unverified rather than presume a stop fill.
2. Predefine one or two causal entry-time features (overextended 20-day breakout/volume spike and buy-day opening gap); **full re-run** Gold+PIT simulator with same single-position 50k capital, 30–50k per trade, all cost/limit/stop assumptions. Never filter on future 2-day exit or future PnL.
3. Time-ordered calibration and **subsequent never-used forward PAPER** cohort, worst-case slippage/suspension, target maxDD≤25% and return>0; do not trade live or auto-merge on historical sample only.

**Status:** research findings reproduced, CI passed; 15m coverage remains 0 for these simulated trades, not fixed, and no new strategy has passed the acceptance criteria. The unattended research scheduler stays paused as requested.
