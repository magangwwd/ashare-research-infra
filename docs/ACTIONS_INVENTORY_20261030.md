# GitHub Actions root workflow inventory — 2026-10-30

Source: live `main` directory `.github/workflows/` (read 2026-10-30 10:30 Beijing). This is a **configuration inventory**, not proof of successful production runs. 

## Summary
- Root workflow files: **55**; workflows with `schedule`: **11**; no schedule: **44**.
- No root workflow YAML contains the literal stock code **600031** (all 55 YAMLs inspected); hence **no 600031-specific GitHub schedule identified**. Do not disable shared A-share Task01 or Gold workflows to stop this research. GPT's dedicated 600031 automation was paused separately.
- The stock code **600351** occurs in separate K-line research workflows; **600351 ≠ 600031**. Do not confuse them.
- Financial recruitment `finance-jobs-daily.yml` and `finance-jobs-pages.yml` no longer have `schedule`; manual / PR validation remains.

## Scheduled workflows (UTC cron, 11 files)
| Workflow | Cron(s), UTC | Purpose / safeguard |
| --- | --- | --- |
| `astock-quant-drive-first.yml` | `50 15 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-hourly-research-cycle.yml` | `23 * * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-okx-freshness-watchdog.yml` | `47 */4 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-okx-market-archive.yml` | `7 */4 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-regime-router.yml` | `50 2 * * *; 50 8 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-research-scheduler-guard.yml` | `07 */2 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-v3-forward.yml` | `41 2 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `btc-v3-watchdog.yml` | `13 * * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `gdrive-auth-healthcheck.yml` | `17 1 * * 1` | Keep until run evidence and dependency audit confirm safe consolidation |
| `overseas-data-collector.yml` | `30 22 * * *; 15 23 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |
| `task01.yml` | `17 13 * * *; 30 15 * * *; 23 19 * * *` | Keep until run evidence and dependency audit confirm safe consolidation |

## All root workflow files (55)
| Workflow | Automatic cron? |
| --- | --- |
| `ai-research-data-only.yml` | No |
| `astock-quant-ci.yml` | No |
| `astock-quant-drive-first.yml` | Yes |
| `astock-quant-legacy-drive.yml` | No |
| `astock-quant-rank-watchlist.yml` | No |
| `bci-drive-collector.yml` | No |
| `bci-drive-read-smoke.yml` | No |
| `bci-pages.yml` | No |
| `bci-public-http-smoke.yml` | No |
| `btc-hourly-research-cycle.yml` | Yes |
| `btc-okx-freshness-watchdog.yml` | Yes |
| `btc-okx-l2-schema-audit.yml` | No |
| `btc-okx-market-archive.yml` | Yes |
| `btc-okx-official-catalog.yml` | No |
| `btc-okx-official-l2-pilot.yml` | No |
| `btc-regime-router.yml` | Yes |
| `btc-research-scheduler-guard.yml` | Yes |
| `btc-v3-backtest.yml` | No |
| `btc-v3-cutoff-backfill.yml` | No |
| `btc-v3-daily-alternative.yml` | No |
| `btc-v3-forward.yml` | Yes |
| `btc-v3-watchdog.yml` | Yes |
| `etf-research-one-shot.yml` | No |
| `fina-indicator-conflict-audit.yml` | No |
| `finance-jobs-daily.yml` | No |
| `finance-jobs-pages.yml` | No |
| `finance-jobs-public-smoke.yml` | No |
| `finance-jobs-qq-html-probe.yml` | No |
| `five-clean-backfill.yml` | No |
| `five-clean-pr-check.yml` | No |
| `five-stock-decision.yml` | No |
| `five-stock-llm-shadow.yml` | No |
| `five-stock-morning-packet.yml` | No |
| `five-stock-pre-move-kline.yml` | No |
| `four-stock-volatility-compare.yml` | No |
| `gdrive-auth-healthcheck.yml` | Yes |
| `kline-600351-15m-bridge.yml` | No |
| `kline-600351-3y.yml` | No |
| `kline-600351-baostock-probe.yml` | No |
| `kline-600351-volatility-15m.yml` | No |
| `kline-600351-volatility-regime.yml` | No |
| `mainboard-swing-unattended-health.yml` | No |
| `manual-five-ai-review-pack.yml` | No |
| `overseas-data-collector.yml` | Yes |
| `paper-forward-r0-manual.yml` | No |
| `pipeline-ci.yml` | No |
| `publish-verified-five-finance.yml` | No |
| `r1-five-after-gold.yml` | No |
| `research-ci.yml` | No |
| `task01-backfill.yml` | No |
| `task01.yml` | Yes |
| `vol-market-pre20-panel.yml` | No |
| `volatility-pre20-deep.yml` | No |
| `watchdog-alert.yml` | No |
| `watchdog-ci.yml` | No |

## Latest concrete run evidence sampled
- Overseas scheduled [run 40989642152](https://github.com/magangwwd/ashare-research-infra/actions/runs/40989642152): both `safety-and-parser-tests` and `collect-and-publish` jobs succeeded, one evidence artifact. This does not independently prove Google Drive snapshot readback.
- Task01 [run 40948715414](https://github.com/magangwwd/ashare-research-infra/actions/runs/40948715414): task01 job in progress at inspection, `drive-publish` skipped at that moment; newer [run 40972437556](https://github.com/magangwwd/ashare-research-infra/actions/runs/40972437556) pending, no jobs yet. No successful Drive publication claim.
- A-share offline multi-role [run 40954899551](https://github.com/magangwwd/ashare-research-infra/actions/runs/40954899551): unit tests succeeded, Drive job failed because verified A-pool had **4** names while requested cap was **5**; draft fix [PR #134](https://github.com/magangwwd/ashare-research-infra/pull/134) adds capped-count checks and offline regression tests, not deployed to main.
- BTC native daily [run 40976764671](https://github.com/magangwwd/ashare-research-infra/actions/runs/40976764671) on unreviewed feature branch succeeded; PR #129 flags unintended credential-bearing execution, not evidence of credential leak. Independent security review required before merge.

## Remaining gates
1. Confirm every enabled workflow's latest three runs, job status, artifact, failure and source date; check duplicate trigger paths and time windows.
2. Verify data publisher Drive manifest/current pointer and rollback with actual readback, not job exit code alone.
3. For BTC, do not disable collection, forward monitoring or security guards merely because cron overlaps. Resolve PR #129 privileged non-main trigger risk separately.
4. After verified consolidation, update operational guide/Skill and issue #128; stop only the temporary GPT consolidation task.
