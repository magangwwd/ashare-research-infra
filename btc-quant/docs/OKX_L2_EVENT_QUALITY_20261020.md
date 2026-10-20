# OKX BTC-USDT-SWAP official L2 400-level — full-day quality report

## Verifiable execution

- Date/source: UTC **2026-10-10**, `BTC-USDT-SWAP-L2orderbook-400lv-2026-10-10.tar.gz` from OKX official public archive, internal JSONL.
- Raw archive remains PRIVATE in Google Drive. SHA256: `829b1393dcf7b9072383092f620039ba15b8be0d7e0fd422e9b66a8afd8b5a7e`, size 172,934,071 compressed bytes; prior tar and Gzip CRC validated.
- [Schema probe Actions #39356751911](https://github.com/magangwwd/ashare-research-infra/actions/runs/39356751911): action/asks/bids/instId/ts, 1,343 complete records in a 512KiB sample.
- [Full-day audit Actions #39356981273](https://github.com/magangwwd/ashare-research-infra/actions/runs/39356981273): 8 focused offline tests; live audit processed **7,069,214** parseable events:
  - 96 snapshots, 7,069,118 updates
  - earliest timestamp `2026-10-10T00:00:00Z`; latest `2026-10-10T23:59:59.991Z`
  - 1,440/1,440 UTC minutes had at least one event
  - longest *positive adjacent* timestamp interval 0.736 seconds
  - no out-of-order timestamp anomalies, no same-millisecond adjacent ties, no both-sides-empty updates, no crossed top-of-book on records tagged snapshot, and no out-of-day records were recorded in the counters
  - audit has **not** proven no dropped exchange events or correct sequential orderbook reconstruction.
- The quality JSON report and 512KiB schema JSON report were uploaded privately to `.../2026-10-10/quality/run_39356981273_1`, and checked with `rclone check --download`: 2 matching files, 0 differences.

## What the metrics do NOT prove

1. A gap-free 1,440-minute time span is not a cryptographic or sequence-number proof of lossless L2 message coverage. Source JSON only exposed `action,bids,asks,instId,ts` in sampled schema. No explicit exchange sequence number was verified.
2. No independent orderbook reconstruction, cross-snapshot consistency test, trading fill/queue replay, or strategy profitability comparison has been completed.
3. One UTC day audited, not a monthly/annual backfill. The REST sparse snapshots cannot be substituted for official 400-level depth.
4. OKX historical data terms/API agreement restrict download to individual research/trading strategy development and prohibit public redistribution or commercial resale without written authorization.

## Next acceptance

- Reconstruct deterministic book snapshots applying increments in observed order, quantify mid/spread/level invariants and compare each successive independent snapshot to the reconstruction; tolerate only documented reset semantics, otherwise mark unknown.
- Build Drive-backed resumable chunk transport for official archive backfills; cap daily size, retry, remote SHA256 and immutable artifacts; never backfill decades blindly.
- Define PIT/WF backtesting with protective regime routing and explicit separate market microstructure validation before applying to HMG/V3.
