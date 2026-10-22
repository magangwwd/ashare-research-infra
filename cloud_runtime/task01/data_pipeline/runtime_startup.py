"""Idempotent startup after a cloud instance is available; never kills a service."""
import argparse,fcntl,json,os,subprocess,sys,time
from datetime import datetime
from pathlib import Path
from data_pipeline.runtime_lifecycle import collect,read_json,utc,DEFAULT_ROOT,DEFAULT_RUNTIME


def recovery_policy(snapshot,state,stop_requested=False,completed=False,explicit_resume=False):
    s=snapshot['services'];foreign=snapshot.get('foreign_worker_pids',[])
    if foreign or len(s['worker'])>1 or len(s['watchdog'])>1:return 'CONFLICT_DO_NOT_START'
    if stop_requested and not explicit_resume:return 'INTENTIONALLY_STOPPED'
    if completed:return 'COMPLETE'
    if s['watchdog']:
        if explicit_resume and not s['worker'] and state.get('status') in ('BLOCKED','STOPPED','BACKOFF'):return 'RESUME_GUARD'
        return 'KEEP_GUARD'
    if s['worker']:return 'KEEP_ORPHAN_WORKER'
    declared=state.get('status')
    if declared in ('BLOCKED','STOPPED') and not explicit_resume:return 'REQUIRES_OPERATOR_RESUME'
    if declared=='COMPLETE':return 'COMPLETE'
    if explicit_resume:return 'START_RESUME'
    # A prior RUNNING heartbeat predating the new PID1 proves that its owner
    # cannot be a surviving service. Resume that unplanned loss only.
    try:
        container_start=datetime.fromisoformat(snapshot['instance']['pid1_started_at'])
        last=datetime.fromisoformat(state['updated_at'])
        if declared=='RUNNING' and last<container_start:return 'START_RESUME'
    except (KeyError,ValueError,TypeError):pass
    return 'START'


def launch_service(command,repo,log_path):
    log_path.parent.mkdir(parents=True,exist_ok=True)
    with log_path.open('ab',buffering=0) as log:
        return subprocess.Popen(command,cwd=repo,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True).pid


def ensure(runtime,root,repo,explicit_resume=False,dry_run=False):
    snapshot=collect(runtime,root);state=read_json(runtime/'heartbeat.json');report=read_json(root/'metadata/full_backfill_report.json')
    action=recovery_policy(snapshot,state,(runtime/'STOP').exists(),report.get('completed') is True,explicit_resume)
    result={'checked_at':utc(),'action':action,'started':{},'note':'Invoked recovery only; this is not a platform boot hook or an external monitor.'}
    if dry_run:return result
    if action in ('CONFLICT_DO_NOT_START','INTENTIONALLY_STOPPED','COMPLETE','REQUIRES_OPERATOR_RESUME','KEEP_ORPHAN_WORKER'):return result
    python=repo/'.venv/bin/python'
    if not python.is_file():raise RuntimeError('Missing project Python environment')
    if action.startswith('START') or action=='RESUME_GUARD':
        missing=[n for n in ['TUSHARE_TOKEN','GDRIVE_OAUTH_JSON'] if not os.environ.get(n)]
        if missing:result['action']='MISSING_CREDENTIALS';result['missing_names']=missing;return result
        # Use the established guard command and its existing single-writer lock.
        command=[str(python),'-m','data_pipeline.runtime_guard','resume' if action in ('START_RESUME','RESUME_GUARD') else 'start','--runtime',str(runtime),'--root',str(root),'--repo',str(repo)]
        subprocess.run(command,cwd=repo,check=True,timeout=30)
        for attempt in range(20):
            snapshot=collect(runtime,root)
            if snapshot['services']['watchdog']:break
            time.sleep(1)
        if not snapshot['services']['watchdog']:raise RuntimeError('Guard readiness not observed')
    snapshot=collect(runtime,root)
    for role,module,args in [('observer','data_pipeline.runtime_lifecycle',['daemon']),('wechat','data_pipeline.recovery_notifier',[])]:
        if snapshot['services'][role]:continue
        if role=='wechat' and not os.environ.get('PUSHPLUS_TOKEN'):
            result['wechat']='MISSING_PUSHPLUS_TOKEN';continue
        command=[str(python),'-u','-m',module,*args,'--root',str(root),'--runtime',str(runtime)]
        result['started'][role]=launch_service(command,repo,runtime/(role+'.log'))
    if os.environ.get('HEALTHCHECKS_PING_URL') and not snapshot['services']['external_heartbeat']:
        command=[str(python),'-u','-m','data_pipeline.external_heartbeat','--root',str(root),'--runtime',str(runtime)]
        result['started']['external_heartbeat']=launch_service(command,repo,runtime/'external-heartbeat.log')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=DEFAULT_ROOT);p.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--resume',action='store_true');p.add_argument('--dry-run',action='store_true');a=p.parse_args();a.runtime.mkdir(parents=True,exist_ok=True)
    with (a.runtime/'ensure.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print(json.dumps({'action':'OTHER_STARTUP_IN_PROGRESS'}));return
        print(json.dumps(ensure(a.runtime,a.root,a.repo,a.resume,a.dry_run),ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except Exception as exc:print('ENSURE_RUNTIME_FAILED '+type(exc).__name__);raise SystemExit(1)
