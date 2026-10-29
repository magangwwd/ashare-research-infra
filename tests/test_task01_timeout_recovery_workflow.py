"""Contract for timeout-only, no-Tushare, no-stale-pointer recovery."""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/task01-timeout-publish-recovery.yml"


def test_only_completed_task01_workflow_run_can_trigger():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'workflows: ["Task 01 data infrastructure"]' in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.conclusion == 'cancelled'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in text
    assert "schedule:" not in text
    assert "workflow_dispatch:" not in text


def test_timeout_like_job_and_same_run_checkpoint_are_required():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "235 <= minutes <= 265" in text
    assert 'one("task01-validation-report")' in text
    assert 'checkpoint = one("task01-checkpoint")' in text
    assert "SOURCE_RUN_ID" in text
    assert "CHECKPOINT_ID" in text
    assert 'tf.extractall(".", filter="data")' in text
    assert "member.issym() or member.islnk()" not in text


def test_isolated_recovery_does_not_download_data_or_change_strategy():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "timeout-minutes: 350" in text
    assert "group: task01-data" in text
    assert "cancel-in-progress: false" in text
    assert "python -m data_pipeline.drive_publish" in text
    assert "TUSHARE_TOKEN" not in text
    assert "run_task01" not in text
    assert "stock_state_strategy" not in text
    assert "delete" not in text.lower()


def test_tar_data_filter_restores_internal_task01_current_symlink(tmp_path):
    import tarfile
    root = tmp_path / "source"
    target = root / "data/.versions/accepted"
    target.mkdir(parents=True)
    (target / "acceptance.json").write_text('{"passed":true}', encoding="utf-8")
    (root / "data/current").symlink_to(".versions/accepted", target_is_directory=True)
    archive = tmp_path / "checkpoint.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(root / "data", arcname="data")
    dest = tmp_path / "restored"
    dest.mkdir()
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest, filter="data")
    assert (dest / "data/current/acceptance.json").read_text() == '{"passed":true}'


def test_tar_data_filter_rejects_symlink_escape(tmp_path):
    import io
    import tarfile
    import pytest
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("data/current")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    dest = tmp_path / "restored"
    dest.mkdir()
    with tarfile.open(archive, "r:gz") as tf:
        with pytest.raises(tarfile.FilterError):
            tf.extractall(dest, filter="data")
