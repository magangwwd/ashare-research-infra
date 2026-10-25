# ETF-DIV-004 evidence gate (2026-10-23)

Status: REVIEW_REQUIRED; NOT_FOR_TRADING. This note is not an acceptance receipt.

- Draft PR #92 contains corporate-action audit code, ledger tests, and issuer evidence; review all changes before merge.
- 512100.SH 2022-09-02 registered-share conversion uses per-account ceiling rounding; do not infer portfolio P&L from aggregate fund shares.
- Dividend entitlement, ex-date NAV recognition, payment availability, independent market/fund_adj reconciliation and issuer coverage require scoped tests and review.
- The authoritative ETF queue remains feature/etf-single-rotation-v1/etf_single_rotation/research_backlog.json. ETF-PAPER-005 must not run until ETF-DIV-004 is independently verified.
- Do not trigger full historical one-shot solely to test this documentation update. Run scoped CI on the PR, verify checkout SHA and artifact hashes, then check Drive readback before considering VERIFIED_DONE.
