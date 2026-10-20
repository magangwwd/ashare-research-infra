# BTC OKX official historical 400-level L2: one-day pilot (2026-10-20)

## Grounded real verification

- Official history metadata API `GET /api/v5/public/market-data-history` for `module=4`, `instType=SWAP`, `instFamilyList=BTC-USDT`, `dateAggrType=daily`, `begin=end=1791590400000` returned exactly one matched `BTC-USDT-SWAP` date, 2026-10-10 UTC.
- [Read-only catalog Actions #39355769353](https://github.com/magangwwd/ashare-research-infra/actions/runs/39355769353): real query succeeded, 4 unit tests, Google Drive byte check: one matching metadata file, zero differences.
- [Bounded official day download #39356335318](https://github.com/magangwwd/ashare-research-infra/actions/runs/39356335318): official file `BTC-USDT-SWAP-L2orderbook-400lv-2026-10-10.tar.gz`; full 172,934,071 compressed bytes via validated HTTP Range 206, 1 tar member, 1,697,232,486 uncompressed bytes. Full gzip trailer CRC and safe tar validation PASS.
- Archive SHA256: `829b1393dcf7b9072383092f620039ba15b8be0d7e0fd422e9b66a8afd8b5a7e`.
- Archived private Google Drive path `BTC_Quant_System_OKX/market_data/OKX/official_portal/L2_400lv/BTC-USDT-SWAP/2026-10-10/run_39356335318_1/`: 2 files matching, zero byte differences after `rclone check --download`.
- GitHub only retains small audit manifest, NOT the raw official archive.

## Evidentiary limits

1. Verified compressed-file integrity and Drive copying; **not verified internal message schema, timestamp coverage, event continuity, order book reconstruction, or microstructure replay suitability**.
2. Just **one UTC day**, NOT historical completeness. In particular sparse 50-level REST captures are separate; do not blend with genuine official 400-level L2 or assume they form continuous depth.
3. No recurring bulk L2 download is enabled, no live trading. Max compressed pilot file 200 MiB; unknown date/filename/URL rejected.
4. Current pilot supports resume within the SAME runner via HTTP Range of aligned partial bytes. Cross-run partial L2 resume still requires a verified chunk-manifest / remote checkpoint workflow, and should not be claimed.
5. Official OKX terms restrict historical market-data access to individual strategy research. Redistribution and commercial data products require written permission; do **not** make the ZIP or underlying records public. See https://www.okx.com/en-ae/help/historicaldata-terms-and-conditions and https://www.okx.com/en-us/help/okx-api-agreement .

## Next acceptance gates

- [ ] Schema and market-data integrity audit of the actual 2026-10-10 stream without publishing raw data.
- [ ] Validate exchange UTC boundaries, event timestamp monotonicity, duplicates, order-book sequence fields, bid/ask invariants and sequence gaps if supplied by provider.
- [ ] Develop safe multi-run, Drive-backed L2 part/chunk checkpoint with per-file SHA256, not unconditional 165-MB redownloads.
- [ ] Resolve legitimate rights before any commercial reuse; keep personal research data private.
- [ ] Pilot second day **only after** validation and storage quota checks.
