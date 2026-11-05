# 600031 v0.8 — cross-year warm-up correction (2026-10-29)

**Status:** retrospective method correction, NOT prospective holdout or a validated trading edge.

Re-read the verified Google Drive `Ashare_Quant/current.json` pointer. It still targets `2026-10-24T211915.119571_0000-7c30e07d`, `validated=true`, `critical_history_complete=false`, manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`. Newer staging folders are not approved production snapshots.

Source inputs (archived in the fixed Drive research folder, not in GitHub): `600031_2023_Q4_five_stock_daily.csv`, `600031_2024_five_stock_daily.csv`, `600031_2025_2026_peer_daily.csv`. Combined 755 sessions and 3,775 five-stock rows (2023 Sep–Dec 77; 2024 242; 2025 243; 2026 through Oct 23 193), five aligned calendars and no duplicate stock/date keys. The 2026-09-30 five-stock `pre_close` nulls remain open in #119.

**Method correction:** prior one-year-at-a-time simulations reset 20-day lookbacks every January. With the preceding December history available, those early-January signals are eligible. Keep the same four frozen patterns and 3/5-session next-open trading assumptions; disallow exits crossing the year boundary. The audit now reports both year-reset and continuous-warm-up trade counts, and peer gross return on identical entry/exit dates.

| Year | Pattern | Year reset -> continuous trades | New early-January signals | Continuous mean target net | Target minus peers gross |
|---|---|---:|---|---:|---:|
| 2024 | B_breakdown / 5 | 4 -> **5** | Jan 22 | **+1.521%** | +3.236 pp |
| 2025 | B_breakdown / 5 | 5 -> **6** | Jan 2 | +0.428% | +0.036 pp |
| 2025 | F_tight_lowvol / 5 | 4 -> **6** | Jan 2, Jan 20 | -0.366% | -0.558 pp |
| 2026 | D_breakout / 5 | 4 -> **6** | Jan 6, Jan 23 | -0.205% | -1.204 pp |
| 2026 | G_upper_wick / 3 | 9 -> **10** | Jan 26 | -2.057% | -0.383 pp |

**Five affected year-pattern groups, seven additional pattern-level trades.** The 2026 n=6 breakout and n=10 upper-wick counts were already included in prior 2025–26 joint-panel results; this audit mainly corrects the comparability of the 2024/2025 single-year tests. For 2024 breakdown, the previously reported n=4/+0.829% must not be used as the final continuous-lookback statistic.

Run: `python skills/stock-personality-analysis/cross_year_audit_v08.py --daily 600031_2023_Q4_five_stock_daily.csv 600031_2024_five_stock_daily.csv 600031_2025_2026_peer_daily.csv --out 600031_cross_year_v08.json`.

The tool is read-only and checks five-stock calendar alignment, duplicates across files and SHA256 of each input. Three self-contained CI tests cover early-January signal preservation, peer-calendar rejection and overlapping-source rejection. Actual full-data JSON/CSV and hashes are archived in Drive, not committed to GitHub.

**Limitations:** incomplete 2023 history, no independent dividend cashflows or true price-limit/suspension fill model, minute history only 9 sessions, gross peer relative return is not net alpha, all historical years are post-exploratory. No confirmed stable stock-specific edge. Keep PR #118 draft. The first prospective holdout can only begin with newly validated production data for signals dated 2026-10-26 onward.
