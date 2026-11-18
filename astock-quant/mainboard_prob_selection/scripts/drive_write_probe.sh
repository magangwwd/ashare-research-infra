#!/usr/bin/env bash
set -euo pipefail
# Isolated, append-only write/readback probe. NEVER changes production/current.json.
: "${GDRIVE_RCLONE_CONFIG_B64:?secret required}"
: "${GITHUB_RUN_ID:?GitHub run id required}"
project_id="${PROB_PROJECT_FOLDER_ID:-1ASvjYoIlejQH1uNiMeasofOHG4hkhvg7}"
remote="gdrive,root_folder_id=${project_id}:"
cfg="${RUNNER_TEMP:-/tmp}/prob-v7-rclone-writer.conf"
(umask 077; printf '%s' "$GDRIVE_RCLONE_CONFIG_B64" | base64 --decode > "$cfg")
trap 'rm -f "$cfg"' EXIT
d="${RUNNER_TEMP:-/tmp}/prob-v7-write-probe"
mkdir -p "$d"
file="write_probe_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT:-1}.json"
python - "$d/$file" <<'PY'
import json,datetime,os,sys
rec={"purpose":"isolated-write-readback-probe",
     "run_id":os.environ["GITHUB_RUN_ID"],
     "run_attempt":os.getenv("GITHUB_RUN_ATTEMPT","1"),
     "commit":os.environ.get("GITHUB_SHA",""),
     "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
     "notice":"not a forecast; never a production pointer"}
with open(sys.argv[1],"w",encoding="utf-8") as f: json.dump(rec,f,ensure_ascii=False,indent=2)
PY
target="${remote}production/_migration_probe/$file"
if rclone --config "$cfg" lsf "${remote}production/_migration_probe/" --files-only 2>/dev/null | grep -Fxq "$file"; then
  echo "::error::Probe path already exists, refusing overwrite: $file"
  exit 1
fi
rclone --config "$cfg" copyto "$d/$file" "$target"
rclone --config "$cfg" copyto "$target" "$d/readback.json"
python - "$d/$file" "$d/readback.json" "$d/receipt.json" <<'PY'
import hashlib,json,sys
a,b,o=sys.argv[1:]
def sha(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()
assert sha(a)==sha(b), 'write/readback SHA256 mismatch'
r={"status":"WRITE_READBACK_VERIFIED","sha256":sha(a),"remote":"production/_migration_probe/"+a.split('/')[-1],"production_pointer_changed":False}
with open(o,'w',encoding='utf8') as f: json.dump(r,f,indent=2)
print(json.dumps(r))
PY
