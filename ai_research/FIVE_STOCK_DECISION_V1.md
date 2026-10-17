# Fixed five-stock morning assessment V1 (paper-only)

Tracking: `600351.SH, 002107.SZ, 600641.SH, 002568.SZ, 002296.SZ`.

## Source of truth and why a new isolated pipeline is needed

- Frozen R1: `astock-quant/stock_state_strategy/paper_forward_watchlist_r1.py`; not edited.
- New synthesis: `python -m ai_research.five_stock_decision`; read-only R1 and strict 5-stock Clean acceptance, no brokerage.
- Financial input: `gdrive:stock_state_strategy/finance_clean_five/latest.json` and `versions/<run>`, verified using the same strict partition SHA, 5-code, announcement-date checks as `manual_ai_review_pack.verified_finance`. Initial accepted version `five-clean-38220271682` covers 2026Q1/H1 × five symbols × four datasets (40 records). This is **not all historical financials**. Investments and financing cashflow fields are null in the targeted finance subset; report flags them.
- Separate source A-pool top-five workflow `ai-research-data-only.yml` remains unchanged and **does not** count as fixed five research.
- Deterministic two-round debate is not an LLM and cannot fabricate news. News absence is explicitly missing.
- Original `LOCK_MARKUP_SINGLE_PAPER_V1` is out of scope.

## Run and archive

`.github/workflows/five-stock-decision.yml` runs unit tests on PR. On main it watches the **completed successful R1 workflow_run**, rather than racing the nightly Gold job. It verifies Gold manifest and R1 manifest checksum, reads the immutable R1 day snapshot, restores immutable Clean 40/40 version, runs `five_stock_decision.py`, and publishes to:

`gdrive:stock_state_strategy/output/five_stock_decision_v1/YYYY-MM-DD/`

- `report.json`: five individual facts, accepted financial fields, cited F IDs, rules-based 2-round bull/bear/rebuttal, risk verdict, status changes if previous report supplied.
- `report.md`: morning-friendly Chinese view, including WATCH even with 0 candidates.
- `coverage.json`: five codes, finance/news coverage, no LLM or order execution.
- `ab_forward.json`: A original R1 and B R1 with research veto, **unrealized** paper intents; PnL is null until valid future observations.
- `manifest.json`: source and result SHA256 hashes.

No remote file is overwritten. A partially published date blocks workflow rather than masquerading as success. All generated data stay in Drive, never Git.

Manual recovery: GitHub Actions → `Fixed five-stock PIT research after validated R1 (paper only)` → `Run workflow` → exact R1 day; the date must not be in the future of verified Gold. On PR only offline unit tests run.

## Safe output interpretation

`WATCH`: R1 did not qualify (market breadth may block). `NEEDS_REVIEW`: PIT data incomplete or qualifying R1 still requires risk evidence. `AVOID_EXIT_REVIEW`: original R1 candidate and separate severe financial veto; this **is not a retrospective alteration to R1**. `SETUP_PAPER_NEXT_OPEN_UNVERIFIED`: a conditional paper setup only, requires fresh T+1 exchange/price/liquidity checks. No report labels itself an executable order; entries, stops and weights remain null until separate paper execution checks.

Input coverage is limited to 2026Q1/H1 and externally captured PIT announcements. The *capture timestamp* of the finance version is not overwritten with issuer announcement time. Future release dates fail closed. A true dated historical backtest must only use data captured and available by each relevant date.

## Optional actual LLM debate

`five_stock_llm_shadow.py` is a separate tool. It is disabled unless the operator explicitly enables paid API inference, provides `OPENAI_API_KEY` as a repository secret, a selected supported model ID, and `--max-requests 25`.

`.github/workflows/five-stock-llm-shadow.yml` is **manual only**, defaults to `enable_paid_api=false` (free offline contract test). If manually enabled, it performs five model calls for each of five stocks (bull, bear, bull rebuttal, bear rebuttal, risk manager), archives output by GitHub run ID in Drive under `five_stock_llm_shadow_v1`, and marks all results `UNVALIDATED_LLM_SHADOW_NO_ENTRY_AUTHORIZATION`. It does not run TradingAgents, change R1, or change B gate. Costs/availability depend on API account and chosen model. Tests mock all 25 requests, not a paid/live API validation.

## A/B comparative research

`five_stock_ab_evaluate.py` computes an **observed-fill trade-proxy** only when provided documented T+1 open, later exit, same-basis prices, exchange/entry risk checks, and source hashes. The original R1 candidate set is A; B can only be a subset cleared by the dated research rule review. Both use the same fills, minimum commissions, frozen one-sided 0.2% slippage, 100-share lot, 10% cap and 0.25% equity risk proxy. It reports win rate, trade proxy return and proxy drawdown, with explicit limits. This **is not** an audited portfolio backtest and does not infer missing future fills. Until real unseen-period observations exist, returns and strategy superiority are unknown.

Use `python -m unittest -v ai_research.tests.test_five_stock_decision ai_research.tests.test_five_stock_llm_shadow ai_research.tests.test_five_stock_ab_evaluate` to run all new no-network invariant tests.

## Acceptance left before production declaration

1. Merge only after PR offline tests succeed.
2. Manually dispatch once on main for 2026-10-16 and verify the resulting Drive folder and rclone byte checks (real Actions secrets/Drive authority required).
3. Verify a true later trading day after nightly Gold/R1, ensure **five of five** regardless of zero candidates, and compare news & financial PIT available timestamps.
4. Confirm 08:00 ChatGPT task reads `five_stock_decision_v1` first and labels any unmerged/unavailable record NEEDS_REVIEW. Enable and verify phone/email notifications separately.
5. Collect unseen forward trade outcomes; a 0-candidate day can validate report/coverage but **cannot validate A/B performance**.
