# 600031 v1.0 — fixed CNY 40,000 single-account equity audit (2026-10-29)

## Scope and provenance
- Google Drive Ashare_Quant/current.json remains published snapshot `2026-10-24T211915.119571_0000-7c30e07d`, validated=true, critical_history_complete=false.
- The archived 2023Q4, 2024 and 2025–2026 daily five-stock extracts have 755 aligned sessions, 3,775 rows (2023 starts Sep 6; 2026 ends Oct 23). Each CSV's SHA256 was rechecked against v0.8 `source_csv_sha256`.
- The *historically selected* v0.9 one-position schedule contains 60 accepted trades and 19 excluded overlapping pattern trades. Fixed arbitrary priority: breakout > upper wick > breakdown > low-volume.
- This is an unadjusted-price, retrospective **scenario**. Neither a blinded out-of-time test nor evidence of an executable edge.

## New equity-ledger calculation
- One account starts with CNY 40,000. For each accepted entry, buy maximum affordable shares in 100-share lots at the **next opening price** with 0.05% buy slippage and 0.03% commission. Sell at the scheduled 3/5-session later opening price with 0.05% sell slippage, 0.03% commission and 0.05% stamp tax. Same-open exit and entry are allowed. Unused cash stays idle; daily equity = cash + shares × unadjusted closing price.
- CNY **43,760.76** ending equity; **+9.4019%** simulated cumulative return; **-25.5043%** max daily marked-equity drawdown (2026-06-11); **60** trades, **0** cash rejects; **268/755** days in a position. Winning/losing trades: **29/31**.
- Calendar year account returns: 2023 Sep–Dec **-8.6033%**; 2024 **+26.8977%**; 2025 **+9.7065%**; 2026 through Oct 23 **-14.0179%**.
- CNY 5 minimum commission sensitivity equals base result on this specific 40k account because percentage commissions exceed the minimum. The v0.9 fixed-notional P&L sum (+CNY 5,277.80) is **not** the v1.0 account P&L (+CNY 3,760.76); position sizing and cash constraints differ.
- Locally validated with five tests and an independently SHA-audited archived research package; latest cloud CI must be checked on the new head before calling it passed.

## Reproduce
```bash
python skills/stock-personality-analysis/portfolio_equity_v10.py \
  --audit 600031_portfolio_overlap_v09.json \
  --daily 600031_2023_Q4_five_stock_daily.csv 600031_2024_five_stock_daily.csv 600031_2025_2026_peer_daily.csv \
  --out 600031_portfolio_equity_v10.json
python -m unittest tests/test_stock_personality_portfolio_equity_v10.py
```

The full source CSVs, per-day equity curve, v0.9 schedule, v1.0 output, SHA256 manifest and report belong in the fixed Drive research folder; do not commit raw data into GitHub.

## Blockers before strategy acceptance
- No independently reconciled dividends, ex-rights, splits or corporate-action cash ledger. Raw OHLC daily mark-to-market may be economically wrong around those dates.
- No opening-auction fill, limit-up/down, suspension or queue feasibility verification; T+1 is satisfied by the 3/5-session schedule but real execution remains unverified.
- 2023 Jan–Aug and broad 15-minute history missing; published forward data after Oct 23 absent. The 2026-09-30 pre_close quality issue remains tracked in #119.
- All current historical years were used in exploratory development. **No untouched prospective holdout** and no stable stock-specific edge confirmed. Keep PR #118 draft; do not modify main or Task01 production pointer.
