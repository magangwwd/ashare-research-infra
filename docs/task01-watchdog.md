# Task 01 External Watchdog

GitHub Actions `task01.yml` is the data producer. The independent Cloudflare watchdog is a fallback dispatcher, **not** a separate data writer. Configuration in Git does not prove that the Cloudflare Worker has been redeployed or that its Cron Triggers are active.

## Timing (Asia/Shanghai, UTC+08)

- GitHub native `task01.yml`: **21:17**, backup **23:30**, next-day backup **03:23**. The matching UTC cron entries are `17 13 * * *`, `30 15 * * *`, and `23 19 * * *`.
- The checked-in `watchdog/wrangler.toml` declares **00:20, 01:20, 02:20, 06:20** the next local day, with UTC cron entries `20 16 * * *`, `20 17 * * *`, `20 18 * * *`, `20 22 * * *`.
- The checks are intended to be idempotent across midnight. **These are repository settings, not verified deployed schedules.** Confirm Cloudflare Cron Trigger deployment/execution separately before treating watchdog recovery as operational.
- GitHub schedule is best-effort and may arrive late. A missing run at one slot is not by itself proof that the workflow is disabled.

## State machine

For the applicable data-session window (including the cross-midnight backups):

1. A managed Task 01 run already succeeded → do nothing.
2. A managed run is queued/in progress → do nothing.
3. No managed run exists → dispatch `task01.yml` with `watchdog_trigger=true`.
4. The managed run failed/cancelled on attempt 1 → rerun the same workflow run once.
5. Attempt 2 also failed → dispatch `watchdog-alert.yml`, which creates one deduplicated GitHub Issue.
6. No third automatic retry is attempted.

A normal manual workflow dispatch is deliberately ignored by the external watchdog state machine, so ad-hoc auth/Drive/manual work cannot satisfy the daily watchdog by accident.

## Duplicate protection

A delayed native GitHub schedule and an external watchdog dispatch can race. The main workflow therefore has a `daily-guard` job.

Before a scheduled/watchdog data run starts its expensive data job, the guard checks whether another successful run from the same **18:00-to-18:00 local data session** already produced `task01-validation-report`. If so, the delayed duplicate is skipped. Drive-auth-only and Drive-publish-only runs do not produce that artifact and cannot suppress the daily data task.

The workflow-level concurrency group `task01-data` remains enabled with `cancel-in-progress: false`, so two Task01 producers cannot run concurrently. A successful validation artifact does **not** prove that Drive publication and production pointer readback succeeded.

## Cloudflare deployment

Files:

- `watchdog/worker.mjs` — Cloudflare Worker entrypoint.
- `watchdog/decision.mjs` — idempotent state machine.
- `watchdog/wrangler.toml` — checked-in four-trigger configuration.
- `watchdog/wrangler.toml.example` — example aligned with the checked-in configuration.
- `watchdog/decision.test.mjs` — state-machine tests.

Use a fine-grained GitHub token restricted to this repository with **Actions: Read and write**. No Contents, Issues, or repository administration write permission is required by the Worker. The alert workflow itself uses GitHub's short-lived repository token to create the Issue.

Store the token only as the Cloudflare Worker secret `GITHUB_TOKEN`; never commit it to this repository.

For an authorized deployment, inspect the existing `watchdog/wrangler.toml` and deployed Worker configuration first; do **not** overwrite it with an outdated example. Deployment requires a separately authenticated Cloudflare environment and appropriate authorization. After deployment, verify the actual Cloudflare Cron Triggers and a real scheduled invocation, then verify the resulting GitHub Run ID and Drive publication independently. A GitHub commit or successful Worker HTTP probe alone does not prove scheduled execution.

The Worker exposes an HTTP handler for manual verification, but the production path is the Cron Trigger. A successful HTTP call returns a decision such as `done`, `wait`, `dispatch`, `rerun`, or `alert`.

## Security boundary

The external token can only trigger/re-run Actions. It cannot read or modify Google Drive credentials, Tushare credentials, repository contents, or Issues. Tushare and Google Drive secrets remain only inside GitHub Actions.
