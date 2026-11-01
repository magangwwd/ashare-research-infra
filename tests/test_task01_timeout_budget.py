"""Regression gate for Task01's accepted-snapshot publication time budget.

Run #41248659151 was cancelled near the 240-minute job limit, after
8,600/8,657 staged files. Increasing the *existing* job budget does not
constitute proof of a successful remote publication.
"""
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/task01.yml"


def test_task01_has_bounded_time_for_large_verified_publish():
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    minutes = config["jobs"]["task01"]["timeout-minutes"]
    assert 300 <= minutes <= 350


def test_task01_keeps_single_writer_lock_and_existing_crons():
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "group: task01-data" in content
    for cron in ("17 13 * * *", "30 15 * * *", "23 19 * * *"):
        assert cron in content
    assert "cancel-in-progress: false" in content


def test_failed_publishing_never_prunes_checkpoint():
    content = WORKFLOW.read_text(encoding="utf-8")
    section = content.split("- name: Prune superseded recovery checkpoints", 1)[1]
    section = section.split("- name: Generate Task 02 readiness", 1)[0]
    assert "steps.drive_publish.outcome == 'success'" in section
