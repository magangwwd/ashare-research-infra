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


## v0.3: confidence and 15-minute completeness audit (2026-10-28)
Run `python skills/stock-personality-analysis/uncertainty_audit.py --daily peer_daily.csv --minutes minute_sample.csv --execution execution.json --out robustness.json`.
It calculates Wilson 95% intervals for trade win proportions, seeded 10,000-draw bootstrap 95% intervals for mean net returns, and conservative nine-rule Bonferroni-adjusted intervals. With fewer than five independent trades the mean interval is intentionally omitted. These intervals still assume IID trade samples, cannot repair data-snooping, and do not replace an untouched holdout. The 2026 data have already been examined.
For the 2026 three-session long-upper-wick signal (10 non-overlapping trades), mean net -2.057%; naive bootstrap 95% interval [-3.731%, -0.263%], nine-rule-adjusted interval [-4.374%, +0.389%]. This is NOT a statistically confirmed stock-specific bearish edge. For 2025 five-session breakout (8 trades), mean net +1.657%, naive 95% interval [-0.322%, +3.959%].
October minute sample contains 153 bars on 9 dates (17 per day, Oct 13-23), with missing Oct 8, 9, 12 relative to 12 daily trading sessions through Oct 23. Minute-to-daily OHLC matches exactly, minute volume is 100x daily volume (shares vs lots), minute amount is 1000x daily amount (yuan vs thousand yuan). No minute predictive signal should be claimed from this coverage.
CI for the v0.3 script must be checked on the latest PR head before describing it as cloud-verified. Do not merge PR #118 until action-adjustment, limit/suspension fill and genuinely new out-of-sample periods are validated.

## v0.4: matched-date peer specificity and missing pre-close (2026-10-28)
Run: `python skills/stock-personality-analysis/peer_matched_v04.py --daily peer_daily.csv --execution execution_v0.2.json --out peer_matched_v0.4.json`.
The script matches each target trade's next-open entry/exit dates to the same dates on four engineering-machinery peers; reports gross target-minus-equal-weight-peer percentage points, flags possible ex-action windows and missing `pre_close`, and calculates a same-month random-date placebo (10,000 draws, seeded). Fewer than five events receive no placebo p-value. This is post-selected retrospective analysis, NOT a blind holdout or a valid stand-alone significance test; peer raw prices do not include dividend cashflows or verified fills.
The five-stock daily extract has five missing `pre_close` values on 2026-09-30, one per stock; raw OHLC remains present. For 2026 long-upper-wick 3-session events, 10 target trades average gross -1.851% versus -1.468% for same-date peers, only -0.383pp relative difference (two of ten windows have possible corporate actions). The same-month placebo is not significant; the signal's negative return is not demonstrated to be stock-specific. Do not merge the PR into production until longer history, corporate-action verification, fill checks and untouched holdout are complete.


## v0.5/v0.6: frozen prospective rules and 2024 backward stress (2026-10-29)
Read `HOLDOUT_PROTOCOL_v05.md` before any forward test. 2025–2026 were used for exploratory selection; neither year is a blind holdout. For a *historical backward stress*, 12 SHA-verified 2024 official daily partitions were decoded: 242 aligned sessions per stock across 600031.SH and four frozen peers, 1,210 rows total. Run `python skills/stock-personality-analysis/backward_stress_v06.py --daily 600031_2024_five_stock_daily.csv --out stress.json` with the archived five-stock CSV. For 2024 5-session breakout, 6 independent trades averaged +2.954% target net under assumed costs, but -2.703 percentage points **gross** versus same-date four-peer mean (6/6 underperformed). 2024 3-session upper wick, 8 trades, averaged +2.851% net, contrary to the negative 2026 sample. This undermines simple year-invariant pattern claims. See `RETRO_2024_v06.md` for complete numbers, provenance and limitations.

**Source quality issue:** Published clean 2026-09-30 daily data have `pre_close=NULL` for 5,559 stocks, while earlier raw snapshots contain one consistent valid `pre_close` per code/date. Later incomplete raw fetches and `keep='last'` clean dedup likely explain the loss. See GitHub Issue #119. Do not silently impute in research or modify production snapshots. No validated dividend cashflows, fill constraints, full minute history, or prospective holdout yet. Keep research PR draft.


## v0.7: 2023 Sep-Dec partial-year SHA-verified stress (2026-10-29)
The official published snapshot contains **only four 2023 clean daily partitions** (September-December); do not call this 2023 full-year coverage. All four downloaded partitions matched the official production manifest by SHA256 and bytes. Five aligned stocks have **77 sessions (2023-09-06 to 2023-12-29), 385 rows, zero missing pre_close**. Combined verified daily research coverage is now 755 sessions / 3,775 five-stock rows across partial 2023, all 2024, all 2025 and partial 2026. The 2026-09-30 pre_close data defect remains unresolved in #119.

Run `python skills/stock-personality-analysis/coverage_guard_v07.py --daily 600031_2023_Q4_five_stock_daily.csv --year 2023 --months 9,10,11,12 --out 600031_2023Q4_v07_audit.json`. The guard rejects false full-year claims, misaligned peers and duplicate keys. Source CSV and per-file SHA audit are archived in Drive, not committed to GitHub. See `RETRO_2023Q4_v07.md` for source hashes and detailed evidence.

Frozen 2023 Q4 retrospective: volume breakout **0** trades; upper wick 3-session **2** trades, target mean net **+0.160%**, relative to same-date peers gross **-0.773 pp**; breakdown 5-session **4** trades, target net **-2.265%**, relative gross **-0.252 pp**; tight low-volume 5-session **2** trades, target net **-2.152%**, relative gross **+0.447 pp**. These are very small, backward-selected historical samples, not untouched holdout or live-tradable profit. The same breakdown signal was net-positive in 2024; do not infer year-invariant bearish predictive power.

Keep PR #118 draft until true forward holdout, corporate-action cashflows, limit/suspension fills and full minute coverage are independently verified.