"""Explain ownership rejection without changing frozen selection rules."""
import pandas as pd


def ownership_quality_diagnostics(snapshot: pd.DataFrame, maximum_age: int = 180):
    out = snapshot[["vendor_symbol"]].copy()
    current = snapshot["current_bar_available"].fillna(False).astype(bool)
    for label, age, count in (("holder", "holder_disclosure_age_days", "holder_update_count"),
                              ("composition", "composition_disclosure_age_days", "composition_update_count")):
        ages = pd.to_numeric(snapshot[age], errors="coerce")
        out[label + "_missing"] = ages.isna()
        out[label + "_expired_or_future"] = ages.notna() & ~ages.between(0, maximum_age)
        out[label + "_insufficient_updates"] = ~pd.to_numeric(snapshot[count], errors="coerce").ge(2)
    out["composition_quality_invalid"] = ~snapshot["composition_quality_valid"].fillna(False).astype(bool)
    flags = list(out.columns[1:])
    passed = ~out[flags].any(axis=1)
    if "ownership_eligible" in snapshot:
        expected = snapshot["ownership_eligible"].fillna(False).astype(bool)
        if (passed[current] != expected[current]).any():
            raise RuntimeError("Ownership diagnostics disagree with the frozen eligibility gate")
    out["ownership_gate_pass"] = passed & current
    primary = pd.Series("PASS", index=out.index)
    for name in reversed(flags):
        primary.loc[out[name]] = name
    primary.loc[~current] = "NO_CURRENT_BAR"
    out["primary_reason"] = primary
    summary = {"universe": int(len(out)), "current_bar_rows": int(current.sum()),
               "ownership_failed_current_rows": int((current & ~passed).sum()),
               "primary_reason_counts": {str(k): int(v) for k,v in primary.value_counts().items()},
               "nonexclusive_failure_counts": {name: int((out[name] & current).sum()) for name in flags}}
    return out, summary
