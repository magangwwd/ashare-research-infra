"""Bounded JSON-only fixtures; stdout is the acceptance artifact."""
import hashlib, json, pathlib, re, sys
root = pathlib.Path("docs/work-relay-pilot/20261021-round2")
sha, case = sys.argv[1:]
if not re.fullmatch("[0-9a-f]{40}", sha) or case not in ("normal", "fault"):
    raise SystemExit("invalid arguments")
path = root / "task.json"
if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
    raise SystemExit("invalid task")
raw = path.read_bytes()
task = json.loads(raw)
expected = {"pilot_id": "WORK-RELAY-R2-20261021", "phase": task.get("phase"), "operation": "json_fixture_only"}
if task != expected or task["phase"] not in ("inject_failure", "repaired"):
    raise SystemExit("invalid task schema")
injected = case == "fault" and task["phase"] == "inject_failure"
result = {"task_id": "R2-NORMAL" if case == "normal" else ("R2-FAILURE" if injected else "R2-REPAIR"),
          "head_sha": sha, "phase": task["phase"], "case": case,
          "checksum": hashlib.sha256(raw).hexdigest(), "status": "FAIL" if injected else "PASS",
          "reason": "deliberately invalid JSON fixture value" if injected else "fixture accepted"}
print(json.dumps(result, sort_keys=True))
raise SystemExit(1 if injected else 0)
