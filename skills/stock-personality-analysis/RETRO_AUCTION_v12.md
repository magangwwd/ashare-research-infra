# 600031 v1.2: same-open auction funding stress (2026-10-29)

**Important: the previous +11.8743% dividend-aware 40k-CNY historical account return is NOT executable without further auction funding verification.**

Read-only, frozen v0.9 60-trade schedule; 755 observed daily sessions (2023-09-06 through 2026-10-23). Official production Drive pointer remains validated 2026-10-24 version, but history is incomplete and there is no prospective post-2026-10-23 snapshot.

## Newly discovered assumption
The v1.1 account simulation processes a sell at the day's open, credits proceeds, then buys a new position at the **same** opening price. Six entry dates also coincide with a prior exit:
2024-02-22, 2024-06-14, 2024-09-25, 2025-03-03, 2025-10-15, 2026-07-08.

For **all six**, previous-close unencumbered cash is **below** the cost of even one 100-share board lot at the new opening price including 0.05% buy slippage and a CNY 5 minimum commission. Per-case shortages (CNY): 376.37, 1192.85, 826.16, 359.41, 1113.15, 808.78.

This is **not proof** that no order could ever fill: broker mechanics, credit, extra funds or buying later at a different price can alter the outcome. It **does** invalidate treating same-opening auction sale proceeds as already available for pre-auction buy orders without proof.

## Frozen-schedule stress results
| Scenario | Trades | Ending equity (CNY) | Return | Max drawdown | Gross dividend (CNY) |
|---|---:|---:|---:|---:|---:|
| v1.1 sell-then-buy at same open, gross dividend | 60 | 44,749.72 | +11.8743% | -24.3356% | 1,254 |
| v1.2 skip only six same-open new buys, gross dividend | 54 | **37,804.80** | **-5.4880%** | -24.2925% | 558 |
| v1.1 uniform 20% dividend tax sensitivity | 60 | 44,481.33 | +11.2033% | -24.6606% | 1,254 |
| v1.2 skip same-open, 20% tax sensitivity | 54 | 37,693.20 | -5.7670% | -24.5344% | 558 |

Skipping the six trades is a **conservative scenario**, not a unique real-world fill model. No future information is used to alter the frozen signal definitions, and no scenario may be selected by optimizing historical return.

## Reproduction
Use the source CSV, frozen schedule and dividend events in the versioned Drive research ZIP, not files from unvalidated staging:
```bash
python skills/stock-personality-analysis/auction_cash_audit_v12.py \
  --audit 600031_portfolio_overlap_v09.json \
  --daily 600031_2023_Q4_five_stock_daily.csv 600031_2024_five_stock_daily.csv 600031_2025_2026_peer_daily.csv \
  --events skills/stock-personality-analysis/dividend_events_v11.json \
  --out 600031_auction_cash_v12.json
```

The script reports each flip, pre-open cash, one-lot cost and shortfall, and both gross and tax-sensitive 40k account outcomes. Five synthetic regression tests in `tests/test_stock_personality_auction_cash_v12.py` cover detection, zero flips, skip behavior, freeze guard and fundable-lot counterexample. Offline CI is **not** a real exchange execution test.

**Next:** Verify broker auction funding; test pre-registered next-day entry / later-intraday fills; read official `stk_limit`, `suspend_d`, `adj_factor` Parquet rows in an isolated decoder-enabled runner; require an untouched validated prospective sample before strategy acceptance. Keep PR draft; do not alter production data/pointer.
