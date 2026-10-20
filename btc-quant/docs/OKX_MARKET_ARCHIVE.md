# OKX historical market-data archive — GitHub Actions only

## Scope and nonclaims

- Sparse public REST order-book data: 50 levels per query, three samples per job at ~15-second spacing, with BTC-USDT and BTC-USDT-SWAP ticker / trades.
- These samples are NOT a continuous depth feed. No order-book updates between samples, no queue position, no truthful L2 reconstruction or realistic fill replay.
- REST `/api/v5/market/history-trades` pages cover a provider-limited rolling ~3-month window. This job downloads at most five 100-trade pages each run; it is a bounded partial **trade sample**, not complete tick-by-tick trade history or a historical order-book.
- The official portal <https://www.okx.com/historical-data> advertises separate historical trade, OHLC, funding and high-resolution L2 downloads. **Those full portal archives are not yet downloaded by this pipeline**: their actual file URLs, instrument coverage, licensing and checksums require separate verification.
- Missing periods stay unknown. Never forward-fill missing depth or trade ticks as if observed.

## Persistence and verification

- Workflow: `.github/workflows/btc-okx-market-archive.yml`; intended every 4 hours once merged to the default branch. GitHub schedule is best-effort and may be delayed.
- Snapshots are uploaded before potentially long historical REST pagination, to immutable per-run directories: `gdrive:BTC_Quant_System_OKX/market_data/OKX/sparse_snapshots/run_<run_id>_<attempt>`. `rclone check --download` validates actual remote bytes.
- Trade pages and checkpoint: `gdrive:BTC_Quant_System_OKX/market_data/OKX/history_trades/BTC-USDT-SWAP/`. Each execution restores the remote directory and verifies it byte-for-byte before attempting the next pages.
- New script validates instrument IDs, trade IDs, strictly decreasing cursor, page continuity and SHA256 checkpoint consistency; it atomically persists pages then checkpoints. On restart it validates an orphan page and resumes without overwriting. The old live checkpoint format is migrated only after all stored pages and checksum verify.
- GitHub Artifact temporarily retains a separate snapshot copy (14 days); Drive is the intended long-term archive.
- The independently scheduled `btc-okx-freshness-watchdog.yml` checks latest successful archive run age (<=6h), rereads that run's actual Drive files, verifies SHA256 and sample count, and opens a deduplicated GitHub Issue on fault. It checks sparse snapshots, not complete market-data coverage.

## Real evidence on feature branch (2026-10-20 UTC)

- `39354336561`: first live Actions run completed; OKX API sampling succeeded. Drive check returned 0 differences / 3 matching snapshot files, and 0 differences / 6 matching history files (5 trade pages + checkpoint).
- `39354781426`: independent watchdog run completed; it read 18 sampled rows from Drive, verified SHA256 and a ~0.07h age.
- After adding crash-safe checkpoint schema, runs using the old checkpoint initially failed closed; the schema migration was subsequently implemented. **Do not claim the migration validated until a newer run successfully resumes.**
- Feature-branch test and merge state must be verified separately. A green snapshot workflow never means complete historical L2 backfill.

## Next engineering gates

1. Prove legacy checkpoint migration and multi-run append with verified Drive checksum.
2. Demonstrate stale/missing/corrupt record alarm and deduplicated Issue lifecycle (without damaging production archive).
3. Validate PR, merge only after checks pass, and observe a default-branch scheduled run.
4. Research direct official historical portal archive links/terms/structure, backfill as provenance-tagged separate immutable datasets, and document uncovered date ranges.
5. Build daily data QA metrics (sample age, instrument completeness, missing calendar windows, provider anomalies) before relying on any market microstructure strategy study.
