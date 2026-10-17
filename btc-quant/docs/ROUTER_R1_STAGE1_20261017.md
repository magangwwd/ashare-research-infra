# BTC Router-R0/R1 Stage 1: audited research run (2026-10-17)

## Grounded storage and execution
- Code and jobs: `magangwwd/ashare-research-infra`, `btc-quant/`, `.github/workflows/btc-regime-router.yml`.
- Existing Drive ROOT ONLY: `BTC_Quant_System_OKX` (folder ID `1f1QpjccD0YvciS7RwaxfRGkuW-o0k_0P`).
- Existing verified V3 archival child: `BTC_V3_第三轮回测_20261014` (folder ID `1obKT63RgrH3OBK8TieLwlj5ovzvQQiGQ`). Read-only research input.
- No extra Drive folder, no rewrite, each record uses `btc_router_{UTC_ASOF}__run_{GITHUB_RUN_ID}__{filename}`. SHA256SUMS saved with relative file names. GitHub artifacts are an additional 30-day mirror; only Drive is intended long-term evidence.
- V3 pinned forward pipeline/commit `d60c667b3654dbfb00efea9bc72241cca3bb8d5c` is NOT changed; A-share jobs untouched. Research-only; no API keys, actual orders, leverage or shorts.

## Explicit status and baseline hierarchy
- `router_legacy_20261016.py`: original unmodified artifact for behavioral review.
- `router.py` updated: read production grouped JSON, exact 2-of-3 / 1-of-3 vote thresholds, TRANSITION cash 0.
- `validate.py`: conditional daily strategy return joins lag confirmed regime to FOLLOWING return date. It is only descriptive. Original 20-day future outcome cannot become an online feature.
- `r1_experiment.py`: new separate continuous spot account with research HMG/V3 routing, one account cash/coin/fees/HWM across folds and 1x / 2x costs. NEVER interpret daily proxy as intraday grid execution.

## Pre-registered phase-1 candidates
- Router-R0: corrected router, 730d training regime coverage for candidate HMG eligibility, fixed mapping and standard 8/4/15 central account protections. No breakout veto.
- Router-R1_PRICE: R0 + fixed range lower, one complete close below lower -> WATCH, >0.5 basket-entry ATR below lower OR two complete closes below -> EXIT_PENDING. WATCH blocks additional HMG buys; exit models at next recorded open.
- Router-R1: R1_PRICE + early WATCH if price within 0.5 ATR above frozen lower AND (last-day volume / prior 20-day avg >=1.5 OR 20d/60d realized volatility ratio >=1.4).
- Parameters 0.5 ATR, 1.5x volume and 1.4x relative volatility are new unvalidated research hypotheses. They are NOT optimized or safe stop guarantees.
- Fixed basket low is the prior 30 daily lows EXCLUDING the candle triggering basket creation; frozen ATR also recorded. Re-entry requires at least three consecutive closes back above original lower (and no warning). A signal is never intraday protection.
- RANGE is not blanket permission: each fold requires >=120 RANGE days and >=3 episodes in its *preceding* 730 days to run the HMG daily-proxy candidate. This is a minimum evidence threshold, NOT an HMG admission certificate; actual strategy approval remains NOT_APPROVED.
- HMG study uses total account cap 25%, 5 weights (1,1.15,1.25,1.35,1.50), spacing clamp(0.9 ATR,1.8%,3.5%), basket gross TP trigger 0.8%, at most one modeled layer per UTC day. All buying and selling modeled next-open at 0.15%/0.30% one-way costs; no synthetic fills.
- V3 monthly/weekly frozen comparison is separate; research router uses Monday UTC (Monday Beijing 08:00) V3 weight formula with top-level risk checks, defense cap 25% once.
- Global 8% REDUCED: liquidate half outstanding BTC exactly once and deny new grid; recover only on Monday UTC if prior completed close drawdown <=4%; global 15% HALTED: liquidate at next recorded open, never auto resume. Gaps can exceed 15% before liquidation. Shared HWM never reset.
- Risk exits are more urgent than Monday entry or ordinary 7-day confirmation. Ordinary router-state machine itself retains confirmation/minimum hold rules.
- HIGH_VOL/TREND_DOWN/TRANSITION -> cash as candidates; RECOVERY -> V3_DEFENSIVE only (NOT HMG).
- R2 funding/OI; R3 point-in-time ETF/macro: NOT_RUN pending source availability and timestamp audit.

## Rolling evaluation and limitations
- Outer folds: 730 preceding completed daily candles = training observation window, following 90 daily sessions = test, refit coverage every 90 days. Start after index 910 (180 initial regime feature warm-up + 730 observations). Final fold may be partial and is clearly marked by end date.
- Training only determines whether HMG candidate has minimum episode coverage. **No training-period profit-based mapping optimization or parameter selection is performed.** This is a valid rolling holdout evaluation of pre-registered deterministic logic, NOT proof of an optimized adaptive selector.
- Test account cash/coin/grid layers, state, and HWM continue across boundaries. Next-open historical fill model applies to ALL strategy transfers and costs. Stats export per fold and one continuous NAV.
- `stage1_summary.json` reports three variants x 1x/2x; `*_equity.csv`, `*_trades.csv`, `*_events.csv`, `*_folds.csv`, `fold_boundaries.csv`.
- Outcomes from 2020-2026 are retrospective OOS protocol folds, NOT genuinely new independent market data. The stage-1 paired benchmarks include **V3_STATIC**, **VOL_STATIC** (no trend vote, weekly risk scaling) and **CASH_STATIC**, all under identical fold dates, initial cash and friction assumptions, in addition to the independent frozen V3 diagnostics. These comparisons are not yet volatility/risk-matched; no claim of positive alpha over frozen V3 is implied.
- Benchmark promotion requires paired common-period comparison, many-fold risk improvement, positive net OOS and risk-matched superiority, independent forward verification, and separate high-frequency HMG fill reliability. **Not claimed by this stage.**

## Daily run and issue visibility
Run is scheduled UTC 02:50 = Beijing 10:50, or via GitHub Actions workflow_dispatch. Latest closed UTC day is previous UTC calendar date. Data from Binance Vision original daily ZIP is only processed if complete/verified. The archived next UTC daily open is historical modeling ONLY, not a real-time executable order. If archive not yet published, fail with missing-data status; do not impute.
1. Complete `unittest discover` on Python 3.13.
2. Read pinned historical base from the existing Drive child; validate SHA256.
3. Extend original complete daily candles to most recently completed UTC date, reject gaps/errors.
4. Router, independent HMG 1x/2x, existing frozen-code-style V3 research diagnostics, descriptive summary.
5. Run `r1_experiment.py` historical fold comparison.
6. Upload artifacts and append immutable, run-ID-specific outputs to the existing Drive root and verify with rclone. The evidence includes full validated daily BTC OHLCV, the latest genuinely fetched official daily ZIP, config snapshot, provenance, source freshness manifest, per-fold holdings/trades/events, summary and relative SHA256SUMS. No separate Drive folder.

Never report unit-test PASS as end-to-end PASS, and never report artifacts as uploaded unless the Drive check succeeds. Open blockers must be recorded without fabricated performance statistics.
