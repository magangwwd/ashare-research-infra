# PA-v2 first execution: causality foundations (2026-10-29)

Scope: implement the first bounded software batch from the audited plan, without changing old four patterns, original 60 trades, production data, workflows, or main. Keep PR #118 draft.

New core: `research/price_action_v2_core.py`. New tests: `tests/test_price_action_v2_core.py`.

Implemented: strict swing highs/lows with two-bar right confirmation by default, separate event/confirmation time, no invented executable fill time, finite consistent OHLC and ordered timezone-aware completed-bar input, zero-range ratios as None, exclusion of development labels ending at/after test start, deterministic protocol hash.

Ten synthetic offline regression tests actually ran in the local Windows Python 3.14 environment: `python -m unittest discover -s tests -p test_price_action_v2_core.py -v`, exit 0, 10 passed. Checks include delayed confirmation, historical prefix invariance after appending/mutating future prices, tied extrema, zero-range candles, invalid input, train/test label boundaries and rule hashing.

The test file is placed under existing Pipeline CI path filters so a PR synchronize event can run the existing complete pytest suite. Cloud result must be read from the exact final commit SHA's Actions jobs/logs; local success alone is not cloud success. This document intentionally records only the pre-submit local result, not a future CI assertion.

Limitations: synthetic data only; input completion must be verified by caller against an exchange calendar/clock. No weekly aggregation, real snapshot audit, three-event protocol freeze, price-action scoring, fill simulator or historical profitability run is implemented in this batch. The hash proves identity, not pre-registration. No claim of blind holdout or full PA-v2 research pipeline deployment.

Next bounded batch: pre-register candidate/confirmation/failure/expiry definitions for the three PA-v2 events and connect this core to verified daily inputs plus an immutable run receipt; do not relabel inspected historical outcomes as prospective. Read the 20261029 execution plan for data and execution gates.
