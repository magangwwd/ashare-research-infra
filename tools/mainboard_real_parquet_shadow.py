"""Read-only *historical diagnostic* for Task01 and Gold daily Parquet.

Does NOT establish point-in-time Gold membership, live data parity, a five-session
shadow test, or permission to publish. Requires locally downloaded inputs.
Never accesses network, GitHub, Google Drive, or production output directories.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path

_FIELDS = ("open", "high", "low", "close", "volume_shares", "turnover_cny")
_TASK_FIELDS = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
_GOLD_FIELDS = {"vendor_symbol", "trade_date", *_FIELDS}
_GOLD_INSTRUMENT_FIELDS = {
    "instrument_id", "vendor_symbol", "board", "exchange", "currency",
    "as_of_date", "list_date", "delist_date",
}


def _day(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time() != datetime.min.time():
            raise ValueError("trade_date timestamp has time/zone")
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("invalid date type")
    # The two sources use ISO YYYY-MM-DD in their normalized Parquet files.
    return date.fromisoformat(value)


def _num(value, field):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field}: missing/boolean numeric")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field}: invalid numeric") from exc
    if not d.is_finite():
        raise ValueError(f"{field}: nonfinite numeric")
    return d


def reproduce_legacy_gold_turnover_cny(task01_turnover_cny):
    """Emulate Gold's float64 *1000 then half-even rounding, diagnostics only.

    Gold's existing historical backfill script uses
    (pd.to_numeric(amount) * 1000).round().astype("Int64").
    Never use this as a reason to suppress strict decimal differences.
    """
    value = _num(task01_turnover_cny, "task01_turnover_cny")
    amount_thousand_cny = float(value / Decimal("1000"))
    if amount_thousand_cny == float("inf") or amount_thousand_cny == float("-inf"):
        raise ValueError("legacy Gold float64 amount overflow")
    return Decimal(round(amount_thousand_cny * 1000))


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_rows(path, required):
    import pyarrow.parquet as pq
    parquet = pq.ParquetFile(str(path))
    available = set(parquet.schema_arrow.names)
    if required - available:
        raise ValueError(f"{path.name}: missing columns: {sorted(required - available)}")
    return pq.read_table(str(path), columns=sorted(required)).to_pylist()


def _verified_mainboard_symbols(gold_manifest, gold_instruments_path):
    """Verify official Gold instruments SHA and use its explicit main-board membership.

    Current instruments list is NOT historical PIT membership. The result is
    diagnostic-only until listing/board transfers have independent PIT proof.
    """
    if gold_instruments_path is None:
        raise ValueError("verified Gold instruments required for MAIN_BOARD_ONLY scope")
    file_contract = gold_manifest.get("files", {}).get("instruments.parquet")
    if not isinstance(file_contract, dict) or not isinstance(file_contract.get("sha256"), str):
        raise ValueError("official Gold instruments digest missing")
    path = Path(gold_instruments_path)
    if _sha(path) != file_contract["sha256"]:
        raise ValueError("official Gold instruments SHA mismatch")
    records = _load_rows(path, _GOLD_INSTRUMENT_FIELDS)
    if len(records) != file_contract.get("rows") or not records:
        raise ValueError("official Gold instrument count mismatch")
    ids, members, listing_windows = set(), set(), {}
    missing_list_dates = 0
    max_as_of = _day(gold_manifest["as_of_date"])
    for row in records:
        if (row["board"] != "MAIN_BOARD" or row["exchange"] not in {"SSE", "SZSE"}
                or row["currency"] != "CNY"):
            raise ValueError("Gold instrument list contains non-main-board A-share security")
        sym, ident = row["vendor_symbol"], row["instrument_id"]
        if (not isinstance(sym, str) or not isinstance(ident, str) or not sym or not ident
                or len(sym) != 9 or not sym[:6].isdigit()
                or (row["exchange"] == "SSE" and not sym.endswith(".SH"))
                or (row["exchange"] == "SZSE" and not sym.endswith(".SZ"))):
            raise ValueError("invalid main-board Gold instrument identity")
        if _day(row["as_of_date"]) > max_as_of:
            raise ValueError("Gold instrument recorded after manifest as_of")
        if sym in members or ident in ids:
            raise ValueError("duplicate Gold instrument identity")
        start = _day(row["list_date"]) if row["list_date"] is not None else None
        end = _day(row["delist_date"]) if row["delist_date"] is not None else None
        if start is not None and end is not None and end < start:
            raise ValueError("Gold instrument invalid listing window")
        if start is None:
            missing_list_dates += 1
        listing_windows[sym] = (start, end)
        members.add(sym)
        ids.add(ident)
    return members, listing_windows, missing_list_dates


def diagnose(pointer, task_manifest_bytes, gold_manifest, task_daily_path,
             gold_daily_path, *, task_relative_path, gold_instruments_path=None, max_examples=20):
    """Compare matched (symbol,date) values only; never return production approval."""
    if isinstance(max_examples, bool) or not isinstance(max_examples, int) or not 1 <= max_examples <= 100:
        raise ValueError("max_examples must be 1..100")
    task = json.loads(task_manifest_bytes)
    if not isinstance(pointer, dict) or not isinstance(task, dict) or not isinstance(gold_manifest, dict):
        raise ValueError("invalid manifest/pointer")
    if pointer.get("validated") is not True or task.get("validated") is not True:
        raise ValueError("Task01 production pointer not validated")
    if _sha_bytes(task_manifest_bytes) != pointer.get("manifest_sha256"):
        raise ValueError("Task01 manifest SHA mismatch")
    for k in ("version", "snapshot_as_of", "source_run_id", "file_count", "total_bytes"):
        if task.get(k) != pointer.get(k):
            raise ValueError("Task01 pointer/manifest mismatch")
    cutoff = min(_day(pointer["snapshot_as_of"]), _day(gold_manifest["as_of_date"]))
    if not isinstance(gold_manifest.get("validation"), dict) or any(
        gold_manifest["validation"].get(k) is not True
        for k in ("primary_keys_unique", "ohlc_valid")
    ):
        raise ValueError("Gold top-level manifest validation failed")
    if not isinstance(task_relative_path, str) or not task_relative_path.startswith(
        "data/clean/daily/year="
    ) or not task_relative_path.endswith("/part-000.parquet"):
        raise ValueError("must select an explicit Task01 clean daily month partition")
    expected = [f for f in task.get("files", []) if f.get("path") == task_relative_path]
    if len(expected) != 1:
        raise ValueError("Task01 partition not uniquely listed in manifest")
    f = expected[0]
    task_daily_path, gold_daily_path = Path(task_daily_path), Path(gold_daily_path)
    task_hash = _sha(task_daily_path)
    if task_hash != f.get("sha256") or task_daily_path.stat().st_size != f.get("bytes"):
        raise ValueError("Task01 clean daily month SHA/size mismatch")
    gold_hash = _sha(gold_daily_path)
    # Gold manifest attests ONLY core snapshot and instruments, not historical month parquet.
    # A local observed hash is NOT an independent official Gold month hash.
    task_raw = _load_rows(task_daily_path, _TASK_FIELDS)
    gold_raw = _load_rows(gold_daily_path, _GOLD_FIELDS)
    mainboard_symbols, listing_windows, missing_list_dates = _verified_mainboard_symbols(
        gold_manifest, gold_instruments_path
    )

    def index(rows, label):
        out = {}
        outside, excluded, unclassified = 0, Counter(), Counter()
        listing_unknown, listing_violations = 0, []
        for r in rows:
            if label == "task":
                sym = r["ts_code"]
            else:
                sym = r["vendor_symbol"]
            if not isinstance(sym, str) or not sym:
                raise ValueError(f"{label}: invalid symbol")
            day = _day(r["trade_date"])
            if day > cutoff:
                outside += 1
                continue
            if sym not in mainboard_symbols:
                if label == "gold":
                    raise ValueError("Gold daily has symbol outside verified main-board list")
                # A present-day Gold list is NOT a historical PIT board registry.
                # In particular, a delisted/former main-board symbol must not
                # be labelled non-main-board just because it is absent today.
                prefix = sym.split(".")[0][:3] + "." + sym.split(".")[-1]
                if sym.endswith(".BJ"):
                    # The Beijing exchange is outside the SSE/SZSE target by
                    # exchange identity, NOT by an assumed numeric prefix.
                    excluded[prefix] += 1
                else:
                    # Includes 300/301/302/688/689 until independent dated
                    # board evidence establishes their historical status.
                    unclassified[prefix] += 1
                continue
            # Only validate numeric fields after scope classification. A raw
            # excluded/unclassified row must not contaminate main-board
            # OHLCV parity or be reported as a missing Gold observation.
            if label == "task":
                vol = _num(r["vol"], "vol") * 100
                amount = _num(r["amount"], "amount") * 1000
            else:
                vol = _num(r["volume_shares"], "volume_shares")
                amount = _num(r["turnover_cny"], "turnover_cny")
            start, end = listing_windows[sym]
            if start is None:
                listing_unknown += 1
            if ((start is not None and day < start)
                    or (end is not None and day > end)):
                listing_violations.append((sym, day.isoformat()))
            key = (sym, day.isoformat())
            if key in out:
                raise ValueError(f"{label}: duplicate symbol/date")
            values = {k: _num(r[k], k) for k in ("open", "high", "low", "close")}
            values.update(volume_shares=vol, turnover_cny=amount)
            if any(values[k] <= 0 for k in ("open", "high", "low", "close")) or (
                values["high"] < max(values[k] for k in ("open", "low", "close"))
                or values["low"] > min(values[k] for k in ("open", "high", "close"))
                or vol < 0 or amount < 0
            ):
                raise ValueError(f"{label}: invalid price/volume")
            out[key] = values
        return out, outside, excluded, unclassified, listing_unknown, listing_violations

    t, task_outside, task_non_mainboard, task_unclassified, t_listing_unknown, t_listing_violations = index(task_raw, "task")
    g, gold_outside, gold_non_mainboard, gold_unclassified, g_listing_unknown, g_listing_violations = index(gold_raw, "gold")
    if gold_non_mainboard or gold_unclassified:
        raise ValueError("Gold must be main-board only")
    common = sorted(t.keys() & g.keys())
    if not common:
        raise ValueError("no overlapping records; do not claim equivalence")
    only_task = sorted(t.keys() - g.keys())
    only_gold = sorted(g.keys() - t.keys())
    mismatches, diffs, per_day = 0, [], {}
    field_counts = Counter({name: 0 for name in _FIELDS})
    nearest_yuan_compatible = 0
    nearest_yuan_incompatible = 0
    nearest_yuan_half_even_compatible = 0
    turnover_within_half_yuan = 0
    turnover_beyond_half_yuan = 0
    turnover_exact_half_yuan_ties = 0
    turnover_half_even_incompatible_examples = []
    turnover_legacy_float_rule_compatible = 0
    turnover_legacy_float_rule_incompatible_examples = []
    for key in common:
        for field in _FIELDS:
            if t[key][field] != g[key][field]:
                mismatches += 1
                field_counts[field] += 1
                if field == "turnover_cny":
                    # Precision hypothesis only; this does not convert or certify a match.
                    nearest = t[key][field].quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    if g[key][field] == nearest:
                        nearest_yuan_compatible += 1
                    else:
                        nearest_yuan_incompatible += 1
                    even_nearest = t[key][field].quantize(
                        Decimal("1"), rounding=ROUND_HALF_EVEN,
                    )
                    if g[key][field] == even_nearest:
                        nearest_yuan_half_even_compatible += 1
                    elif len(turnover_half_even_incompatible_examples) < max_examples:
                        turnover_half_even_incompatible_examples.append({
                            "vendor_symbol": key[0], "trade_date": key[1],
                            "task01_turnover_cny": str(t[key][field]),
                            "gold_turnover_cny": str(g[key][field]),
                            "half_up_yuan": str(nearest),
                            "half_even_yuan": str(even_nearest),
                            "absolute_difference_cny": str(abs(t[key][field] - g[key][field])),
                        })
                    absolute_difference = abs(t[key][field] - g[key][field])
                    if absolute_difference <= Decimal("0.5"):
                        turnover_within_half_yuan += 1
                    else:
                        turnover_beyond_half_yuan += 1
                    if absolute_difference == Decimal("0.5"):
                        turnover_exact_half_yuan_ties += 1
                    legacy_gold = reproduce_legacy_gold_turnover_cny(t[key][field])
                    if g[key][field] == legacy_gold:
                        turnover_legacy_float_rule_compatible += 1
                    elif len(turnover_legacy_float_rule_incompatible_examples) < max_examples:
                        turnover_legacy_float_rule_incompatible_examples.append({
                            "vendor_symbol": key[0], "trade_date": key[1],
                            "task01_turnover_cny": str(t[key][field]),
                            "gold_turnover_cny": str(g[key][field]),
                            "reproduced_legacy_gold_turnover_cny": str(legacy_gold),
                        })
                if len(diffs) < max_examples:
                    diffs.append({
                        "symbol": key[0], "date": key[1], "field": field,
                        "task01": str(t[key][field]), "gold": str(g[key][field]),
                    })
    for label, keys in (("task_rows", t.keys()), ("gold_rows", g.keys()), ("common_rows", common)):
        for sym, day in keys:
            per_day.setdefault(day, {"task_rows": 0, "gold_rows": 0, "common_rows": 0})[label] += 1
    return {
        "schema_version": 1,
        "status": "UNCLASSIFIED_HISTORICAL_UNIVERSE" if task_unclassified else
                  "DIFFERENCES_FOUND" if only_task or only_gold or mismatches else
                  "MATCHED_COMMON_ROWS_DIAGNOSTIC_ONLY",
        "comparison_scope": "MAIN_BOARD_ONLY_VERIFIED_CURRENT_GOLD_INSTRUMENTS_HISTORICAL_PIT_UNVERIFIED",
        "universe_scope": "SSE_SZSE_A_SHARE_MAIN_BOARD_ONLY",
        "gold_instruments_file_verified": True,
        "current_gold_instruments_count": len(mainboard_symbols),
        "gold_instruments_missing_listing_date_count": missing_list_dates,
        "task_rows_with_unknown_listing_start": t_listing_unknown,
        "gold_rows_with_unknown_listing_start": g_listing_unknown,
        "task_rows_outside_recorded_listing_window": len(t_listing_violations),
        "gold_rows_outside_recorded_listing_window": len(g_listing_violations),
        "task_listing_window_violation_examples": [list(k) for k in t_listing_violations[:max_examples]],
        "gold_listing_window_violation_examples": [list(k) for k in g_listing_violations[:max_examples]],
        "recorded_listing_window_is_not_historical_board_pit_proof": True,
        # Only exchange-proven BJ records are excluded as non-SSE/SZSE.
        # Unclassified historical SSE/SZSE rows are NOT Gold missing and are
        # NOT claimed to be non-main-board without dated PIT market evidence.
        "excluded_non_mainboard_rows": sum(task_non_mainboard.values()),
        "task_non_mainboard_rows_excluded": sum(task_non_mainboard.values()),
        "task_non_mainboard_excluded_by_prefix": dict(sorted(task_non_mainboard.items())),
        "unclassified_historical_rows": sum(task_unclassified.values()),
        "unclassified_historical_rows_by_prefix": dict(sorted(task_unclassified.items())),
        "unclassified_historical_rows_are_not_gold_missing": True,
        "non_mainboard_exclusion_basis": "BJ_EXCHANGE_ONLY_NO_NUMERIC_PREFIX_GUESS",
        "tushare_stock_basic_market_mainboard_verified": False,
        "common_cutoff": cutoff.isoformat(),
        "task01_as_of": pointer["snapshot_as_of"],
        "gold_as_of": gold_manifest["as_of_date"],
        "task_month_rows": len(task_raw), "gold_month_rows": len(gold_raw),
        "task_rows_after_cutoff": task_outside, "gold_rows_after_cutoff": gold_outside,
        "task_rows_at_or_before_cutoff": len(t), "gold_rows_at_or_before_cutoff": len(g),
        "common_keys": len(common), "only_in_task01": len(only_task),
        "only_in_gold": len(only_gold), "numeric_field_difference_count": mismatches,
        "field_difference_counts": dict(field_counts),
        "turnover_nearest_yuan_compatible_differences": nearest_yuan_compatible,
        "turnover_nearest_yuan_incompatible_differences": nearest_yuan_incompatible,
        "turnover_nearest_yuan_half_even_compatible_differences": nearest_yuan_half_even_compatible,
        "turnover_absolute_difference_le_half_yuan": turnover_within_half_yuan,
        "turnover_absolute_difference_gt_half_yuan": turnover_beyond_half_yuan,
        "turnover_exact_half_yuan_difference_count": turnover_exact_half_yuan_ties,
        "turnover_half_even_incompatible_examples": turnover_half_even_incompatible_examples,
        "turnover_legacy_float_rule_compatible_differences": turnover_legacy_float_rule_compatible,
        "turnover_legacy_float_rule_incompatible_count": (
            field_counts["turnover_cny"] - turnover_legacy_float_rule_compatible
        ),
        "turnover_legacy_float_rule_incompatible_examples": turnover_legacy_float_rule_incompatible_examples,
        "turnover_legacy_float_rule_is_diagnostic_only": True,
        "only_in_task01_by_symbol_prefix": dict(sorted(Counter(
            sym.split(".")[0][:3] + "." + sym.split(".")[-1] for sym, day in only_task
        ).items())),
        "turnover_precision_is_only_a_diagnostic": True,
        "numeric_field_differences": diffs,
        "only_in_task01_examples": [list(k) for k in only_task[:max_examples]],
        "only_in_gold_examples": [list(k) for k in only_gold[:max_examples]],
        "per_day": dict(sorted(per_day.items())),
        "task01_file_sha256_verified_against_official_manifest": True,
        "task01_file_sha256": task_hash,
        "gold_history_month_sha256_observed_only": gold_hash,
        "gold_history_month_sha256_verified_against_official_manifest": False,
        "gold_historical_pit_universe_verified": False,
        "historical_board_transfer_and_delisting_pit_verified": False,
        "other_factors_and_corporate_actions_verified": False,
        "frozen_v1_equivalence_verified": False,
        "five_trading_days_shadow_verified": False,
        "production_switch_allowed": False,
        "delete_old_data_allowed": False,
    }


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pointer", required=True, type=Path)
    p.add_argument("--task-manifest", required=True, type=Path)
    p.add_argument("--gold-manifest", required=True, type=Path)
    p.add_argument("--task-daily", required=True, type=Path)
    p.add_argument("--gold-daily", required=True, type=Path)
    p.add_argument("--gold-instruments", required=True, type=Path)
    p.add_argument("--task-relative-path", required=True)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args(argv)
    report = diagnose(
        json.loads(args.pointer.read_text("utf-8")),
        args.task_manifest.read_bytes(),
        json.loads(args.gold_manifest.read_text("utf-8")),
        args.task_daily, args.gold_daily, task_relative_path=args.task_relative_path,
        gold_instruments_path=args.gold_instruments,
    )
    # No network or pointer writes. Caller owns the explicit local report path.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "utf-8")
    print("HISTORICAL_DIAGNOSTIC", report["status"], report["common_keys"],
          report["numeric_field_difference_count"])
    return 0 if report["status"] == "MATCHED_COMMON_ROWS_DIAGNOSTIC_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
