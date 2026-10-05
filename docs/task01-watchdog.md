# Task 01 External Watchdog

GitHub's built-in `schedule` remains as a fallback, but it is no longer the only trigger.

## Timing

- GitHub native schedule: 20:17 Asia/Shanghai / Singapore.
- External watchdog checks: 20:40, 21:40, 22:40.
- The checks are idempotent. They do not blindly create three Task 01 runs.

## State machine

For the current Asia/Shanghai day:

1. A managed Task 01 run already succeeded → do nothing.
2. A managed run is queued/in progress → do nothing.
3. No managed run exists → dispatch `task01.yml` with `watchdog_trigger=true`.
4. The managed run failed/cancelled on attempt 1 → rerun the same workflow run once.
5. Attempt 2 also failed → dispatch `watchdog-alert.yml`, which creates one deduplicated GitHub Issue.
6. No third automatic retry is attempted.

A normal manual workflow dispatch is deliberately ignored by the external watchdog state machine, so ad-hoc auth/Drive/manual work cannot satisfy the daily watchdog by accident.

## Duplicate protection

A delayed native GitHub schedule and an external watchdog dispatch can race. The main workflow therefore has a `daily-guard` job.

Before a scheduled/watchdog data run starts its expensive data job, the guard checks whether another successful run from the same local day already produced `task01-validation-report`. If so, the delayed duplicate is skipped. Drive-auth-only and Drive-publish-only runs do not produce that artifact and cannot suppress the daily data task.

The existing workflow-level concurrency group remains enabled, so two data jobs cannot execute simultaneously.

## Cloudflare deployment

Files:

- `watchdog/worker.mjs` — Cloudflare Worker entrypoint.
- `watchdog/decision.mjs` — pure idempotent state machine.
- `watchdog/wrangler.toml.example` — three daily Cron Triggers.
- `watchdog/decision.test.mjs` — state-machine tests.

Create a fine-grained GitHub personal access token limited to this repository with **Actions: Read and write**. No Contents, Issues, or repository administration write permission is required by the Worker. The alert workflow itself uses GitHub's short-lived repository token to create the Issue.

Store the token only as the Cloudflare Worker secret `GITHUB_TOKEN`; never commit it to this repository.

Example deployment with Wrangler:

```bash
cd watchdog
cp wrangler.toml.example wrangler.toml
npx wrangler secret put GITHUB_TOKEN
npx wrangler deploy
```

The Worker exposes an HTTP handler for manual verification, but the production path is the Cron Trigger. A successful HTTP call returns the decision such as `done`, `wait`, `dispatch`, `rerun`, or `alert`.

## Security boundary

The external token can only trigger/re-run Actions. It cannot read or modify Google Drive credentials, Tushare credentials, repository contents, or Issues. Tushare and Google Drive secrets remain only inside GitHub Actions.
