"""Regression guards for Task01 checkpoint retention after failed publication.

No GitHub API, Google Drive credentials, or production data are accessed.
"""

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "task01.yml"


def _steps():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {step.get("name"): step for step in data["jobs"]["task01"]["steps"] if "name" in step}


def test_prune_only_runs_after_successful_drive_publish():
    prune = _steps()["Prune superseded recovery checkpoints"]
    condition = " ".join(str(prune["if"]).split())
    assert "always()" in condition
    assert "steps.drive_publish.outcome == 'success'" in condition
    assert "steps.restore.outcome == 'success'" in condition
    assert "steps.daily_update.outcome != 'skipped'" in condition
    assert "DRIVE_PUBLISHED" not in prune["run"]
    assert "keep=1" not in prune["run"]


def test_failed_publication_keeps_older_checkpoints_until_expiry():
    steps = _steps()
    checkpoint = steps["Save recovery checkpoint"]
    assert checkpoint["with"]["retention-days"] == 3
    assert "steps.drive_publish.outcome != 'success'" in checkpoint["if"]
    prune = steps["Prune superseded recovery checkpoints"]
    assert "gh api -X DELETE" in prune["run"]
    assert "steps.drive_publish.outcome == 'success'" in prune["if"]
