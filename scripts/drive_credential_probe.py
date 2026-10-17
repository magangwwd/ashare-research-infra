"""Manual, non-destructive Drive scope probe. Never prints credentials or remote errors."""
import argparse
import base64
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

def result(status, **details):
    return {"status": status, **details}

def rclone(config, args):
    try:
        run = subprocess.run(["rclone", "--config", str(config), *args],
                             capture_output=True, timeout=45, check=False)
        return run.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def probe_rclone(write, report):
    raw = os.getenv("GDRIVE_RCLONE_CONFIG_B64", "")
    if not raw:
        report["rclone"] = {"secret_present": False, "status": "NOT_CONFIGURED"}
        return
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp)/"rclone.conf"
        try:
            cfg.write_bytes(base64.b64decode(raw, validate=True))
            cfg.chmod(0o600)
        except (ValueError, Exception):
            report["rclone"] = {"secret_present": True, "status": "INVALID_ENCODING"}
            return
        remote_ok = rclone(cfg, ["listremotes"])
        scopes = {}
        # Root visibility does not establish access to all of My Drive or Shared Drives.
        for label, target in (("configured_root", "gdrive:"),
                              ("ashare_quant", "gdrive:Ashare_Quant"),
                              ("strategy", "gdrive:stock_state_strategy")):
            scopes[label] = {"read_listing": rclone(cfg, ["lsf", target, "--max-depth", "1"])}
        if write:
            # Only probe an existing, designated test directory; never create at Drive root.
            for label, target in (("ashare_quant", "gdrive:Ashare_Quant/_access_probe"),
                                  ("strategy", "gdrive:stock_state_strategy/_access_probe")):
                if not scopes[label]["read_listing"]:
                    scopes[label]["write_delete"] = "SKIPPED_NO_READ"
                    continue
                name = "probe-" + uuid.uuid4().hex + ".txt"
                local = Path(tmp)/name
                local.write_text("disposable access probe\n", encoding="utf-8")
                dest = target.rstrip("/") + "/" + name
                wrote = rclone(cfg, ["copyto", str(local), dest])
                readback = wrote and rclone(cfg, ["copyto", dest, str(Path(tmp)/("readback-"+name))])
                matched = readback and (Path(tmp)/("readback-"+name)).read_bytes() == local.read_bytes()
                deleted = wrote and rclone(cfg, ["deletefile", dest])
                scopes[label]["write_delete"] = ("PASS" if matched and deleted else
                                                  "FAIL_OR_CLEANUP_REQUIRED")
        report["rclone"] = {"secret_present": True,
                            "remote_config_parseable": remote_ok, "scopes": scopes,
                            "full_drive_rw": "NOT_PROVEN_BY_PATH_PROBE"}
def probe_oauth(report):
    raw = os.getenv("GDRIVE_OAUTH_JSON", "")
    if not raw:
        report["oauth"] = {"secret_present": False, "status": "NOT_CONFIGURED"}
        return
    # Never persist OAuth JSON or reveal token, email, IDs, or provider response.
    try:
        obj = json.loads(raw)
        plausible = isinstance(obj, dict) and bool(obj)
    except (TypeError, ValueError):
        plausible = False
    report["oauth"] = {"secret_present": True, "json_parseable": plausible,
                       "drive_scope": "NOT_TESTED",
                       "reason": "Requires independent authenticated API folder checks"}
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    report = {"schema": 1, "source": "GitHub Actions manual credential probe",
              "tushare_secret_present": bool(os.getenv("TUSHARE_TOKEN")),
              "write_probe_requested": os.getenv("WRITE_PROBE", "false").lower() == "true"}
    try:
        probe_rclone(report["write_probe_requested"], report)
        probe_oauth(report)
    finally:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
if __name__ == "__main__":
    main()
