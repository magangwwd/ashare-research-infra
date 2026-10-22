"""Export only Task 01 code; never copy live data, credentials or runtime reports."""
import argparse,hashlib,json,shutil,subprocess
from datetime import datetime,timezone
from pathlib import Path

README='''# Task 01 cloud recovery snapshot

This is a preserved copy of the Codex cloud runtime, not a replacement for the repository's production `data_pipeline/`.

- Runtime: **CPython 3.12**. The `recovered_oct08/*.bin` artifacts are checksum-pinned original local module/test caches. They are required recovery dependencies, not reviewable Python source. Replace them with reviewed source before approving production handoff.
- Scope: existing datasets from 2020-01-01; 15-minute history from 2026-01-01. This snapshot's full-universe/minute-derived daily behavior differs from the newer mainboard-only policy in `main`. Do not silently replace either policy.
- No data, local checkpoints, live PID/status files, OAuth credentials or tokens are included.
- The nested `.github/workflows/` files are **test fixtures**, not executable GitHub workflows. The only new root workflow tests this snapshot offline and has no schedule or secrets.
- Historical completion has not been certified by this code export. GitHub Actions daily publication must wait for the handoff gates in `../../docs/task01-actions-handoff-plan.md`.

Offline verification, from the repository root:

```bash
cd cloud_runtime/task01
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

The original cloud's full-backfill entry point is `python -m data_pipeline.run_task01 --mode full-backfill --config config/config.yaml --root /explicit/state/root`. Do not launch a second writer or point an offline check at production. A live backfill is **not** started by CI. Runtime/watchdog helpers expect a project-local `.venv`.

Use `docs/prompts/task01-actions-daily-handoff.md` in the repository root for the next implementation task. The daily handoff is a separate, reviewable change, not an automatic consequence of merging this backup.
'''

def export(source,destination,force=False):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.exists() and not force:raise RuntimeError('Destination exists; inspect it before using --force')
    if destination==source or not destination.is_relative_to(source/'cloud_runtime'):raise RuntimeError('Snapshot must be under cloud_runtime')
    files=[]
    files+=list((source/'data_pipeline').glob('*.py'))
    files+=list((source/'data_pipeline/recovered_oct08').glob('*.bin'))
    files+=[source/'data_pipeline/recovered_oct08/manifest.json',source/'data_pipeline/recovered_oct08/README.md']
    files+=[p for p in (source/'tests').glob('*.py') if p.name!='test_research_engine.py']
    files+=[source/'requirements.txt',source/'pytest.ini',source/'config/config.yaml',source/'scripts/ensure_task01_runtime.py']
    files+=[source/'docs'/name for name in ['recovery-20261018.md','runtime-outages-20261020.md','task01-spec.md','task01-watchdog.md']]
    files+=[source/'.github/workflows'/name for name in ['task01.yml','task01-backfill.yml','pipeline-ci.yml']]
    paths=set()
    for path in files:
        if not path.is_file() or path.is_symlink():raise RuntimeError('Missing or unsafe source: '+str(path.relative_to(source)))
        relative=path.relative_to(source)
        if relative in paths:raise RuntimeError('Duplicate snapshot member')
        paths.add(relative)
        dest=destination/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
    (destination/'README.md').write_text(README)
    members=[]
    for path in sorted([destination/p for p in paths]+[destination/'README.md']):
        payload=path.read_bytes();members.append({'path':path.relative_to(destination).as_posix(),'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
    def git(ref):return subprocess.check_output(['git','rev-parse',ref],cwd=source,text=True).strip()
    report={'schema_version':'1.0','captured_at':datetime.now(timezone.utc).isoformat(),'source_git_base':git('HEAD'),'compared_upstream_main':git('origin/main'),'runtime':'CPython 3.12','purpose':'cloud runtime recovery preservation; not production handoff approval','scope':{'history_start':'20200101','minute_history_start':'20260101'},'data_included':False,'credential_values_included':False,'files':members}
    (destination/'snapshot_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--destination',type=Path);parser.add_argument('--force',action='store_true');args=parser.parse_args();dest=args.destination or args.source/'cloud_runtime/task01';report=export(args.source,dest,args.force);print('EXPORTED_FILES',len(report['files']))
if __name__=='__main__':main()
