# 600031 v1.1 dividend cash-flow & opening-fill audit (2026-10-29)

**Retrospective only; frozen 60-trade schedule, not prospective holdout or executable trading proof.**

Read the validated Google Drive production pointer `Ashare_Quant/current.json`: version `2026-10-24T211915.119571_0000-7c30e07d`, `critical_history_complete=false`. There is **no validated new snapshot** for post-2026-10-23 holdout.

Source: 755 aligned 600031 sessions, 2023-09-06 to 2026-10-23; five-stock daily 3,775 rows. Four historical dividend ex-dates were checked against `previous close - ex-date pre_close` and published distribution records:
- 2024-06-21: CNY 0.22/share, record date 2024-06-20;
- 2025-06-11: CNY 0.36/share, record date 2025-06-10;
- 2025-10-15: CNY 0.31/share, record date 2025-10-14;
- 2026-07-17: CNY 0.18/share, record date 2026-07-16.

The account held **2,600 shares** on 2024-06-20 and **2,200 shares** on 2025-10-14; it was flat on the other two record dates. Gross cash dividend entitlement is **CNY 1,254** (572+682), credited after ex-date opening trades. This includes the 2024-06-21 ex-date opening sale: entitlement survives that sale.

| 40k CNY single account | Ending equity | Cumulative return | Max daily drawdown |
| --- | ---: | ---: | ---: |
| v1.0 excluding dividends | 43,760.76 | +9.4019% | -25.5043% |
| v1.1 gross dividends | **44,749.72** | **+11.8743%** | -24.3356% |
| v1.1 flat 20% dividend tax sensitivity | **44,481.33** | **+11.2033%** | -24.6606% |

Difference is not equal to CNY 1,254 because subsequent board-lot sizing changes. The 20% assumption is not verified broker withholding; the simulation must not be described as after-tax realized profit.

All 120 assumed next-open entry/exit observations have matching source daily open prices. Among those dates: 0 missing pre_close, 0 opens within 0.2 percentage points of a +/-10% boundary, 0 one-price days. **This is a heuristic screening only**, not a verified fill. Validated production `adj_factor`, `dividend`, and `stk_limit` Parquet partition SHA256s were checked, but the runner had no Parquet decoder, so their rows were not audited. Full suspension, limit queue and adjustment factor checks remain incomplete. The historical signal schedule was NOT regenerated on adjusted prices.

Code: `dividend_cash_v11.py`; offline synthetic tests: `tests/test_stock_personality_dividend_v11.py`. Run against the archived v1.0 CSVs and v0.9 schedule:

```bash
python skills/stock-personality-analysis/dividend_cash_v11.py --audit 600031_portfolio_overlap_v09.json --daily 600031_2023_Q4_five_stock_daily.csv 600031_2024_five_stock_daily.csv 600031_2025_2026_peer_daily.csv --events skills/stock-personality-analysis/dividend_events_v11.json --out 600031_dividend_v11.json
```

The **full** data, source hashes, separate 120-open audit CSV, dividend cash ledger, equity curves, original reproducibility script and 6 local tests are archived at:
`Google Drive / Ashare_Quant / stock-personality-research / 600031_股票性格研究_v1.1_分红现金流与成交审计.zip`.

Sources: [SSE 2025 interim dividend announcement](https://big5.sse.com.cn/site/cht/www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-09-30/600031_20250930_OHZY.pdf), [distribution history](https://stock.quote.stockstar.com/dividend_600031.shtml). Open data quality defect: [Issue #119](https://github.com/magangwwd/ashare-research-infra/issues/119).

**Do not merge this draft into main until adjusted-price signals, verified fills and genuinely new forward holdout pass.**
