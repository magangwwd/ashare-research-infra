# Mainboard probability V7 migration (2026-11-07)

Status: **staged in GitHub branch; not deployed and no scheduled trading/paper signals**.

Original project: Google Drive `Ashare_Quant/mainboard_prob_selection_20261107`.
Historical research baseline: V7; 2020-01–2026-11-06; 150 trades; +58.60% cumulative; 9.77% maximum drawdown; worst single-account trade -8.22%. These are legacy research results, **not independent validation**. No live trades are authorized.

Production runtime configuration: `PROB_GOLD_ROOT`, `PROB_AQ_ROOT`, `V7_LIVE_DIR`, `PROB_REQUIRE_OFFICIAL_CALENDAR=1`. Legacy scripts were migrated for reproducibility; `core.py`, `09_weekly_signal.py` and `11_forward_ledger.py` define the active V7 paper protocol. Existing historical parquets and models remain on Google Drive; no source market data or account artifacts are checked in.

Required acceptance gates:
1. Public repo code and static tests pass, with no data or secrets committed.
2. Authenticated rclone read-only preflight verifies original Gold and project folder IDs and latest manifest coverage.
3. On a provisioned runner, re-run `10_replay_check.py` using exact historical cached inputs and compare 150 trades, selection and corrected equity; preserve evidence.
4. Verify model/input SHA, daily data coverage, formal trade-calendar coverage, quarterly model freeze, corporate actions and external fee assumptions.
5. Dry-run a simulated future week with original ledger, then separately verify isolated Drive upload + hash/readback; only after acceptance consider switching production pointer and enabling schedule.

Never treat a CI green status as proof of strategy profitability. Do not rewrite or delete the original Drive source. Any failure blocks publication. Time zone Asia/Shanghai.
