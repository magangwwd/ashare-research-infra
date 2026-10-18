# OKX market data archival — Actions-only

This job samples OKX public REST books5, recent trades and ticker three times at 15-second intervals, every four hours (UTC). Schedule delays and missed runs are possible. It is NOT continuous L2 or complete trade history.

Official historical data portal: https://www.okx.com/historical-data . Historical backfill is NOT implemented: first verify actual archive URLs, downloadable instruments, field schemas, licenses, and checksums. The catalog only probes the portal and never claims a successful historical download.

Uses the existing GDRIVE_RCLONE_CONFIG_B64 secret and gdrive:BTC_Quant_System_OKX/ folder. Each run uploads to an immutable run-ID folder and verifies bytes by downloading them. Workflow failure should trigger GitHub notifications. Missing scheduled runs require an independent freshness watchdog, not yet implemented.

Do not use sparse snapshots to reconstruct order-book events, queue positions, or execution fills. Mark missing intervals as unknown rather than forward-filling them.

## Follow-up implementation (2026-10-18)
- Sparse REST depth increased to 50 levels; still NOT a continuous order-book feed.
- `okx_history_trades.py` downloads a bounded number of pages from the public last-three-months trade history endpoint. Its checkpoint is local to the job directory; to resume ACROSS GitHub runners, restore the checkpoint and pages from verified Drive storage first. This workflow integration is NOT done.
- `btc-okx-freshness-watchdog.yml` separately checks the age of the most recent successful archive workflow. It detects missing success, not direct Drive freshness; it is only active after merging into the default branch.
- Historical portal L2 files, cross-run resume, actual Drive upload and API success have NOT been validated.
- The workflow may fail on regional OKX API restrictions or Drive permission; do not call it production-ready before an actual run.
