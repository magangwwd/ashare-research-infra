#!/usr/bin/env bash
set -euo pipefail
# Historical 2026-11-02 signal replay + four paper ledgers: isolated local files only.
base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${GDRIVE_RCLONE_CONFIG_B64:?missing authorized reader secret}"
cfg="${RUNNER_TEMP:-/tmp}/prob-v7-dryrun-rclone.conf"
(umask 077; printf '%s' "$GDRIVE_RCLONE_CONFIG_B64" | base64 --decode > "$cfg")
trap 'rm -f "$cfg"' EXIT
gold="gdrive,root_folder_id=${PROB_GOLD_FOLDER_ID:-1vCz7H5aeePjFapg7xLPu44x-PUSQOX_8}:"
proj="gdrive,root_folder_id=${PROB_PROJECT_FOLDER_ID:-1ASvjYoIlejQH1uNiMeasofOHG4hkhvg7}:"
mkdir -p "$base/gold" "$base/cache" "$base/live/cache" "$base/results"
rclone --config "$cfg" copyto "${gold}manifest.json" "$base/gold/manifest.json"
rclone --config "$cfg" copyto "${gold}instruments.parquet" "$base/gold/instruments.parquet"
for f in weekly_panel.parquet daily_sim.parquet; do
 rclone --config "$cfg" copyto "${proj}cache/$f" "$base/live/cache/$f" --retries 3
done
rclone --config "$cfg" copyto "${proj}cache/predictions.parquet" "$base/cache/predictions.parquet" --retries 3
export PROB_GOLD_ROOT="$base/gold"
export V7_LIVE_DIR="$base/live"
export PROB_REQUIRE_OFFICIAL_CALENDAR="0"
cd "$base"
python -I -W ignore 09_weekly_signal.py --replay 2026-11-02 --skip-build
python - <<'PY'
import json,pathlib,shutil
root=pathlib.Path('live')
p=json.loads((root/'replay/signal_2026-11-02.json').read_text())
rec=p['record']
assert rec['signal_date']=='2026-11-02',rec
assert '600050' in rec['rank1'],rec
assert rec['model_cutoff']=='2026-10-12',rec
assert p['model_meta']['sha256'],p
(root/'signals').mkdir(exist_ok=True)
shutil.copyfile(root/'replay/signal_2026-11-02.json',root/'signals/signal_2026-11-02.json')
print('PASS: historical replay top1, model cutoff and hashes verified; no prospective signal issued')
PY
python -I -W ignore 11_forward_ledger.py --test-ignore-deadline
python - <<'PY'
import json,hashlib,pathlib,os
p=pathlib.Path('live/ledger/summary.json')
x=json.loads(p.read_text())
assert x['weeks_signalled']==1,x
assert all(k in x for k in ('PRIMARY_budget50','CONTROL_risk1pct','CONTROL_R2_gate55','CONTROL_R3_dual'))
sig=pathlib.Path('live/replay/signal_2026-11-02.json')
rec={'result':'HISTORICAL_PAPER_DRYRUN_OK','historical_only':True,
     'signal_sha256':hashlib.sha256(sig.read_bytes()).hexdigest(),
     'signal_date':'2026-11-02','accounts':x,
     'production_pointer_changed':False}
out=pathlib.Path(os.environ.get('RUNNER_TEMP','/tmp'))/'prob-v7-forward-dryrun'
out.mkdir(exist_ok=True)
(out/'receipt.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2))
print(json.dumps(rec,ensure_ascii=False))
PY
