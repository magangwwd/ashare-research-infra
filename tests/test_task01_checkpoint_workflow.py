"""Keep Task 01 recovery artifacts conditional on a real update attempt."""
from pathlib import Path

import yaml


def test_no_new_checkpoint_is_created_when_live_smoke_blocks_daily_update():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/task01.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = {step.get("name"): step for step in workflow["jobs"]["task01"]["steps"]}

    assert steps["Daily update or maintenance"]["id"] == "daily_update"
    for name in (
        "Package recovery state, including unfinished work",
        "Save recovery checkpoint",
        "Prune superseded recovery checkpoints",
    ):
        assert "steps.daily_update.outcome != 'skipped'" in steps[name]["if"]
