# BTC Router Stage-1 verified results, 2026-10-17

## Verdict: R1 NOT APPROVED; historical benefit not demonstrated

Source run: [GitHub Actions #38992243368](https://github.com/magangwwd/ashare-research-infra/actions/runs/38992243368), **unit-tests SUCCESS and daily-shadow SUCCESS**. Verified independent Google Drive readback in EXISTING root `BTC_Quant_System_OKX`:
- [full stage1 summary JSON](https://drive.google.com/file/d/1eanQ9OwkMbRgvpSNakJT8kzKPzBZ8oe9/view)
- [canonical 1d input CSV](https://drive.google.com/file/d/1haojcoxCpfyKX8wNv7a-Y2756eMZrIFn/view)
- [original selected-day ZIP](https://drive.google.com/file/d/11O82AEPlHQySLdsSjhkGZUoNS7WWKDS-/view)
- [freshness evidence](https://drive.google.com/file/d/1fJivyPvU97Vk9PrN_iDBKS2Sg6iLTGN4/view)
- [relative checksum manifest](https://drive.google.com/file/d/17FmEgMAwnn4xNiqLrAEvBJpry7Tjl8w0/view)

Input hash SHA256: `7d9f8128b9da034b7284dd18b53809b06556a81368961ad499bf373a7d00d8c5`.
As of `2026-10-15` UTC because the most recently completed `2026-10-16` original archive was **HTTP 404** when checked; `data_health=STALE_SOURCE_ARCHIVE`. A stale historical result is NOT a real-time signal or an executable next-open price.

Test period: `2022-06-29` to `2026-10-15`, **18 folds** (last fold may be partial). Starting test ledger: 1,000 USDT, no leverage, one shared account, one-way fee+slippage 0.15% or 0.30%. Original 2020-2022 observations feed lookbacks and training, not these reported testing returns.

## Paired retrospective results

| Variant | 1× net return | 1× max drawdown | 2× net return | 2× max drawdown |
|---|---:|---:|---:|---:|
| Router-R0 corrected baseline | +9.02% | 8.96% | +5.75% | 8.82% |
| Router-R1_PRICE | +4.53% | 9.51% | +2.96% | 8.80% |
| Router-R1 volume+vol warning | +4.69% | 9.37% | +3.14% | 8.64% |
| V3_STATIC same-ledger comparator | +29.68% | 8.03% | +28.50% | 8.26% |
| VOL_STATIC (no trend vote) | +51.11% | 13.38% | +50.70% | 13.41% |
| CASH_STATIC | 0% | 0% | 0% | 0% |

R1 − R0 net return = **−4.32 percentage points at 1× cost**, −2.61 pp at 2×. R1 − static V3 = **−24.99 pp at 1×**, −25.36 pp at 2×. V3_STATIC also had lower max DD than R1 in both cost scenarios, so R1 is not better under this historical prototype. VOL_STATIC has materially more drawdown, so its return should not be ranked as risk-matched excess return.

The static V3 comparator is a re-run in this experiment's shared-ledger framework, NOT a substitution for or modification of the independently pinned frozen V3 forward-paper account.

## Implementation scope and NOT_RUN items

Implemented: real nested production config read; 2/3 vote precision; cash transition target=0; corrected next-period regime return joins; legacy router preserved; R1 frozen basket lower, WATCH and EXIT_PENDING, pre-entry breakdown veto; HMG daily proxy no >5 layers; shared cash/coin/fees/HWM; one-shot 8% reduced/Monday 4% resume/15% irreversible halt; 730/90 test fold boundaries with training-only range coverage HMG research eligibility; 1×/2× costs; static V3/vol/cash references; original complete-day archive freshness selection, historical source SHA and immutable Drive run-ID evidence.

Limits: R1 is NOT shown superior; selection relies on training observation coverage, NOT full training-only profit-based adaptive mapping. Risk-matched weight calibration, inner purge optimization, comprehensive confidence intervals/episode events, actual intraday order-fill simulation, derivative point-in-time OI/funding and ETF/macro features are NOT_RUN or PARTIAL. No simulated backtest here is independent forward evidence. HMG remains research candidate, and a RANGE regime alone does not approve it.

**Admission decision**: **NO_PROMOTION** for R1 strategy routing or HMG real trading. Continue frozen V3 independent forward research; retain R0 as reference and R1 as a separately tagged failed-to-beat-baseline hypothesis. Stale data => no new actionable trading recommendation. Any new thresholds must be preregistered and retested on unused prospective observations rather than retrofitted to this result.
