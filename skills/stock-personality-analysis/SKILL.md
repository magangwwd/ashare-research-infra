# stock-personality-analysis · v0.1 (exploratory)

## Purpose
Use verified OHLCV data to describe the *distinctive, repeatable* pre-rise, pre-fall and sideways behavior of one A-share stock. A report must always show historical sample counts, same-period peer comparisons, out-of-time stability, counterexamples and limitations. Do not invent or overstate probabilities.

## Data
Read only the validated Google Drive `Ashare_Quant/current.json` production version and its `data/clean/daily` Parquet partitions. Preserve snapshot version ID and manifest checksum. Validate ts_code, trade_date, OHLCV, duplicates, corporate actions/adjustment policy, missing days, suspensions and source units. For minute data, `stk_mins` is shares while Tushare `daily.vol` is lots (100 shares): compare only after conversion. If history is incomplete, clearly scope the findings.

## Workflow
1. Confirm the published snapshot, file counts, date coverage and SHA256. Never use unverified staging as production.
2. Choose one stock and same-date sector peers, preferably four or more.
3. Define all signal rules *before* inspecting future returns. Signals at day t may use only information through close t. Rolling volume and high/low references must exclude t.
4. Label the future 1/3/5/10-session close-to-close returns. Initial default: after five sessions, up >=3%, down <=-3%, flat if absolute return <=2%. These are not exhaustive categories.
5. Compare each condition to its **same-year unconditional base rate**, and give numerator, denominator, average return, and at least five-session-separated episode count. Overlapping five-day labels are not independent.
6. Split training, validation and holdout chronologically; exclude label windows crossing boundaries. Add walk-forward embargo, blocked-bootstrap intervals and multiple-testing correction before asserting significance.
7. Compare the same conditions on peers and market/industry benchmarks. Market-wide behavior is not a stock's unique personality.
8. Test the strategy using next-session executable prices, fees, stamp tax, slippage, A-share T+1, suspensions, price limits, capacity, and maximum adverse excursion. Do not call close-to-close outcomes a tradable return.
9. Archive versioned JSON/CSV, a Chinese plain-language report, code/tests and provenance in `Ashare_Quant/stock-personality-research`. Do not modify Task01 production data or pointer.

## First measured case (600031)
Source snapshot `2026-10-24T211915.119571_0000-7c30e07d`; 2025-01-02 to 2026-10-23, 436 daily sessions for 600031 and each of four peers (2,180 rows total). October 2026 15-minute check: 153 bars across nine days, 9/9 daily closes match, minute-to-daily volume ratio exactly 100. Formal report is in the linked Google Drive research folder.

2025 volume breakout: 16 signal days, mean subsequent five-day return +2.071%; 2026: 12 days, mean -2.025%. Ten-day tight low-volume pattern: 12 signal days in 2025, 4 in 2026, but only **one** independent 2026 episode. Breakdown 2026: 6/12 daily observations followed by a <=-3% five-day return, falling to 2/7 (28.57%) after spacing episodes; do not advertise 50% as reliable.

## Acceptance status
- Real data read and 4/4 local unit tests: **completed**.
- GitHub CLI script and offline Pipeline CI (Run 40686330439): **completed**, still in draft PR #118, not merged into main.
- Longer-history coverage, 15-minute features, walk-forward validation, execution-cost backtest and final strategy acceptance: **pending**.
- Current version is a reproducible *method and initial evidence*, not a validated trade recommendation.

## v0.2: next-open execution sensitivity (2026-10-28)
Run: `python skills/stock-personality-analysis/execution_backtest.py --input peer_daily.csv --target 600031.SH --cash 40000 --out execution.json`.
Signals at close t; enter at next-session open and exit at open 3 or 5 sessions later, one open position per pattern, 100-share lots. Assumed each-side commission 0.03%, sell stamp 0.05%, each-side slippage 0.05% (approx 0.21% round trip). No fill verification for suspensions/price limits, no independent dividend cash ledger; `pre_close` adjustment is sensitivity only. Do not call these figures executable trade profits without independent execution verification.
The 2025/2026 results are retrospective, not untouched holdout. 2025 breakout 5-day: 8 non-overlapping trades, mean net +1.657%; 2026: 6 trades, mean net -0.205%. 2026 upper-wick 3-day: 10 trades, mean net -2.057%. Compare target with four peers on the *same trade dates* to distinguish market-wide effects. Data and v0.2 report are in the fixed Drive research folder.
CI validation of new code must be checked on the new commit; earlier successful CI only covered v0.1.
