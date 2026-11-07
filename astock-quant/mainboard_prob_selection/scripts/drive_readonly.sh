#!/usr/bin/env bash
set -euo pipefail
# Read-only Drive probe/replay. Never modifies Google Drive.
MODE="${1:-preflight}"
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE="${RUNNER_TEMP:-/tmp}/prob-v7-evidence"
mkdir -p "$EVIDENCE"
: "${GDRIVE_RCLONE_CONFIG_B64:?Secret GDRIVE_RCLONE_CONFIG_B64 missing}"
GOLD_ID="${PROB_GOLD_FOLDER_ID:-1vCz7H5aeePjFapg7xLPu44x-PUSQOX_8}"
PROJECT_ID="${PROB_PROJECT_FOLDER_ID:-1ASvjYoIlejQH1uNiMeasofOHG4hkhvg7}"
cfg="${RUNNER_TEMP:-/tmp}/prob-v7-rclone.conf"
(umask 077; printf '%s' "$GDRIVE_RCLONE_CONFIG_B64" | base64 --decode > "$cfg")
trap 'rm -f "$cfg"' EXIT
rclone --config "$cfg" listremotes | grep -qx 'gdrive:'
# Dynamic remote option preserves the authenticated gdrive provider but scopes each read to a known folder.
GOLD_REMOTE="gdrive,root_folder_id=${GOLD_ID}:"
PROJECT_REMOTE="gdrive,root_folder_id=${PROJECT_ID}:"
rclone --config "$cfg" lsf "$GOLD_REMOTE" --files-only | grep -Fxq 'manifest.json'
rclone --config "$cfg" lsf "$PROJECT_REMOTE" --dirs-only | grep -Fxq 'cache/'
rclone --config "$cfg" copyto "${GOLD_REMOTE}manifest.json" "$EVIDENCE/gold-manifest.json"
python - "$EVIDENCE/gold-manifest.json" "$EVIDENCE/preflight.json" <<'PY'
import json,sys,hashlib,datetime
p,out=sys.argv[1:3]
raw=open(p,'rb').read()
d=json.loads(raw)
report={'result':'READ_ONLY_AUTH_OK', 'as_of_date':d.get('as_of_date'), 'manifest_sha256':hashlib.sha256(raw).hexdigest(), 'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
open(out,'w').write(json.dumps(report,indent=2))
print(json.dumps(report))
PY
if [[ "$MODE" == "preflight" ]]; then
    exit 0
fi
if [[ "$MODE" != "replay" ]]; then
    echo "::error::Unsupported mode"; exit 2
fi
mkdir -p "$BASE/cache" "$BASE/results"
for f in weekly_panel.parquet daily_sim.parquet predictions.parquet predictions_v67.parquet; do
    rclone --config "$cfg" copyto "${PROJECT_REMOTE}cache/$f" "$BASE/cache/$f" --retries 3
done
rclone --config "$cfg" copyto "${PROJECT_REMOTE}results/improve_trades_V7_ensemble.csv" "$BASE/results/improve_trades_V7_ensemble.csv"
cd "$BASE"
python -I -W ignore 10_replay_check.py
python - <<'PY'
import json,math
x=json.load(open('results/replay_check.json',encoding='utf8'))
t=x['replay_vs_original']
assert t['same_trades']==150 and t['replay_trades']==150, t
assert abs(t['replay_total']-0.5859625003435696)<0.001, t
print('PASS: 150 trades and V7 corrected baseline matched within registered tolerance')
PY
cp results/replay_check.json "$EVIDENCE/replay_check.json"
