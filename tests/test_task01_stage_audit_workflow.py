"""Safety contract for the manual, non-publishing Task01 staging audit."""
from pathlib import Path
import os
import subprocess
import sys
import tarfile
import tempfile
import textwrap

import pytest

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
    assert 'member.name == "data/current"' in text
    assert 'len(link.parts) == 2 and link.parts[0] == ".versions"' in text
    assert 'not (member.isfile() or member.isdir() or safe_current_link)' in text


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


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_restore_script_rejects_checkpoint_links_before_extraction(member_type):
    """A same-run checkpoint must not plant symlinks/hardlinks during audit."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Restore verified checkpoint without network writes\n", 1)[1]
    script = textwrap.dedent(
        step.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "task01-state.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo("data/current/unsafe-link")
            member.type = member_type
            member.linkname = "data/current/acceptance.json"
            tar.addfile(member)
        env = {**os.environ, "RUNNER_TEMP": temp}
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=temp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "BLOCKED: unsafe checkpoint member" in result.stderr + result.stdout
        assert not (Path(temp) / "data").exists()


def _run_restore_script_with_archive(archive_bytes):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Restore verified checkpoint without network writes\n", 1)[1]
    script = textwrap.dedent(
        step.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "task01-state.tar.gz").write_bytes(archive_bytes)
        env = {**os.environ, "RUNNER_TEMP": temp}
        outcome = subprocess.run(
            [sys.executable, "-c", script], cwd=temp, env=env,
            capture_output=True, text=True, check=False,
        )
        return outcome, (root / "data/current/acceptance.json").exists()


def test_restore_script_accepts_real_internal_current_version_link(tmp_path):
    """The audit must be capable of reading a normal Task01 tar checkpoint."""
    root = tmp_path / "source"
    version = root / "data/.versions/accepted"
    version.mkdir(parents=True)
    (version / "acceptance.json").write_text('{"passed":true}', encoding="utf-8")
    (root / "data/current").symlink_to(".versions/accepted", target_is_directory=True)
    archive = tmp_path / "accepted.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(root / "data", arcname="data")
    result, restored = _run_restore_script_with_archive(archive.read_bytes())
    assert result.returncode == 0, result.stderr + result.stdout
    assert restored


@pytest.mark.parametrize("linkname", ["../../etc", "/tmp/escape", ".versions/../escape"])
def test_restore_script_rejects_current_symlink_escaping_version_dir(tmp_path, linkname):
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("data/current")
        info.type = tarfile.SYMTYPE
        info.linkname = linkname
        tf.addfile(info)
    result, extracted = _run_restore_script_with_archive(archive.read_bytes())
    assert result.returncode != 0
    assert "BLOCKED: unsafe checkpoint member" in result.stderr + result.stdout
    assert not extracted
