"""Safety and provenance gates for manual-only Task01 verified stage recovery."""
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github/workflows/task01-verified-stage-resume.yml"


def test_manual_only_main_guard_serializes_production_writer():
    s = WF.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in s
    assert "source_run_id:" in s and "stage_id:" in s
    assert "if: github.ref == 'refs/heads/main'" in s
    assert "group: task01-data" in s
    assert "cancel-in-progress: false" in s
    assert "timeout-minutes: 350" in s
    for dangerous in ("schedule:", "workflow_run:", "pull_request:", "TUSHARE_TOKEN", "run_task01", "git push", "gh api -X"):
        assert dangerous not in s


def test_source_checkpoint_and_explicit_stage_log_witness():
    s = WF.read_text(encoding="utf-8")
    for guard in (
        'source.get("path") != ".github/workflows/task01.yml"',
        'source.get("conclusion") != "cancelled"',
        'source.get("head_branch") != "main"',
        'step("Daily update or maintenance") != "success"',
        'step("Production acceptance") != "success"',
        'step("Publish accepted snapshot to Drive") != "cancelled"',
        'artifact("task01-validation-report")',
        'checkpoint = artifact("task01-checkpoint")',
        'not sha.startswith("sha256:")',
        'sha256sum -c -',
        'f.write("task_job_id="',
        'actions/jobs/$SOURCE_JOB_ID/logs',
        'matches != [os.environ["STAGE_ID"]]',
        'tf.extractall(".", filter="data")',
    ):
        assert guard in s, guard


def test_sha_audit_must_finish_before_resuming_and_preserve_old_pointer():
    s = WF.read_text(encoding="utf-8")
    assert s.index('--audit-resume-stage-id "$STAGE_ID"') < s.index('--resume-stage-id "$STAGE_ID"')
    assert 'r.get("production_switch_allowed") is not False' in s
    assert 'r.get("pointer_unchanged") is not True' in s
    assert 'r.get("stage_id") != os.environ["STAGE_ID"]' in s
    assert 'r.get("version_folder_id") != os.environ["STAGE_ID"]' in s
    assert 'r.get("previous_version_recovery_verified") is not True' in s
    assert 'r.get("snapshot_as_of") != accepted.get("as_of")' in s
    assert "permissions:\n  contents: read\n  actions: read" in s
    assert "persist-credentials: false" in s
    assert "task01-stage-audit" in s
    assert "task01-stage-recovery-publication" in s


def test_manual_recovery_does_not_trigger_expensive_price_downloads():
    s = WF.read_text(encoding="utf-8")
    assert "GDRIVE_OAUTH_JSON: ${{ secrets.GDRIVE_OAUTH_JSON }}" in s
    assert "task01-data" in s
    assert "python -m pip install -r requirements.txt" in s
    assert "python -m data_pipeline.drive_publish --audit-resume-stage-id" in s
    assert "python -m data_pipeline.drive_publish --resume-stage-id" in s
    assert "python -m data_pipeline.run_task01" not in s
    assert "secrets.TUSHARE_TOKEN" not in s

