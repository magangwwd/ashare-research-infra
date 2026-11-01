import hashlib
import json

from tools.mainboard_loop_gate import assess


def evidence():
    manifest = {
        "version": "v1", "snapshot_as_of": "2026-10-30",
        "source_run_id": "100", "file_count": 1, "total_bytes": 20,
        "validated": True, "files": [{"path": "data/clean/daily/x", "bytes": 20}],
    }
    raw = json.dumps(manifest).encode()
    pointer = {k: manifest[k] for k in (
        "version", "snapshot_as_of", "source_run_id", "file_count",
        "total_bytes", "validated"
    )}
    pointer.update(manifest_sha256=hashlib.sha256(raw).hexdigest(),
                   critical_history_complete=True)
    gold = {"as_of_date": "2026-10-30",
            "validation": {"primary_keys_unique": True, "ohlc_valid": True}}
    run = {"id": 123, "status": "completed", "conclusion": "success",
           "publication_step_conclusion": "success",
           "acceptance_step_conclusion": "success"}
    artifacts = [{"name": "task01-validation-report", "expired": False,
                  "digest": "sha256:" + "0" * 64}]
    return pointer, manifest, raw, gold, run, artifacts


def test_active_workflow_never_retriggers_or_switches():
    p, m, raw, g, run, a = evidence()
    run.update(status="in_progress", conclusion=None,
               publication_step_conclusion=None)
    result = assess(p, m, raw, g, run, a)
    assert result["decision"] == "WAIT_FOR_ACTIVE_RUN"
    assert result["automatic_retrigger_allowed"] is False
    assert result["production_switch_allowed"] is False


def test_cancelled_publish_with_checkpoint_is_not_success():
    p, m, raw, g, run, a = evidence()
    run.update(status="completed", conclusion="cancelled",
               publication_step_conclusion="cancelled")
    a.append({"name": "task01-checkpoint", "expired": False,
              "digest": "sha256:" + "1" * 64})
    result = assess(p, m, raw, g, run, a)
    assert result["decision"] == "BLOCKED_PUBLISH"
    assert result["same_run_checkpoint_observed"] is True
    assert result["stage_sha_audit_completed"] is False


def test_old_data_and_incomplete_history_remain_blocked():
    p, m, raw, g, run, a = evidence()
    g["as_of_date"] = "2026-10-31"
    p["critical_history_complete"] = False
    result = assess(p, m, raw, g, run, a)
    assert result["decision"] == "BLOCKED_METADATA"
    assert {"TASK01_BEHIND_GOLD", "TASK01_HISTORY_INCOMPLETE"} <= set(result["issues"])


def test_tampered_manifest_is_blocked():
    p, m, raw, g, run, a = evidence()
    p["manifest_sha256"] = "wrong"
    result = assess(p, m, raw, g, run, a)
    assert result["decision"] == "BLOCKED_METADATA"
    assert "TASK01_MANIFEST_SHA_MISMATCH" in result["issues"]


def test_missing_file_count_and_byte_count_fail():
    p, m, raw, g, run, a = evidence()
    m["files"][0]["bytes"] = 19
    assert "TASK01_TOTAL_BYTES_INCORRECT" in assess(p, m, raw, g, run, a)["issues"]
    m["files"] = []
    assert "TASK01_FILE_COUNT_INCORRECT" in assess(p, m, raw, g, run, a)["issues"]


def test_missing_acceptance_artifact_fail_closed():
    p, m, raw, g, run, a = evidence()
    assert "ACCEPTANCE_REPORT_ARTIFACT_UNAVAILABLE" in assess(p, m, raw, g, run, [])["issues"]


def test_metadata_success_requires_future_shadow_and_rollback():
    p, m, raw, g, run, a = evidence()
    result = assess(p, m, raw, g, run, a)
    assert result["decision"] == "SHADOW_VALIDATION_REQUIRED"
    assert result["production_switch_allowed"] is False


def test_invalid_dates_fail_closed():
    p, m, raw, g, run, a = evidence()
    g.pop("as_of_date")
    assert "INVALID_AS_OF_DATE" in assess(p, m, raw, g, run, a)["issues"]
