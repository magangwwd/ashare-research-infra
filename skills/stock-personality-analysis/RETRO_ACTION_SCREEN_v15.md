# 600031 v1.5 — daily pre_close/cash-event screening (2026-10-29)

**Read-only historical screen, NOT full adj_factor, suspension, price-limit, or opening-auction fill validation.** Freeze the four rules and original v0.9 one-position 60-trade schedule. The capital-rich v1.4 account remains the primary scenario: CNY 100k total, CNY 40k per-entry cap, 60 cash-prefunded hypothetical trades, +6.3548% gross-dividend account return, -9.3107% max daily-close drawdown. These are not verified executable profits.

## Source and method
- Google Drive `Ashare_Quant/current.json`: validated `2026-10-24T211915.119571_0000-7c30e07d`, manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`; `critical_history_complete=false`. No post-2026-10-26 validated forward snapshot.
- Reused the v1.4 ZIP's archived three SHA-tracked five-stock daily CSVs (2023 Sep-Dec, 2024, 2025-Oct 2026), frozen 60-trade CSV and four v1.1 cash-event entries. **No raw/production data was changed.**
- Compare each stock/date's `pre_close` with that stock's *previous available session* `close` using >CNY 0.015 discrepancy threshold. This is a *screen* for potential ex-rights/ex-dividend discontinuities; not a complete corporate-action proof. Missing pre_close stays missing.
- Link each target candidate to an archived cash event by ex-date and compare the amount; assign dividend entitlement only when `entry_date <= record_date < exit_date` (old stock at an ex-date same-open flip, not the new stock).

## Verified offline screen
- Five stocks × 755 aligned daily sessions = **3,775** rows; 600031 **755** rows; **0 duplicate stock/date keys**.
- Five `pre_close` nulls, all on **2026-09-30**, one per stock; GitHub Issue #119 remains open.
- Across five stocks: **22** pre_close discontinuity candidates. For 600031, exactly **4**: 2024-06-21 -CNY 0.22, 2025-06-11 -0.36, 2025-10-15 -0.31, 2026-07-17 -0.18. All four match the amount/date in the **previously archived** cash-event list; this is a consistency check, **not independent verification of dividend source, stock splits, or adj_factor**.
- Of four cash-event dates, two involve shares held on the record date: 2024-06-20 **2,500** shares (gross CNY 550); 2025-10-14 **1,700** shares (gross CNY 527). Two dates have zero eligible held shares. Sum CNY **1,077**, matching v1.4 gross-dividend ledger. On 2025-10-15 the same-open new position does not receive the previous record date's dividend.
- Frozen 60 trades by **entry year**, *excluding dividends* (not yearly account returns): 2023 Sep-Dec **6** trades / **-CNY 3,501.31**; 2024 **17** / **+10,186.37**; 2025 **21** / **+3,886.90**; 2026 Jan-Sep **16** / **-5,294.13**. Thus the positive aggregate is concentrated in 2024–25 and **not stable across years**. Historical samples are all post-selected and no year is an untouched holdout.

## Reproduce and limits
```bash
python skills/stock-personality-analysis/daily_action_screen_v15.py \
 --daily 600031_2023_Q4_five_stock_daily.csv 600031_2024_five_stock_daily.csv 600031_2025_2026_peer_daily.csv \
 --events dividend_events_v11.json --trades 600031_v14_trades_gross.csv \
 --out 600031_v15_daily_action_screen.json
```

The script has four synthetic tests for same-open record-date entitlement, missing pre_close, unexplained gap and duplicate detection. The research branch CI is software validation only; run status must be checked for the exact latest head.

**Next gates:** (1) independently SHA-verify all published 2023–26 `adj_factor`, `stk_limit`, `suspend_d` partitions using GitHub Actions pyarrow, join them to the 60 trade dates, compare adjusted signal stability; (2) check intraday limits, auction fills and missing minutes; (3) register prospective observations only from a new post-2026-10-26 **validated** production snapshot, never staging/backfilled old data. Keep PR #118 draft; no main merge or production pointer edits.
