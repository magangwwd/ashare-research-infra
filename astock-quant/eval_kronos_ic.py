"""Evaluate Kronos mini rank IC on A-share daily data.

For each historical signal_date, run Kronos mini --predict-only (with data
truncated at that date), compute actual 20-day forward returns from daily_data/,
then report Spearman rank IC.  Results are cached under
kronos_return_top3/output/ic_eval/ so subsequent runs skip already-computed
periods.

Usage:
    python eval_kronos_ic.py                      # 6 periods, 20-day interval
    python eval_kronos_ic.py --periods 2          # quick sanity check
    python eval_kronos_ic.py --force-rerun        # ignore cache
    python eval_kronos_ic.py --end-date 2026-03-28
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DAILY_DATA_DIR = BASE_DIR / "daily_data"
KRONOS_OUTPUT_DIR = BASE_DIR / "kronos_return_top3" / "output"
IC_CACHE_DIR = KRONOS_OUTPUT_DIR / "ic_eval"
LGBM_METRICS_CSV = BASE_DIR / "lightgbm_return_top3" / "output" / "validation_metrics.csv"
REFERENCE_STOCK = "000001.SZ"


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Evaluate Kronos mini rank IC on A-share data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--periods", type=int, default=6,
                   help="Number of historical periods to evaluate (default 6)")
    p.add_argument("--interval-days", type=int, default=20,
                   help="Trading-day interval between signal dates (default 20)")
    p.add_argument("--end-date", type=str, default=None,
                   help="Last signal_date YYYY-MM-DD (default: today minus 21 trading days)")
    p.add_argument("--hold-days", type=int, default=20,
                   help="Forward holding days matching Kronos prediction window (default 20)")
    p.add_argument("--lookback", type=int, default=512,
                   help="Kronos context window (default 512)")
    p.add_argument("--batch-size", type=int, default=32,
                   help="Kronos inference batch size (default 32)")
    p.add_argument("--output-prefix", type=str, default="mini_full_h20_lb512",
                   help="Base prefix for cached prediction files (default mini_full_h20_lb512)")
    p.add_argument("--min-obs", type=int, default=100,
                   help="Minimum stocks with valid actual returns to compute IC (default 100)")
    p.add_argument("--force-rerun", action="store_true",
                   help="Re-run Kronos inference even if cache exists")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-batch Kronos output")
    return p


# ── Trading calendar ─────────────────────────────────────────────────────────

def load_trading_dates() -> pd.DatetimeIndex:
    path = DAILY_DATA_DIR / f"{REFERENCE_STOCK}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Reference stock CSV not found: {path}. "
            "Ensure daily_data/ is populated."
        )
    df = pd.read_csv(path, usecols=["Date"])
    dates = pd.to_datetime(df["Date"]).sort_values().reset_index(drop=True)
    return pd.DatetimeIndex(dates)


def nearest_past_trading_date(
    target: pd.Timestamp,
    trading_dates: pd.DatetimeIndex,
) -> pd.Timestamp:
    candidates = trading_dates[trading_dates <= target]
    if candidates.empty:
        raise ValueError(f"No trading date on or before {target.date()}")
    return candidates[-1]


def default_end_date(trading_dates: pd.DatetimeIndex, hold_days: int) -> pd.Timestamp:
    today = pd.Timestamp.today().normalize()
    past = trading_dates[trading_dates < today]
    needed = hold_days + 1
    if len(past) < needed:
        raise ValueError("Not enough trading history to compute a complete forward window.")
    return past[-(needed)]


def enumerate_signal_dates(
    trading_dates: pd.DatetimeIndex,
    end_date: pd.Timestamp,
    periods: int,
    interval_days: int,
) -> list[pd.Timestamp]:
    try:
        idx = trading_dates.get_loc(end_date)
    except KeyError:
        raise ValueError(
            f"end_date {end_date.date()} is not a trading day. "
            "Pass a valid trading day or omit --end-date."
        )
    result: list[pd.Timestamp] = []
    for i in range(periods):
        pos = idx - i * interval_days
        if pos < 0:
            break
        result.append(trading_dates[pos])
    return list(reversed(result))


# ── Kronos inference (cached) ─────────────────────────────────────────────────

IC_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_path_for(prefix: str, signal_date: pd.Timestamp) -> Path:
    date_str = signal_date.strftime("%Y-%m-%d")
    return IC_CACHE_DIR / f"{prefix}_{date_str}_predictions.csv"


def run_kronos_for_date(
    signal_date: pd.Timestamp,
    prefix: str,
    hold_days: int,
    lookback: int,
    batch_size: int,
    force_rerun: bool,
    quiet: bool,
) -> Path | None:
    """Run Kronos mini full-universe predict-only with data cut at signal_date.
    Returns path to the cached predictions CSV, or None on failure.
    """
    dest = cache_path_for(prefix, signal_date)
    if dest.exists() and not force_rerun:
        return dest

    date_str = signal_date.strftime("%Y-%m-%d")
    run_prefix = f"ic_eval_{prefix}_{date_str}"

    cmd: list[str] = [
        sys.executable, "-m", "kronos_return_top3",
        "--predict-only",
        "--use-mini",
        "--full-universe",
        f"--hold-days={hold_days}",
        f"--lookback={lookback}",
        f"--batch-size={batch_size}",
        f"--output-prefix={run_prefix}",
        f"--signal-date={date_str}",
    ]
    if quiet:
        cmd.append("--quiet")

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    print(f"  [kronos] signal_date={date_str} … (this may take 30-60 min on CPU)")
    result = subprocess.run(cmd, cwd=str(BASE_DIR), env=env)
    if result.returncode != 0:
        print(f"  [error] Kronos inference failed for {date_str}", file=sys.stderr)
        return None

    raw = KRONOS_OUTPUT_DIR / f"{run_prefix}_predictions_latest.csv"
    if not raw.exists():
        print(f"  [error] Expected output not found: {raw}", file=sys.stderr)
        return None

    raw.rename(dest)
    return dest


# ── Actual 20-day forward return ──────────────────────────────────────────────

def compute_forward_returns(
    ts_codes: list[str],
    signal_date: pd.Timestamp,
    trading_dates: pd.DatetimeIndex,
    hold_days: int,
) -> pd.Series:
    """Return a Series indexed by ts_code with actual forward returns.

    actual_return = exit_close / entry_open - 1
      entry_open  = open price on the 1st trading day after signal_date
      exit_close  = close price on the hold_days-th trading day after signal_date
    """
    from TechnicalPatternScanner.utils import load_one  # type: ignore

    future = trading_dates[trading_dates > signal_date]
    if len(future) < hold_days:
        print(
            f"  [warn] Only {len(future)} trading days after {signal_date.date()}, "
            f"need {hold_days}. IC will be NaN."
        )
        return pd.Series(np.nan, index=ts_codes, dtype=float)

    entry_date = future[0]
    exit_date = future[hold_days - 1]

    rets: dict[str, float] = {}
    for ts_code in ts_codes:
        df = load_one(
            ts_code,
            start_date=str(entry_date.date()),
            end_date=str(exit_date.date()),
        )
        if df is None or df.empty:
            continue
        entry_rows = df[df.index == entry_date]
        exit_rows = df[df.index == exit_date]
        if entry_rows.empty or exit_rows.empty:
            continue
        entry_open = float(entry_rows["Open"].iloc[0])
        exit_close = float(exit_rows["Close"].iloc[0])
        if entry_open <= 0:
            continue
        rets[ts_code] = exit_close / entry_open - 1.0

    return pd.Series(rets, dtype=float)


# ── IC calculation ────────────────────────────────────────────────────────────

def spearman_rank_ic(
    predicted: pd.Series,
    actual: pd.Series,
    min_obs: int = 100,
) -> float:
    merged = pd.DataFrame({"pred": predicted, "actual": actual}).dropna()
    if len(merged) < min_obs or merged["pred"].nunique() < 10:
        return np.nan
    r_pred = merged["pred"].rank(method="average")
    r_act = merged["actual"].rank(method="average")
    return float(r_pred.corr(r_act))


# ── LightGBM IC baseline ──────────────────────────────────────────────────────

def load_lgbm_ic() -> tuple[float | None, str]:
    if not LGBM_METRICS_CSV.exists():
        return None, "validation_metrics.csv 不存在"
    df = pd.read_csv(LGBM_METRICS_CSV, encoding="utf-8-sig")
    sel = df[df.get("is_selected", pd.Series(False, index=df.index)) == True]  # noqa: E712
    if sel.empty:
        return None, "无 is_selected=True 行"
    row = sel.iloc[0]
    ic = float(row["mean_rank_ic"])
    scope = str(row.get("scope", "unknown"))
    template = str(row.get("template", "unknown"))
    return ic, f"scope={scope}, template={template}"


# ── Weight recommendation ─────────────────────────────────────────────────────

def suggest_weights(mean_ic: float) -> dict[str, float]:
    if np.isnan(mean_ic) or mean_ic < 0.02:
        w_kronos, w_lgbm = 0.10, 0.40
    elif mean_ic < 0.04:
        w_kronos, w_lgbm = 0.15, 0.35
    else:
        w_kronos, w_lgbm = 0.25, 0.25
    return {
        "_W_FINANCIAL": 0.25,
        "_W_PAST": 0.20,
        "_W_LGBM": w_lgbm,
        "_W_KRONOS": w_kronos,
        "_W_AGREEMENT": 0.05,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = build_parser().parse_args()
    trading_dates = load_trading_dates()

    if args.end_date:
        end_ts = nearest_past_trading_date(
            pd.Timestamp(args.end_date), trading_dates
        )
        if end_ts != pd.Timestamp(args.end_date):
            print(f"[eval_ic] --end-date {args.end_date} 非交易日，使用 {end_ts.date()}")
    else:
        end_ts = default_end_date(trading_dates, args.hold_days)

    print(
        f"[eval_ic] end_date={end_ts.date()}  periods={args.periods}  "
        f"interval={args.interval_days}d  hold_days={args.hold_days}"
    )

    signal_dates = enumerate_signal_dates(
        trading_dates, end_ts, args.periods, args.interval_days
    )
    if not signal_dates:
        print("[eval_ic] 没有足够的历史交易日，请减少 --periods 或 --interval-days")
        sys.exit(1)

    print(f"[eval_ic] 共 {len(signal_dates)} 个 signal_date：", end=" ")
    print("  ".join(d.strftime("%Y-%m-%d") for d in signal_dates))
    print()

    ic_records: list[dict] = []

    for sig_date in signal_dates:
        date_str = sig_date.strftime("%Y-%m-%d")
        print(f"── {date_str} ──")

        pred_csv = run_kronos_for_date(
            sig_date,
            args.output_prefix,
            args.hold_days,
            args.lookback,
            args.batch_size,
            args.force_rerun,
            args.quiet,
        )
        if pred_csv is None:
            print(f"  跳过（推理失败）")
            continue

        pred_df = pd.read_csv(
            pred_csv, encoding="utf-8-sig",
            usecols=["ts_code", "predicted_return"],
        )
        if pred_df.empty:
            print("  跳过（预测文件为空）")
            continue
        print(f"  已加载预测 {len(pred_df)} 只")

        print(f"  计算实际 {args.hold_days}D 收益…")
        actual = compute_forward_returns(
            pred_df["ts_code"].tolist(),
            sig_date,
            trading_dates,
            args.hold_days,
        )

        n_obs = int(actual.notna().sum())
        ic = spearman_rank_ic(
            pred_df.set_index("ts_code")["predicted_return"],
            actual,
            min_obs=args.min_obs,
        )
        print(f"  IC={ic:.4f}  n_obs={n_obs}/{len(pred_df)}")
        ic_records.append({
            "signal_date": date_str,
            "ic": ic,
            "n_obs": n_obs,
            "n_pred": len(pred_df),
        })

    print()
    if not ic_records:
        print("[eval_ic] 无有效 IC 记录，检查数据范围或增加 --periods")
        sys.exit(1)

    ic_df = pd.DataFrame(ic_records)
    valid_ic = ic_df["ic"].dropna()
    mean_ic = float(valid_ic.mean()) if len(valid_ic) > 0 else np.nan
    ic_ir = float(valid_ic.mean() / valid_ic.std(ddof=1)) if len(valid_ic) > 1 else np.nan

    lgbm_ic, lgbm_desc = load_lgbm_ic()

    print("=" * 55)
    print("Kronos Mini IC 评估报告")
    print("=" * 55)
    print(ic_df.to_string(index=False))
    print()
    print(f"mean_IC   = {mean_ic:.4f}")
    print(f"IC_IR     = {ic_ir:.4f}")
    print(f"有效期数  = {len(valid_ic)}/{len(ic_df)}")
    if lgbm_ic is not None:
        print(f"LightGBM mean_rank_IC = {lgbm_ic:.4f}  ({lgbm_desc})")
        print(f"Kronos / LightGBM IC 比值 = {mean_ic / lgbm_ic:.3f}")
    print()

    weights = suggest_weights(mean_ic)
    print("── 权重建议 ──────────────────────────────────────────")
    for k, v in weights.items():
        marker = "  ← 已调整" if k in ("_W_LGBM", "_W_KRONOS") and v != 0.25 else ""
        print(f"  {k} = {v:.2f}{marker}")
    print()
    if weights["_W_KRONOS"] != 0.25:
        print(
            f"建议运行：python generate_quality_h20_report.py "
            f"--kronos-weight {weights['_W_KRONOS']:.2f}"
        )
    else:
        print("Kronos IC 充足，保持默认权重（各 25%）。")

    out_path = IC_CACHE_DIR / "ic_evaluation_summary.csv"
    ic_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n[eval_ic] 评估结果已保存：{out_path.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
