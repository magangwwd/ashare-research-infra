from pathlib import Path

import yaml


def test_mainboard_research_history_boundaries_are_frozen():
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
    )

    assert config["download"]["history_start"] == "20200101"
    assert config["intraday"]["universe"] == "mainboard"
    assert config["intraday"]["history_start"] == "20260101"
    assert config["intraday"]["activate_daily_from_minutes"] is False
