import hashlib
import json
from tools.mainboard_cycle_gate import evaluate


def fixture():
    manifest = {"version": "v1", "snapshot_as_of": "2026-10-30",
                "file_count": 10, "total_bytes": 100, "validated": True}
    raw = json.dumps(manifest).encode()
    pointer = {**manifest, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
               "critical_history_complete": True}
    gold = {"as_of_date": "2026-10-30",
            "validation": {"primary_keys_unique": True, "ohlc_valid": True}}
    run = {"id": 42, "status": "completed", "conclusion": "success"}
    return pointer, manifest, raw, gold, run


def test_active_publisher_blocks_rerun_even_if_metadata_ok():
    p, m, raw, g, r = fixture()
    r["status"] = "in_progress"
    receipt = evaluate(p, m, raw, g, r)
    assert receipt["decision"] == "WAIT_ACTIVE_TASK01"
    assert receipt["retrigger_allowed"] is False
    assert receipt["production_switch_allowed"] is False


def test_stale_pointer_and_missing_history_fail_closed():
    p, m, raw, g, r = fixture()
    g["as_of_date"] = "2026-10-31"
    p["critical_history_complete"] = False
    receipt = evaluate(p, m, raw, g, r)
    assert receipt["decision"] == "BLOCKED"
    assert {"TASK01_BEHIND_GOLD", "TASK01_HISTORY_INCOMPLETE"} <= set(receipt["issues"])


def test_manifest_tamper_blocks():
    p, m, raw, g, r = fixture()
    m["file_count"] = 99
    receipt = evaluate(p, m, raw, g, r)
    assert "TASK01_FILE_COUNT_MISMATCH" in receipt["issues"]
    assert receipt["production_switch_allowed"] is False


def test_sha_mismatch_blocks():
    p, m, raw, g, r = fixture()
    p["manifest_sha256"] = "0" * 64
    assert "TASK01_MANIFEST_SHA_MISMATCH" in evaluate(p, m, raw, g, r)["issues"]


def test_green_metadata_is_not_go_live():
    p, m, raw, g, r = fixture()
    receipt = evaluate(p, m, raw, g, r)
    assert receipt["decision"] == "SHADOW_VALIDATION_REQUIRED"
    assert receipt["production_switch_allowed"] is False


def test_invalid_date_blocks():
    p, m, raw, g, r = fixture()
    g.pop("as_of_date")
    assert "INVALID_AS_OF_DATE" in evaluate(p, m, raw, g, r)["issues"]


def test_missing_run_evidence_blocks():
    p, m, raw, g, r = fixture()
    r.pop("id")
    assert "TASK01_RUN_EVIDENCE_MISSING" in evaluate(p, m, raw, g, r)["issues"]


def test_failed_run_blocks_even_with_aligned_manifest():
    p, m, raw, g, r = fixture()
    r["conclusion"] = "failure"
    assert evaluate(p, m, raw, g, r)["decision"] == "BLOCKED"
