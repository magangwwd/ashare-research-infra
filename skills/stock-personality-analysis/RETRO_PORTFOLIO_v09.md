# 600031 v0.9: single-stock portfolio overlap / capital audit

Read-only audit of the SHA-verified archived v0.8 five-stock 755-session/3,775-row panel. **No new post-2026-10-23 validated market data.** This is not an untouched holdout or executable strategy.

## Finding
Four frozen patterns were simulated independently with CNY 40,000 *per trade and per pattern*, allowing simultaneous positions in the **same** 600031 stock. Therefore adding all pattern-level P&Ls is not a valid CNY 40,000 total-capital portfolio result.

| Retrospective diagnostic | Result |
|---|---:|
| Independent pattern-level trades | 79 |
| Calendar sessions with >=2 concurrent pattern positions | 53 of 755 |
| Sessions with >=3 concurrent positions | 4 |
| Maximum concurrent positions | 3 (up to ~CNY 120,000 nominal exposure) |
| Deterministic one-position-total trade count | 60 |
| Blocked by capital conflict | 19 |
| Simple sum of 79 per-trade simulated net P&L | +CNY 10,407.46 |
| Simple sum of 60 selected per-trade simulated net P&L | +CNY 5,277.80 |
| Across all 24 fixed priority orders, selected P&L range | +CNY 4,550.75 to +7,067.48 |

The default tie-break priority is D_breakout > G_upper_wick > B_breakdown > F_tight_lowvol, fixed mechanically for this audit, **not** selected as a predictive or optimal trading rule. Entry at next session open; exit at open after 3 or 5 sessions. The 60 selected trades are a capital-overlap sensitivity, not a true equity-curve simulation. No compounding, idle cash, cash dividends, suspension, limit queue, or verified fills. Historical patterns were already inspected; do not optimize the 24 priorities on this same sample.

## Reproducibility
Input: `600031_cross_year_v08.json` from archived v0.8 package; SHA256 `cce4d01d61e2ac8ad194449e30f1fc9f76949b4c5a48e7e5f5d1e4ba4187f883`. Source CSV SHA256:
- 2023Q4: `7a0dc6baa785efd46e900ed989970760e549b6e6d1728cdfca6f133fb170ab11`
- 2024: `f465532a35c8af2efabcd29c8b2d0464d3f576c6bb2bb8fd5102b223c73b66a6`
- 2025-2026: `76a44d09f85014a735a4f4554285d65cdd344f73d2411f875eafd394993057f0`

Run `python skills/stock-personality-analysis/portfolio_overlap_v09.py --input 600031_cross_year_v08.json --daily 600031_2023_Q4_five_stock_daily.csv 600031_2024_five_stock_daily.csv 600031_2025_2026_peer_daily.csv --out portfolio_v09.json`.

Four local offline tests passed. Verify cloud CI on the **new** PR head; do not reuse v0.8 CI as v0.9 proof. Published Drive `current.json` still points to 2026-10-24 snapshot with `critical_history_complete=false`. No changes to main, published data or pointer. The final strategy is NOT accepted. Next: true account equity curve, price-limit/suspension/dividend checks, independent prospective observations.
