"""Safety contract for the manual, non-publishing Task01 staging audit."""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/task01-stage-audit.yml"


def test_manual_main_only_and_no_background_schedule():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "source_run_id:" in text and "stage_id:" in text
    assert "if: github.ref == 'refs/heads/main'" in text
    assert "schedule:" not in text
    assert "workflow_run:" not in text
    assert "cancel-in-progress: false" in text


def test_source_run_and_checkpoint_are_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")
    for guard in (
        'source.get("path") != ".github/workflows/task01.yml"',
        'source.get("conclusion") != "cancelled"',
        'source.get("head_branch") != "main"',
        'source.get("event") not in ("schedule", "workflow_dispatch")',
        'step("Daily update or maintenance") != "success"',
        'step("Production acceptance") != "success"',
        'step("Publish accepted snapshot to Drive") != "cancelled"',
        'artifact("task01-validation-report")',
        'checkpoint = artifact("task01-checkpoint")',
        'not sha.startswith("sha256:")',
        'not 0 < size <= 5_000_000_000',
        "sha256sum -c -",
        'tf.extractall(".", filter="data")',
        'accepted = Path("data/current/acceptance.json")',
    ):
        assert guard in text, guard


def test_only_explicit_readonly_audit_with_no_drive_mutation():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '--audit-resume-stage-id "$STAGE_ID"' in text
    assert "metadata/stage_audit.json" in text
    assert 'r.get("audit_only") is not True' in text
    assert 'r.get("production_switch_allowed") is not False' in text
    assert 'r.get("pointer_unchanged") is not True' in text
    assert "TUSHARE_TOKEN" not in text
    assert "run_task01" not in text
    assert "gh api -X" not in text
    assert "git push" not in text
    assert "--resume-stage-id " not in text
    assert "python -m data_pipeline.drive_publish\n" not in text
    assert "task01-data" not in text  # read-only audit never blocks the production writer


def test_artifact_and_archive_handling_limits():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n  actions: read" in text
    assert "persist-credentials: false" in text
    assert '[[ "$SOURCE_RUN_ID" =~ ^[0-9]{8,15}$ ]]' in text
    assert '[[ "$STAGE_ID" =~ ^[A-Za-z0-9_-]{10,100}$ ]]' in text
    assert 'rm -f "$RUNNER_TEMP/checkpoint.zip"' in text
    assert text.index("sha256sum -c -") < text.index("unzip -p")
    assert text.index('rm -f "$RUNNER_TEMP/checkpoint.zip"') < text.index('tf.extractall(".", filter="data")')
    assert 'path.parts[0] not in ("data", "metadata")' in text
    assert 'not (member.isfile() or member.isdir() or member.issym() or member.islnk())' in text


def test_audit_receipt_is_bound_to_explicit_source_stage_and_pointer():
    text = WORKFLOW.read_text(encoding="utf-8")
    for guard in (
        'SOURCE_RUN_ID: ${{ inputs.source_run_id }}',
        'CHECKPOINT_SHA: ${{ steps.gate.outputs.checkpoint_sha }}',
        'r.get("stage_id") != os.environ["STAGE_ID"]',
        'r.get("pointer_sha256")',
        'r["source_run_id"] = int(os.environ["SOURCE_RUN_ID"])',
        'r["checkpoint_sha256"] = os.environ["CHECKPOINT_SHA"]',
        'r["audit_run_id"] = int(os.environ["GITHUB_RUN_ID"])',
        'r["audit_commit_sha"] = os.environ["GITHUB_SHA"]',
    ):
        assert guard in text, guard
    assert "python -m data_pipeline.drive_publish --audit-resume-stage-id" in text
    assert "--resume-stage-id " not in text
