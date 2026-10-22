import copy,importlib.util,json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from data_pipeline import runtime_lifecycle as lifecycle
from data_pipeline import runtime_startup as startup
from data_pipeline import external_heartbeat as external


def snapshot(guard=False,worker=False,notifier=False,observer=False):
    services={role:[] for role in lifecycle.ROLES.values()}
    for role,live,pid in [('watchdog',guard,10),('worker',worker,11),('wechat',notifier,12),('observer',observer,13)]:
        if live:services[role]=[{'pid':pid,'ppid':1,'start_ticks':pid*100,'module':'data_pipeline.'+role}]
    return {'checked_at':'2026-10-20T12:00:00+00:00','instance':{'fingerprint':'new','kernel_fingerprint':'kernel','pid1_started_at':'2026-10-20T06:14:11+00:00'},'services':services,'foreign_worker_pids':[],'guard_heartbeat':{'fresh':guard,'declared_status':'RUNNING','updated_at':'2026-10-20T12:00:00+00:00'},'memory':{'events':{'oom_kill':0}}}


def test_recreated_container_and_pid_reuse_are_distinguished():
    old=snapshot(True,True);old['instance']['fingerprint']='old';old['memory']['events']['oom_kill']=5
    current=snapshot(True,True);current['services']['worker'][0]['start_ticks']+=1
    events=lifecycle.transitions(old,current)
    assert any(e['type']=='CONTAINER_CHANGED' and e['platform_trigger']=='UNKNOWN' for e in events)
    assert any(e['type']=='SERVICE_SET_CHANGED' and e['role']=='worker' for e in events)
    assert not any(e['type']=='CGROUP_OOM_KILL_INCREASED' for e in events)
    old=copy.deepcopy(current);current['memory']['events']['oom_kill']=1
    assert any(e['type']=='CGROUP_OOM_KILL_INCREASED' for e in lifecycle.transitions(old,current))


def test_stale_running_file_cannot_make_bridge_healthy(tmp_path):
    current=snapshot();current['guard_heartbeat']['declared_status']='RUNNING'
    lifecycle.bridge(current,tmp_path/'repo')
    d=json.loads((tmp_path/'repo/runtime_bridge/ashare_runtime_status.json').read_text())
    assert d['health']=='STOPPED' and d['download_process']['status']=='STOPPED' and d['auto_restart']['enabled'] is False


def test_process_inventory_does_not_copy_secret_arguments(tmp_path):
    p=tmp_path/'proc/44';p.mkdir(parents=True)
    (p/'cmdline').write_bytes(b'python\0-m\0data_pipeline.run_task01\0--root\0/workspace/state\0--token\0must-never-be-copied\0')
    fields=['S','1']+['0']*17+['123']
    (p/'stat').write_text('44 (python) '+' '.join(fields))
    services,foreign=lifecycle.process_inventory(Path('/workspace/state'),tmp_path/'proc')
    assert services['worker'][0]['pid']==44 and not foreign
    assert 'must-never' not in json.dumps(services)
    (p/'stat').write_text('44 (python) '+' '.join(['Z']+fields[1:]))
    assert not lifecycle.process_inventory(Path('/workspace/state'),tmp_path/'proc')[0]['worker']


@pytest.mark.parametrize('status,expected',[('BLOCKED','REQUIRES_OPERATOR_RESUME'),('STOPPED','REQUIRES_OPERATOR_RESUME'),('COMPLETE','COMPLETE')])
def test_startup_respects_nonrunning_states(status,expected):
    assert startup.recovery_policy(snapshot(),{'status':status})==expected


def test_startup_only_resumes_proven_unplanned_loss():
    s=snapshot();old={'status':'RUNNING','updated_at':'2026-10-20T06:09:01+00:00'}
    assert startup.recovery_policy(s,old)=='START_RESUME'
    assert startup.recovery_policy(s,old,stop_requested=True)=='INTENTIONALLY_STOPPED'
    assert startup.recovery_policy(s,old,completed=True)=='COMPLETE'
    s['foreign_worker_pids']=[55]
    assert startup.recovery_policy(s,old)=='CONFLICT_DO_NOT_START'
    assert startup.recovery_policy(snapshot(worker=True),old)=='KEEP_ORPHAN_WORKER'


def test_running_guard_and_cooldown_never_create_second_worker():
    assert startup.recovery_policy(snapshot(guard=True),{'status':'BACKOFF'})=='KEEP_GUARD'
    assert startup.recovery_policy(snapshot(guard=True,worker=True),{'status':'RUNNING'},explicit_resume=True)=='KEEP_GUARD'
    assert startup.recovery_policy(snapshot(guard=True),{'status':'BLOCKED'},explicit_resume=True)=='RESUME_GUARD'


def test_idempotent_startup_leaves_all_existing_services_unchanged(tmp_path,monkeypatch):
    runtime=tmp_path/'runtime';runtime.mkdir();root=tmp_path/'root';s=snapshot(True,True,True,True)
    lifecycle.atomic_json(runtime/'heartbeat.json',{'status':'RUNNING'})
    monkeypatch.setattr(startup,'collect',lambda *a:s)
    python=tmp_path/'repo/.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
    monkeypatch.delenv('HEALTHCHECKS_PING_URL',raising=False)
    monkeypatch.setattr(startup.subprocess,'run',lambda *a,**k:pytest.fail('Must not run another guard'))
    monkeypatch.setattr(startup,'launch_service',lambda *a:pytest.fail('Must not launch duplicate services'))
    assert startup.ensure(runtime,root,tmp_path/'repo')['started']=={}


@pytest.mark.parametrize('url',['http://hc-ping.com/'+'x'*30,'https://other.example/'+'x'*30,'https://secret@hc-ping.com/'+'x'*30,'https://hc-ping.com/short'])
def test_heartbeat_rejects_wrong_destinations(url):assert not external.valid_ping_url(url)


def test_external_heartbeat_ignores_download_stalls_and_accepts_cooling(tmp_path,monkeypatch):
    s=snapshot(guard=True);s['guard_heartbeat']['declared_status']='BACKOFF';monkeypatch.setattr(external,'collect',lambda *a:s)
    calls=[]
    def sender(url,**kwargs):calls.append(1);return SimpleNamespace(status_code=200,close=lambda:None)
    endpoint='https://hc-ping.com/'+'private-endpoint-id-123456789'
    assert external.ping(tmp_path,tmp_path/'root',endpoint,sender)
    assert calls==[1]
    payload=(tmp_path/'external-heartbeat.json').read_text();assert endpoint not in payload and 'private-endpoint' not in payload
    s['guard_heartbeat']['fresh']=False
    assert not external.ping(tmp_path,tmp_path/'root',endpoint,sender) and calls==[1]
