"""Safety and provenance gates for manual-only Task01 verified stage recovery."""
from pathlib import Path
import os
import subprocess
import sys
import tarfile
import tempfile
import textwrap

import pytest

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


def test_yaml_structure_and_input_contract():
    import yaml
    data = yaml.load(WF.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(data["on"]) == {"workflow_dispatch"}
    dispatch = data["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch) == {"source_run_id", "stage_id"}
    assert all(dispatch[key]["required"] == "true" for key in dispatch)
    assert data["concurrency"]["group"] == "task01-data"
    assert data["concurrency"]["cancel-in-progress"] == "false"
    assert data["jobs"]["audit"]["if"] == "github.ref == 'refs/heads/main'"
    assert data["jobs"]["audit"]["timeout-minutes"] == "350"


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_restore_script_rejects_checkpoint_links_before_extraction(member_type):
    """A same-run checkpoint must not plant symlinks/hardlinks during audit."""
    workflow = WF.read_text(encoding="utf-8")
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
    workflow = WF.read_text(encoding="utf-8")
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
