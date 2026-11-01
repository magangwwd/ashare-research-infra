"""Synthetic-only checkpoint archive contract tests; no Drive, tokens, or production data."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [
    ROOT / ".github/workflows/task01-stage-audit.yml",
    ROOT / ".github/workflows/task01-verified-stage-resume.yml",
]


def restore_script(path: Path) -> str:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["audit"]["steps"]
    matches = [s["run"] for s in steps if s.get("name") == "Restore verified checkpoint without network writes"]
    assert len(matches) == 1
    marker = "python - <<'PY'\n"
    assert matches[0].count(marker) == 1
    return matches[0].split(marker, 1)[1].split("\nPY", 1)[0]


def _add(tf: tarfile.TarFile, name: str, *, payload: bytes | None = None,
         symlink: str | None = None, hardlink: str | None = None) -> None:
    item = tarfile.TarInfo(name)
    if symlink is not None:
        item.type = tarfile.SYMTYPE
        item.linkname = symlink
        tf.addfile(item)
    elif hardlink is not None:
        item.type = tarfile.LNKTYPE
        item.linkname = hardlink
        tf.addfile(item)
    else:
        assert payload is not None
        item.size = len(payload)
        tf.addfile(item, io.BytesIO(payload))


def run_guard(tmp_path: Path, script: str, *, malicious: str | None = None):
    archive = tmp_path / "task01-state.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        _add(tf, "data/.versions/v1/acceptance.json", payload=b'{"passed": true}')
        _add(tf, "data/.versions/v1/raw/example", payload=b"verified-data")
        _add(tf, "data/.versions/v1/clean/example", hardlink="data/.versions/v1/raw/example")
        _add(tf, "data/current", symlink=".versions/v1")
        _add(tf, "data/raw", symlink="current/raw")
        _add(tf, "data/clean", symlink="current/clean")
        if malicious == "outside_symlink":
            _add(tf, "data/escape", symlink="../../outside")
        elif malicious == "unseen_hardlink":
            _add(tf, "data/.versions/v1/clean/evil", hardlink="data/.versions/v1/raw/not-archived")
        elif malicious == "traversal":
            _add(tf, "data/../../escape", payload=b"no")
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path))
    return subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                          text=True, capture_output=True, timeout=30)


def test_restore_guards_remain_identical():
    assert restore_script(WORKFLOWS[0]) == restore_script(WORKFLOWS[1])


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_only_known_in_tree_aliases_and_prior_regular_hardlink_are_allowed(workflow, tmp_path):
    result = run_guard(tmp_path, restore_script(workflow))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "data/current/acceptance.json").is_file()


@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("malicious", ["outside_symlink", "unseen_hardlink", "traversal"])
def test_archive_guard_rejects_unsafe_members(workflow, malicious, tmp_path):
    result = run_guard(tmp_path, restore_script(workflow), malicious=malicious)
    assert result.returncode != 0
    assert "BLOCKED: unsafe checkpoint member" in result.stderr + result.stdout
