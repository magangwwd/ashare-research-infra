# 600031 forward holdout freeze v0.5 (2026-10-29)

Status: exploratory research, **NOT** a validated strategy. This document freezes the next test before examining new post-2026-10-23 outcomes.

## Data boundary
- Development/exploration: 2025-01-02 through 2026-10-23, source snapshot 2026-10-24T211915.119571_0000-7c30e07d. These dates are fully contaminated by earlier exploratory rule selection and must never be labeled blind holdout.
- Prospective holdout: signals from **2026-10-26 onward** only when a newly validated production Drive snapshot is published. Do not use staging/backfill without independent validation. Store exact snapshot, manifest SHA256, daily/minute coverage and immutable run receipts.
- 2023/2024 partitions are visible in the production Drive snapshot but were not extracted or evaluated in this version. They can be retrospective external stress tests, **not forward holdout**.
- Before scoring any holdout: verify corporate actions, adjustment convention, dividend cashflows, suspensions, price-limit tradability and 15-minute completeness. If any critical check fails, mark result non-executable.

## Frozen hypotheses and outcome rules
- Use unchanged v0.4 signal code, with its definitions and lookback windows, for four preselected patterns: D_breakout (5 sessions), G_upper_wick (3), B_breakdown (5), F_tight_lowvol (5).
- A signal at close t can use information only through t. Entry: next-session open; exit: open h sessions after entry. Reject unfillable or unverified windows; never assume a limit-up/down fill.
- No overlapping positions within a pattern. Maintain 100-share board lots, nominal 40,000 CNY, each-side commission 0.03%, sell stamp 0.05%, each-side slippage 0.05%; also evaluate realistic minimum commission and liquidity constraints.
- Compare 600031 against four unchanged peers 000157.SZ, 000425.SZ, 600761.SH, 601100.SH, on **identical entry/exit dates**; report gross and net performance separately, dividend-adjusted when source permits.
- Track trade count, 3/5-day returns, mean/median alpha, win rates, maximum adverse excursion, uncertainty intervals, signal-month diversity, and all rejected fills.

## Decision gates
1. Do not infer an edge from fewer than 20 non-overlapping prospective episodes per tested pattern, nor from fewer than four distinct calendar quarters. If insufficient, report 'not enough new evidence'.
2. Report the full pre-registered family of four hypotheses (and any extra explorations separately), not just the best performer. Apply multiple-testing correction, and blocked/time-cluster uncertainty where feasible.
3. Require directionally stable peer-relative performance after costs and corporate-action/fill checks across more than one market environment. An interval overlapping zero means no confirmed stock-specific edge.
4. If tests fail, publish a negative result and preserve the Skill as an analytical tool rather than an automated trading recommendation.
5. Keep PR #118 draft and do not modify Task01 production workflows or data until the above are independently validated.

## Current blockers
- Official production pointer still 2026-10-24; critical_history_complete=false.
- Minute sample only 9 sessions / 153 bars from 2026-10-13 to 23; three earlier October dates absent.
- Five missing pre_close rows in the extracted five-stock CSV, all on 2026-09-30; source-vs-extraction root cause not verified.
- Historical dividend and price-limit/suspension execution constraints not independently reconciled.
