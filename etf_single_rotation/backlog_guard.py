"""Read-only safety check for ETF task dependencies; never dispatches workflows.

Only VERIFIED_DONE is a formal acceptance state under AUTO-RD-SOP-001.
Legacy VERIFIED_RESEARCH_COMPLETE records do not unlock new tasks.
"""
import json
from pathlib import Path

QUEUE = Path(__file__).with_name("research_backlog.json")
ACTIVE = frozenset({"READY", "CLAIMED", "RUNNING", "VERIFYING", "VERIFIED_DONE"})


def validate_dependencies(backlog):
    tasks = backlog["tasks"]
    by_id = {task["id"]: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("Duplicate ETF task IDs")
    for task in tasks:
        if task["status"] not in ACTIVE:
            continue
        for dependency in task.get("depends_on", []):
            if dependency not in by_id:
                raise ValueError(f"{task['id']}: missing dependency {dependency}")
            if by_id[dependency]["status"] != "VERIFIED_DONE":
                raise ValueError(
                    f"{task['id']}: dependency {dependency} is "
                    f"{by_id[dependency]['status']}, not VERIFIED_DONE"
                )
    return True


def main():
    backlog = json.loads(QUEUE.read_text(encoding="utf-8"))
    validate_dependencies(backlog)
    print("ETF_BACKLOG_DEPENDENCIES_OK; NOT_FOR_TRADING")


if __name__ == "__main__":
    main()
