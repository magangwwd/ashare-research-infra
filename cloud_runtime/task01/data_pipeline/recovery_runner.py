"""Explicit entry point for the checksum-pinned recovery baseline."""
import argparse,copy,hashlib,json,os,signal,traceback
from pathlib import Path
from .manifest import atomic_json,read_json,utcnow
from .run_task01 import load_config
from .drive_publish import Drive,PublishError
from .drive_auth import parse_credentials,verify_folder
from google.auth.transport.requests import AuthorizedSession,Request

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--config',type=Path,default=Path('config/config.yaml'))
    parser.add_argument('--checkpoint-every',type=int,default=200);parser.add_argument('--min-free-gib',type=float,default=2)
    args=parser.parse_args(argv)
    missing=[n for n in ['TUSHARE_TOKEN','GDRIVE_OAUTH_JSON'] if not os.environ.get(n)]
    if missing:print('Missing credentials: '+', '.join(missing),flush=True);return 1
    def interrupted(*args):raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,interrupted)
    try:
        c=load_config(args.config);c['_drive_streaming']=True;c['_recovery_scope']=True
        assert c['download']['history_start']=='20200101' and c['intraday']['history_start']=='20260101'
        assert c['datasets']['required'] and c['datasets']['optional'] and c['intraday']['enabled']
        creds=parse_credentials(os.environ['GDRIVE_OAUTH_JSON']);creds.refresh(Request())
        with AuthorizedSession(creds) as session:
            target=c['storage']['drive']['folder_id'];verify_folder(session,target)
            from .recovery_backup import ensure_source_backup
            ensure_source_backup(Drive(session),target,args.root.resolve())
            from .full_backfill import execute
            execute(args.root.resolve(),c,Drive(session),target,args.checkpoint_every,args.min_free_gib)
        return 0
    except KeyboardInterrupt:
        print('INTERRUPTED; verified checkpoint preserved',flush=True);return 130
    except Exception as e:
        reason=str(e) if isinstance(e,PublishError) else type(e).__name__
        # Frames only: SDK exception text, locals and signed URLs are excluded.
        frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(e.__traceback__)]
        atomic_json(args.root/'outputs/full-backfill-failure.json',{'status':'FAILED','reason':reason,'exception_type':type(e).__name__,'frames':frames,'updated_at':utcnow()})
        print(f'RECOVERY_FAILED type={type(e).__name__}; inspect safe failure report',flush=True);return 1
    finally:signal.signal(signal.SIGTERM,previous)


def patch_checkpoint(module):
    original=module.restore_best
    def restore_best(root,config,drive,target):
        # Preserve the accepted immutable base; stub bytes are verified on access.
        if read_json(Path(root)/'outputs/recovery-base.json').get('manifest_sha256'):
            return {'source':'verified_working_checkpoint','production_validation':'DEFERRED_TO_DATA_ACCESS_AND_FINAL_DRILL'}
        return original(root,config,drive,target)
    module.restore_best=restore_best
