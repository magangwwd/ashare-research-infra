# OKX market data archival — Actions-only

This job samples OKX public REST books5, recent trades and ticker three times at 15-second intervals, every four hours (UTC). Schedule delays and missed runs are possible. It is NOT continuous L2 or complete trade history.

Official historical data portal: https://www.okx.com/historical-data . Historical backfill is NOT implemented: first verify actual archive URLs, downloadable instruments, field schemas, licenses, and checksums. The catalog only probes the portal and never claims a successful historical download.

Uses the existing GDRIVE_RCLONE_CONFIG_B64 secret and gdrive:BTC_Quant_System_OKX/ folder. Each run uploads to an immutable run-ID folder and verifies bytes by downloading them. Workflow failure should trigger GitHub notifications. Missing scheduled runs require an independent freshness watchdog, not yet implemented.

Do not use sparse snapshots to reconstruct order-book events, queue positions, or execution fills. Mark missing intervals as unknown rather than forward-filling them.
