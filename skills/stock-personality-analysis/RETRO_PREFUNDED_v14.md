# 600031 v1.4 — prefunded 100k single-stock account (2026-10-29)

**Retrospective, not an executable strategy or prospective holdout.** This is the primary capital-rich research scenario replacing v1.2's 40k-cash stress scenario. Frozen four patterns and original 60 selected trades are unchanged; no re-optimization.

## Funding contract

- Start CNY 100,000 total cash; per entry cap CNY 40,000 **including** 100-share board-lot purchase, 0.05% buy slippage, 0.03% commission (CNY 5 minimum). No margin/leverage.
- Hold only 600031.SH. At six same-open sale/new-buy dates, **new buy orders must be fully pre-funded from previous-close cash**. Do not use that same opening auction's sale proceeds or ex-date dividends to place the buy. Book sell then buy for post-open accounting only after checking prefunding independently.
- Sell assumptions: 0.05% slippage, 0.03% commission (CNY 5 minimum), 0.05% stamp duty. Dividends are based on record-date close shares and credited after ex-date open. Uniform 20% withholding is a sensitivity only.
- Read-only validated snapshot: `2026-10-24T211915.119571_0000-7c30e07d`, manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`; `critical_history_complete=false`. Historical 755 aligned sessions/3775 peer rows, partial 2023; original v0.9 schedule and three daily CSVs and dividends match archived v1.2 SHA256 manifest.

## Local independent replay results

| Measure | Gross dividend | 20% tax sensitivity |
| --- | ---: | ---: |
| Trades / cash rejects | 60 / 0 | 60 / 0 |
| Ending 100k account (CNY) | 106,354.83 | 106,139.43 |
| 100k total-account return | +6.3548% | +6.1394% |
| Maximum daily-close drawdown | -9.3107% | -9.4120% |
| Gross dividend cash (CNY) | 1,077.00 | 1,077.00 |

**Capital actually needed:** peak single purchase debit CNY 39,967.95; peak same-open old-stock open value plus new-buy reservation CNY 82,633.74; exact minimum initial cash (rounded up to cents) CNY **80,896.61**, binding 2024-02-22; additional buffer above 40k CNY 40,896.61. 100k scenario minimum day-end cash CNY 56,600.79, maximum CNY 112,896.53. All six same-open flips passed independent prefunding. The 40k nominal profit/budget ratio is +15.8871%, **NOT** a 40k standalone account return. The old 40k-account v1.1 +11.8743% must never be presented as a 100k-account return; v1.2 54-trade -5.4880% remains only a cash-stress sensitivity.

## Personality / peer specificity

| Frozen pattern | Trades | Net trade win rate | Net trade P&L excluding dividends | Same-date four-peer mean gross alpha |
| --- | ---: | ---: | ---: | ---: |
| D_breakout | 17 | 58.82% | +8,556.12 CNY | -0.7446 pp |
| G_upper_wick | 16 | 43.75% | -2,832.84 CNY | +0.1490 pp |
| B_breakdown | 19 | 42.11% | -1,305.71 CNY | +0.8468 pp |
| F_tight_lowvol | 8 | 50.00% | +860.26 CNY | +1.0946 pp |

Mean matched-peer **gross** alpha across 60 trades +0.2429 pp, untested for significance and not directly comparable with net after-cost P&L. The D breakout has positive raw account trade P&L yet negative matched-peer alpha, and changes sign across years; bearish upper-wick outcomes are not demonstrated to be uniquely 600031-specific. No confirmed stock-specific edge.

## Provenance and pending gates

Five input-file SHA256 checks matched the v1.2 archive, five local synthetic tests passed; the local v1.4 reproduction ZIP includes the full script, tests, source CSV/JSON, 60-trade ledger, 755-session cash/equity CSV, report, and a verified 15-file SHA256 manifest (ZIP SHA256 `595a8bfe08a52f02684fa95cb6d47193e6113c3f37c7af4ad8f24226a4cb8780`). Cloud archive upload may be separately pending; do not claim it was uploaded until Drive listing verifies it.

Full 2023–26 `adj_factor`/`stk_limit`/`suspend_d`, exchange-auction fills, adjusted-price signal stability, and post-2026-10-26 **new validated production** forward observations are still pending. v1.3 decoded only 33 September–October 2026 days, not all history. Issue #119 (2026-09-30 missing `pre_close`) remains open. Keep PR #118 draft, never merge into main or alter production data.
