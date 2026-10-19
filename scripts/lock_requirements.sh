#!/usr/bin/env bash
# Regenerate every hash-pinned requirements file from its .in source.
#
#   pip install uv        # once
#   scripts/lock_requirements.sh
#
# Edit the ranges in the .in files, rerun this script, review the diff and commit
# both. Workflows install the locked .txt files, so every run uses exactly the
# versions (and file hashes) that were tested. Each lock is resolved for the
# Python version of the workflows that install it.
set -euo pipefail
cd "$(dirname "$0")/.."

lock() {  # lock <python-version> <input.in> <output.txt>
  uv pip compile "$2" --python-version "$1" --universal --generate-hashes --quiet -o "$3"
  echo "locked $3 ($(grep -cE '^[A-Za-z0-9_.-]+==' "$3") packages)"
}

# Task 01 / research engine (Python 3.12 workflows)
lock 3.12 requirements.in requirements.txt

# ai_research and other pandas-only jobs (Python 3.13 workflows)
lock 3.13 requirements/research-pandas2.in requirements/research-pandas2.txt
lock 3.13 requirements/research-pandas2-tushare.in requirements/research-pandas2-tushare.txt
lock 3.13 requirements/research-pandas3.in requirements/research-pandas3.txt

# astock-quant (Python 3.13 workflows)
lock 3.13 astock-quant/stock_state_strategy/requirements.in astock-quant/stock_state_strategy/requirements.txt
lock 3.13 astock-quant/kronos_return_top3/requirements.in astock-quant/kronos_return_top3/requirements.txt
