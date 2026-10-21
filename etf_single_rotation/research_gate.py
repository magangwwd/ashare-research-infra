"""Deterministic, conservative ETF research gate (never an order gateway).

Check completed pipeline outputs and numeric stress criteria. Genuine total
return/dividend semantics and untouched prospective evidence remain manual
acceptance prerequisites, so research never self-certifies as trade-ready.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
SF = RESULTS / "self_financing"
MANIFEST_PATH = RESULTS / "research_acceptance_gate.json"
ETF_SYMBOLS = {
    "510300.SH", "510500.SH", "159915.SZ", "510050.SH",
    "510880.SH", "518880.SH", "512100.SH", "512690.SH",
}
REQUIRED_REPORTS = (
    RESULTS / "data_quality_summary.json",
    RESULTS / "execution_accounting_summary.json",
    SF / "self_financing_manifest.json",
    SF / "executable_walk_forward_summary.json",
    SF / "cost_stress_manifest.json",
    SF / "cost_stress_executable_walk_forward.csv",
    SF / "benchmark_self_financing_manifest.json",
    SF / "benchmark_self_financing_comparison.csv",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate(root: Path = ROOT) -> dict:
    """Compute a machine-readable review state and fail on missing core outputs."""
    results = root / "results"
    sf = results / "self_financing"
    paths = tuple(
        (root / p.relative_to(ROOT)) for p in REQUIRED_REPORTS
    )
    missing = [str(p.relative_to(root)) for p in paths if not p.is_file()]
    if missing:
        raise ValueError(f"Required research evidence missing: {missing}")
    quality = json.loads((results / "data_quality_summary.json").read_text("utf-8"))
    etfs = set(quality.get("expected_symbols", ()))
    if etfs != ETF_SYMBOLS or quality.get("symbols") != 8 or quality.get("missing_symbols"):
        raise ValueError("Expected ETF universe is not exactly the frozen eight")
    cost = pd.read_csv(sf / "cost_stress_executable_walk_forward.csv")
    fee_col = "fee_slippage_per_side"
    if fee_col not in cost or "max_drawdown" not in cost or "cagr" not in cost:
        raise ValueError("Cost stress results lack required numeric metrics")
    for fee in (0.001, 0.003, 0.005):
        matching = cost[(cost[fee_col] - fee).abs() < 1e-10]
        if len(matching) != 1:
            raise ValueError(f"Expected one actual cost-stress fold result at {fee}")
    cost_summary = []
    for row in cost.to_dict("records"):
        cost_summary.append({
            "fee_slippage_per_side": float(row[fee_col]),
            "cagr": float(row["cagr"]),
            "max_drawdown": float(row["max_drawdown"]),
            "passes_numeric_history_filter": bool(
                row["max_drawdown"] >= -0.25 and row["cagr"] > 0
            ),
        })
    blockers = []
    if quality.get("acceptance") != "PRELIMINARY_PASS_NOT_STRATEGY_ACCEPTANCE":
        blockers.append("HISTORICAL_ETF_DATA_REVIEW_REQUIRED")
    if int(quality.get("missing_etf_session_records", -1)) > 0:
        blockers.append("UNRESOLVED_ETF_SESSION_GAPS")
    benchmark_manifest = json.loads(
        (sf / "benchmark_self_financing_manifest.json").read_text("utf-8")
    )
    benchmark = pd.read_csv(sf / "benchmark_self_financing_comparison.csv")
    if benchmark_manifest.get("status") != "BENCHMARK_EXPLORATORY_NOT_FOR_TRADING":
        raise ValueError("Benchmark manifest must remain exploratory")
    if set(benchmark["benchmark"]) != {
        "cash_zero_interest", "etf_buy_hold_510300.SH", "etf_buy_hold_518880.SH"
    }:
        raise ValueError("Missing or unexpected approved comparison benchmarks")
    blockers.extend([
        "DIVIDEND_TOTAL_RETURN_NOT_INDEPENDENTLY_VALIDATED",
        "CANDIDATE_SELECTION_WAS_RETROSPECTIVELY_EXPLORED",
        "NO_UNTOUCHED_FORWARD_PAPER_HISTORY",
        "SAME_SOURCE_BENCHMARKS_NOT_EXTERNALLY_VERIFIED",
        "MODELLED_TRANSACTION_COST_NOT_LIVE_EXECUTION",
    ])
    if not all(r["passes_numeric_history_filter"] for r in cost_summary):
        blockers.append("HISTORICAL_DRAW_DOWN_OR_RETURN_STRESS_FILTER_FAILED")
    return {
        "schema_version": 1,
        "project": "ETF_SINGLE_ROTATION",
        "required_symbols": sorted(ETF_SYMBOLS),
        "input_data_last_date": quality.get("last_date"),
        "data_quality": quality.get("acceptance"),
        "historical_max_drawdown_numeric_target": -0.25,
        "cost_stress": cost_summary,
        "benchmark_status": benchmark_manifest["status"],
        "benchmark_names": sorted(benchmark["benchmark"].tolist()),
        "status": "NOT_FOR_TRADING",
        "full_acceptance": False,
        "open_blockers": blockers,
        "files": {
            str(p.relative_to(root)): sha256(p) for p in paths
        },
        "next_work": [
            "Verify five ETF/date gaps against exchange notices and independent source.",
            "Audit ETF distribution / corporate-action and true total-return semantics.",
            "Run benchmark comparison and freeze a genuinely prospective paper strategy.",
        ],
        "warning": "Passing historical numeric drawdown checks never guarantees future drawdown, profitability, or trading suitability.",
    }


def main():
    result = evaluate()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print("ETF_RESEARCH_GATE " + json.dumps({
        "status": result["status"], "data_quality": result["data_quality"],
        "open_blockers": result["open_blockers"],
        "cost_stress": result["cost_stress"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
