"""Create isolated current ownership inputs; never mutate frozen legacy assets."""
from copy import deepcopy
from datetime import date
from pathlib import Path


def current_config(template: dict, market_root: Path, snapshot_root: Path, as_of: str) -> dict:
    date.fromisoformat(as_of)
    root = snapshot_root.resolve()
    if "legacy" in root.parts:
        raise ValueError("Current ownership refresh cannot write legacy assets")
    config = deepcopy(template)
    config.update({"market_data_root": str(market_root.resolve()),
        "research_end": as_of, "raw_data_dir": str(root / "raw"),
        "processed_data_dir": str(root / "processed"), "output_dir": str(root / "audit")})
    return config


def batch_can_resume(metadata: dict, start: str, end: str, symbols: list[str]) -> bool:
    return (metadata.get("start_date") == start and metadata.get("end_date") == end
            and metadata.get("requested_symbols") == list(symbols))
