# 600351.SH: three-year daily signals × 15-minute microstructure

## Current operational status (2026-10-18)

The 2023-10-16 to 2026-10-16 **daily experiment is complete** at `stock_state_strategy/output/kline_600351_3y_exploratory_v1/2026-10-16/run_39126862763/`. It is not an actual fill backtest. Among 728 valid daily bars, the two follow-up signal hypotheses are: **up-day volume spike** and **first close above prior 20-day high**.

Actual live Google Drive folder listings show **zero entries** under each documented 15-minute source folder:

1. `gdrive:down_data/data/gold/main_board/kline_15m/`
2. `gdrive:ashare_15m_kline/kline_15m/`

The old Google Drive handbooks mention a Tushare downloader and a different repo `magangwwd/python-code`, but neither documentation nor an empty directory counts as downloaded historical minute bars. That other repo was not accessible through the current GitHub connector at implementation time. We have **not** reproduced these minute bars from daily OHLCV, called paid minute APIs, or invented an intraday result.

## Ready-to-run quality gate

`.github/workflows/kline-600351-15m-bridge.yml` runs on PR (offline QA only), main-merge (true Drive probe), or operator manual `workflow_dispatch`. It checks the two exact Drive folders through the existing rclone GitHub secret and archives a non-overwriting evidence record in:

`gdrive:stock_state_strategy/output/kline_600351_15m_quality_v1/2026-10-16/run_<actions_run_id>/`

When both are empty the archive must contain `statistics.json.status=NO_15M_DATA`, with *no* pattern conclusions and no implied 15m data coverage. The action can succeed as a QA audit **without** claiming actual 15-minute inference was done.

If exactly one 15m repository becomes available, it pulls 2023-2026 partitions, checks local target `600351.SH` rows, sixteen 15m bars per trading day (bar-end timestamps 09:45–11:30 and 13:15–15:00), excludes any optional 09:30 auction bar, fails on duplicate timestamps, invalid OHLC, missing bars, zero whole-day volume, and cross-validates 15:00 close with the historical daily Gold close. It uses daily signal rules from `ai_research/kline_three_year.py`, not a new retrospective pattern classification. It stores which event dates have complete verified 15m evidence; never imputes lost dates or future-minute prices.

### What would be explored only after coverage is verified

- Of 600351's daily volume-spike events, how often are >=55% of standard-bar volume in the afternoon? Do those days have different five-*trading*-day close-to-close outcomes versus other volume-spike events?
- Of daily 20-day breakout events, how often is >=30% of standard-bar volume in the final 60 minutes? Do those events behave differently from other breakout dates?
- On the following trading day, what was the first 30 minutes' price change when that following day has complete 15m coverage? This is an **observation after the signal**, not an intraday decision feature available the day before.
- Slice by 2023-24 discovery, 2025 validation and 2026 test. Coverage must be >=90% overall and >=85% within each split to call the joined data representative enough for descriptive study. The threshold is a completeness gate, *not* proof of an edge; selected subgroups with <10 events are explicitly insufficient.

No actual cost-inclusive trading, orders, or live model are permitted. Overnight gap, fill-quality, suspension/limit constraints and hypothesis multiple-testing must be addressed before promoting any finding.

## Restoring the source

The simplest route is a **single-symbol 600351.SH targeted historical 15m backfill** by an account with confirmed `Tushare stk_mins` permission and correct storage authorization; do not launch a 3,000-stock full-history re-download for a one-ticker question. Restore normalized, unadjusted 15m bars with versioned manifests, source request timestamps, correct bar-end timezone, daily-session completeness and publication limits; after that run the existing QA workflow.

If `stk_mins` cannot provide 2023-2026 historical intraday data under the current account and licensing, reduce the temporal coverage honestly and classify the study as partial; do not silently fill historical bars using later snapshots. Check source terms before any redistribution.

## Validation checklist

1. An actual Drive listing and manifest evidence prove which repository contains minute data, and what date range exists.
2. 16 complete bars/day are validated, auction is excluded, and daily adjusted vs raw OHLC basis is not mixed.
3. Signal day is based on daily close; any next-day early trade is a later observation (no lookahead).
4. 2025 and 2026 only form separate evaluations if their minute coverage is measured and sufficient.
5. The report is added as a **separate exploratory supplement**, never overwrite or reinterpret frozen R1 or the already published daily three-year findings.
