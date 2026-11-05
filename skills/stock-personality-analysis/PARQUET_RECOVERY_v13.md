# 600031 v1.3: Parquet reader recovery (read-only)

## Root cause

The ChatGPT analysis container runs Python 3.13 without `pyarrow`,
`fastparquet`, or `duckdb`. Its `pip install pyarrow` failed with
"No matching distribution found"; this is an environment/package-index issue,
**not evidence of corrupt Parquet**. The GitHub repository already declares
`pyarrow>=17,<24` in `requirements.txt`, and `pipeline-ci.yml` installs it
in the Python 3.12 GitHub Actions runner. Keep these environments distinct.

## Actual production data verification, 2026-10-29

Published validated Drive snapshot:
`2026-10-24T211915.119571_0000-7c30e07d`.

The existing v0.6 research archive included a limited, read-only Parquet
decoder (`read_parquet_lite.py`, `parquet_inspect.py`) using the already
available Thrift and system ZSTD library. It supports single-row-group, flat
ZSTD/dictionary Parquet only; unsupported formats raise exceptions.

Eight clean production partitions were downloaded and decoded locally:
`daily`, `adj_factor`, `stk_limit`, `suspend_d` for both 2026-09 and
2026-10. All eight SHA256 and file sizes matched the published manifest.
For `600031.SH`, there were 33 aligned daily rows (21 September,
12 October); `adj_factor=54.155` on all 33 dates; no recorded suspension
or open/close at price limit on those dates. The 2026-09-30 daily
`pre_close` was null, consistent with Issue #119. The `stk_limit.pre_close`
column was null for all 33 dates and **cannot** be used to fill it.
This is a two-month subset, not a full 2023-2026 execution audit.

Full Chinese report, daily CSV, source file IDs and SHA hashes are archived
in `Ashare_Quant/stock-personality-research`. Production bytes and
`current.json` were not modified.

## Durable GitHub Actions path

Use `parquet_readiness_v13.py` to read a pinned file with pyarrow and check
its SHA256 against the production manifest:

```bash
python -m pip install -r requirements.txt
python skills/stock-personality-analysis/parquet_readiness_v13.py \
  --parquet /path/to/part-000.parquet \
  --sha256 EXPECTED_SHA256 \
  --out parquet_audit.json
```

The synthetic `tests/test_stock_personality_parquet_readiness_v13.py`
writes a real ZSTD Parquet fixture with pyarrow, verifies 600031 row
filtering, checks SHA success/failure, and rejects missing columns.
CI must pass on the new PR head before claiming this path is validated.

**Do not** disable SHA checks, modify production data, or merge PR #118
until the full historical action/limit/fill audit and true forward holdout.
