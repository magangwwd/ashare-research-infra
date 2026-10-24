"""Read bounded pilot JSON only; emit acceptance JSON to stdout."""
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path("docs/work-relay-pilot/20261021")
def read_json(name):
    path = ROOT / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("invalid JSON input")
    raw = path.read_bytes()
    return json.loads(raw), raw

def main():
    sha = sys.argv[1]
    result = {"task_id": "UNKNOWN", "head_sha": sha, "status": "FAIL", "checksum": None}
    try:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid head SHA")
        mandate, _ = read_json("mandate.json")
        task, raw = read_json("next-task.json")
        result.update(task_id=task.get("task_id"), checksum=hashlib.sha256(raw).hexdigest())
        if mandate.get("pilot_id") != "WORK-RELAY-PILOT-20261021":
            raise ValueError("wrong pilot")
        if mandate.get("allowed_branch") != "test/work-pr-relay-20261021":
            raise ValueError("wrong branch")
        if not all(mandate.get(k) is True for k in ("no_merge", "no_secrets", "no_trading")):
            raise ValueError("unsafe mandate")
        if mandate.get("max_tasks") != 2 or mandate.get("max_automatic_write_rounds") != 3:
            raise ValueError("wrong bounds")
        expected = {"task_id": "RELAY-002", "pilot_id": "WORK-RELAY-PILOT-20261021",
                    "operation": "validate_json_only", "input": "mandate.json",
                    "reviewed_head_sha": "8a4870e4b802a42a7d451623c44c2447b165019e",
                    "no_network": True, "no_secrets": True, "no_repository_writes": True}
        if task != expected:
            raise ValueError("unexpected task")
        result["status"] = "PASS"
    except (ValueError, OSError, TypeError) as exc:
        result["error"] = str(exc)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
