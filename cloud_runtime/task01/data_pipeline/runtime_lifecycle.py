"""Read-only service observation; records container changes without restarting jobs."""
import argparse,fcntl,hashlib,json,os,signal,time
from datetime import datetime,timezone
from pathlib import Path

ROLES={'data_pipeline.run_task01':'worker','data_pipeline.runtime_guard':'watchdog','data_pipeline.recovery_notifier':'wechat','data_pipeline.runtime_lifecycle':'observer','data_pipeline.external_heartbeat':'external_heartbeat'}
DEFAULT_ROOT=Path('/workspace/ashare-task01-resumed-20261018')
DEFAULT_RUNTIME=Path('/workspace/task01-runtime')

def utc(timestamp=None):return datetime.fromtimestamp(time.time() if timestamp is None else timestamp,timezone.utc).isoformat()
def read_json(path):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return {}
def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    try:
        with temp.open('w') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
def instance_identity(proc=Path('/proc'),now=None):
    now=time.time() if now is None else now
    try:
        fields=(proc/'1/stat').read_text().rsplit(') ',1)[1].split();ticks=int(fields[19]);hz=os.sysconf('SC_CLK_TCK')
        uptime=float((proc/'uptime').read_text().split()[0]);boot=(proc/'sys/kernel/random/boot_id').read_text().strip()
        namespace=os.readlink(proc/'1/ns/pid')
        fingerprint=hashlib.sha256(f'{boot}:{namespace}:{ticks}'.encode()).hexdigest()
        return {'fingerprint':fingerprint,'kernel_fingerprint':hashlib.sha256(boot.encode()).hexdigest(),'pid1_started_at':utc(now-uptime+ticks/hz)}
    except (OSError,ValueError,IndexError):return {'fingerprint':None,'kernel_fingerprint':None,'pid1_started_at':None}
def process_inventory(root,proc=Path('/proc')):
    services={role:[] for role in ROLES.values()};foreign=[]
    for entry in proc.iterdir():
        if not entry.name.isdigit():continue
        try:
            args=(entry/'cmdline').read_bytes().split(b'\0');fields=(entry/'stat').read_text().rsplit(') ',1)[1].split()
            if fields[0]=='Z':continue
            module=next((x.decode() for x in args if x.decode(errors='replace') in ROLES),None)
            if not module:continue
            actual_root=None
            if b'--root' in args:actual_root=args[args.index(b'--root')+1].decode()
            role=ROLES[module]
            if actual_root is not None and Path(actual_root).resolve()!=Path(root).resolve():
                if role=='worker':foreign.append(int(entry.name))
                continue
            services[role].append({'pid':int(entry.name),'ppid':int(fields[1]),'start_ticks':int(fields[19]),'state':fields[0],'module':module})
        except (OSError,ValueError,IndexError):continue
    return services,foreign

def memory_observation(base=Path('/sys/fs/cgroup')):
    result={}
    for name in ['memory.current','memory.peak','memory.max']:
        try:
            value=(base/name).read_text().strip();result[name]=int(value) if value.isdigit() else None
        except OSError:result[name]=None
    try:result['events']={k:int(v) for k,v in (line.split() for line in (base/'memory.events').read_text().splitlines())}
    except (OSError,ValueError):result['events']={}
    return result

def collect(runtime,root,now=None,proc=Path('/proc')):
    now=time.time() if now is None else now;services,foreign=process_inventory(root,proc);h=read_json(runtime/'heartbeat.json')
    try:age=now-datetime.fromisoformat(h['updated_at']).timestamp();fresh=0<=age<=180
    except (KeyError,ValueError,TypeError):age=None;fresh=False
    return {'schema_version':'1.0','checked_at':utc(now),'instance':instance_identity(proc,now),'services':services,'foreign_worker_pids':foreign,'guard_heartbeat':{'fresh':fresh,'age_seconds':age,'declared_status':h.get('status'),'updated_at':h.get('updated_at')},'memory':memory_observation(),'root':str(root),'platform_stop_reason':None}

def transitions(previous,current):
    events=[];old=previous.get('instance',{}).get('fingerprint');new=current['instance']['fingerprint']
    if old and new and old!=new:
        events.append({'type':'CONTAINER_CHANGED','previous_last_observed_at':previous.get('checked_at'),'new_pid1_started_at':current['instance']['pid1_started_at'],'platform_trigger':'UNKNOWN'})
    for role in ['worker','watchdog','wechat']:
        before={(p['pid'],p.get('start_ticks')) for p in previous.get('services',{}).get(role,[])}
        after={(p['pid'],p.get('start_ticks')) for p in current['services'][role]}
        if before!=after:events.append({'type':'SERVICE_SET_CHANGED','role':role,'before_pids':sorted(p[0] for p in before),'after_pids':sorted(p[0] for p in after)})
    if old==new and new:
        a=previous.get('memory',{}).get('events',{}).get('oom_kill',0);b=current['memory']['events'].get('oom_kill',0)
        if b>a:events.append({'type':'CGROUP_OOM_KILL_INCREASED','delta':b-a})
    return events

def append_event(path,event):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())

def bridge(current,repo):
    services=current['services'];guard=services['watchdog'];worker=services['worker'];fresh=current['guard_heartbeat']['fresh']
    health='HEALTHY' if len(worker)==len(guard)==1 and fresh else 'STOPPED' if not worker and not guard else 'DEGRADED'
    def one(role):
        items=services[role];p=items[0] if len(items)==1 else {}
        return {'status':'RUNNING' if len(items)==1 else 'STOPPED' if not items else 'UNKNOWN','pid':p.get('pid'),'ppid':p.get('ppid'),'command':('python -m '+p['module']) if p else None}
    d=one('worker');d.update(started_at=None,uptime_seconds=None,working_directory=None)
    w=one('watchdog')
    if w['status']=='STOPPED':w['status']='NOT_FOUND'
    status={'schema_version':'1.0','checked_at':current['checked_at'],'workspace':str(repo.parent),'service':'ashare-backfill','health':health,'download_process':d,'watchdog':w,'auto_restart':{'enabled':len(guard)==1 and fresh,'mechanism':'runtime_guard' if guard else None,'restart_count':None,'last_restart_at':None,'restart_delay_seconds':None},'progress':{'last_progress_at':None,'source':None},'safe_actions':{'can_check_status':True,'can_read_logs':True,'restart_recommended_now':False},'notes':['Live /proc identities and fresh guard heartbeat, not stale RUNNING files, determine this status.','Runtime health does not certify new download progress or historical completion.','No credentials or raw process argument values are recorded.','Container changes are observable; the provider lifecycle trigger is unavailable.']}
    dest=repo/'runtime_bridge';atomic_json(dest/'ashare_runtime_status.json',status)
    text='ASHARE RUNTIME STATUS\n\nHealth: '+health+'\nDownload process: '+d['status']+'\nPID: '+str(d['pid'])+'\nWatchdog: '+w['status']+'\nWatchdog PID: '+str(w['pid'])+'\nAuto restart: '+('ENABLED' if status['auto_restart']['enabled'] else 'DISABLED')+'\nRestart mechanism: '+str(status['auto_restart']['mechanism'])+'\nLast restart: null\nLast progress: null\nChecked at: '+current['checked_at']+'\n\nRecommendation: Leave a running service unchanged; inspect lifecycle/current.json for evidence.\n'
    (dest/'ashare_runtime_status.txt').write_text(text)

def tick(runtime,root,repo,now=None):
    current=collect(runtime,root,now);state=runtime/'lifecycle/current.json';previous=read_json(state)
    for event in transitions(previous,current):append_event(runtime/'lifecycle/events.jsonl',{'at':current['checked_at'],**event})
    atomic_json(state,current);bridge(current,repo);return current

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['once','daemon']);p.add_argument('--root',type=Path,default=DEFAULT_ROOT);p.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();a.runtime.mkdir(parents=True,exist_ok=True)
    if a.action=='once':print(json.dumps(tick(a.runtime,a.root,a.repo),ensure_ascii=False));return
    with (a.runtime/'lifecycle.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        def shutdown(signum,frame):
            append_event(a.runtime/'lifecycle/events.jsonl',{'at':utc(),'type':'OBSERVER_SIGNAL','signal':signum});raise SystemExit(0)
        signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
        while True:
            try:tick(a.runtime,a.root,a.repo)
            except Exception as exc:print('LIFECYCLE_ERROR '+type(exc).__name__,flush=True)
            time.sleep(60)
if __name__=='__main__':main()
