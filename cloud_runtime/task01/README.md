# Task 01 cloud recovery snapshot

This is a preserved copy of the Codex cloud runtime, not a replacement for the repository's production `data_pipeline/`.

- Runtime: **CPython 3.12**. The `recovered_oct08/*.bin` artifacts are checksum-pinned original local module/test caches. They are required recovery dependencies, not reviewable Python source. Replace them with reviewed source before approving production handoff.
- Scope: existing datasets from 2020-01-01; 15-minute history from 2026-01-01. This snapshot's full-universe/minute-derived daily behavior differs from the newer mainboard-only policy in `main`. Do not silently replace either policy.
- No data, local checkpoints, live PID/status files, OAuth credentials or tokens are included.
- The nested `.github/workflows/` files are **test fixtures**, not executable GitHub workflows. The only new root workflow tests this snapshot offline and has no schedule or secrets.
- Historical completion has not been certified by this code export. GitHub Actions daily publication must wait for the handoff gates in `../../docs/task01-actions-handoff-plan.md`.

Offline verification, from the repository root:

```bash
cd cloud_runtime/task01
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

The original cloud's full-backfill entry point is `python -m data_pipeline.run_task01 --mode full-backfill --config config/config.yaml --root /explicit/state/root`. Do not launch a second writer or point an offline check at production. A live backfill is **not** started by CI. Runtime/watchdog helpers expect a project-local `.venv`.

Use `docs/prompts/task01-actions-daily-handoff.md` in the repository root for the next implementation task. The daily handoff is a separate, reviewable change, not an automatic consequence of merging this backup.
