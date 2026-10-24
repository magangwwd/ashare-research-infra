import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest,requests
from data_pipeline import drive_publish as dp,drive_cache as dc
from data_pipeline.recovery_bundle import Reader,MAGIC,safe_path
from data_pipeline.recovery_scope import rows
from data_pipeline.manifest import atomic_json

def sha(b):return hashlib.sha256(b).hexdigest()

class MemoryDrive:
    def __init__(self):self.data={};self.parents={};self.names={};self.counter=0;self.commits=0;self.fail=False
    def folder(self,parent,name):return name
    def children(self,parent):
        return [{'id':k,'name':self.names[k],'size':str(len(v)),'sha256Checksum':sha(v),'parents':[self.parents[k]],'trashed':False} for k,v in self.data.items() if self.parents[k]==parent]
    def read(self,fid):return self.data[fid]
    def metadata(self,fid):return {'size':str(len(self.data[fid])),'sha256Checksum':sha(self.data[fid]),'parents':[self.parents[fid]],'trashed':False}
    def remote_digest(self,fid):return len(self.data[fid]),sha(self.data[fid])
    def upload_resuming(self,parent,name,path,journal):
        if self.fail:raise dp.PublishError('simulated upload failure')
        self.counter+=1;fid='id'+str(self.counter);self.data[fid]=Path(path).read_bytes();self.parents[fid]=parent;self.names[fid]=name;return fid
    def download(self,fid,path):
        Path(path).write_bytes(self.data[fid]);return len(self.data[fid]),sha(self.data[fid])
    def commit_pointer(self,parent,fid,old,path):
        self.commits+=1
        if fid:assert self.data[fid]==old;self.data[fid]=Path(path).read_bytes()
        else:return self.upload_resuming(parent,'current.json',path,Path('unused'))

def item(drive,payload=b'hello'):
    blob=MAGIC+payload;drive.data['bundle']=blob;drive.parents['bundle']='objects';drive.names['bundle']=sha(blob)+'-'+str(len(blob))
    return {'file_id':'bundle','bytes':len(payload),'sha256':sha(payload),'bundle':{'bytes':len(blob),'sha256':sha(blob),'offset':len(MAGIC)}}

def test_bundle_reader_and_member_corruption(tmp_path):
    d=MemoryDrive();i=item(d);r=Reader(d,tmp_path/'cache',reserve=0);dest=tmp_path/'restored'
    assert r.download(i,dest)==(5,sha(b'hello')) and dest.read_bytes()==b'hello'
    i['sha256']='0'*64
    with pytest.raises(dp.PublishError):r.download(i,dest)
    assert dest.read_bytes()==b'hello'

@pytest.mark.parametrize('path',['../../x','/tmp/x','metadata/../x','data\\x','outside/file'])
def test_safe_paths(path):
    with pytest.raises(dp.PublishError):safe_path(path)


def test_bundle_space_budget(tmp_path,monkeypatch):
    import data_pipeline.recovery_bundle as rb
    d=MemoryDrive();i=item(d);r=Reader(d,tmp_path/'cache',reserve=10)
    monkeypatch.setattr(rb.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(dp.PublishError):r.download(i,tmp_path/'dest')
    assert not (tmp_path/'dest').exists()

class Reply:
    status_code=200
    def __init__(self,fail):self.fail=fail;self.closed=False
    def iter_content(self,n):
        yield b'hello'
        if self.fail:raise requests.exceptions.ChunkedEncodingError('private text')
    def close(self):self.closed=True

@pytest.mark.parametrize('failures',[1,3])
def test_stream_retry_preserves_old_file(tmp_path,monkeypatch,failures):
    import data_pipeline.recovery_transport as rt
    d=dp.Drive(None);replies=[Reply(True) for _ in range(failures)]+[Reply(False)];queue=iter(replies)
    monkeypatch.setattr(d,'request',lambda *a,**k:next(queue));monkeypatch.setattr(rt.time,'sleep',lambda n:None)
    dest=tmp_path/'dest';dest.write_bytes(b'old')
    if failures==3:
        with pytest.raises(dp.PublishError) as e:d.download('fid',dest)
        assert 'private' not in str(e.value) and 'network' in str(e.value);assert dest.read_bytes()==b'old'
    else:assert d.download('fid',dest)==(5,sha(b'hello'))
    assert not list(tmp_path.glob('*.download-*'))


def test_unknown_financial_dates_and_identity_context(config):
    config['download']['history_start']='20200101';config['_recovery_scope']=True
    f=pd.DataFrame({'ann_date':[None,None],'end_date':[None,None]})
    assert len(rows(config,'dividend',f,'raw'))==2
    f=pd.DataFrame({'list_date':['19900101']})
    assert len(rows(config,'stock_basic',f,'raw'))==1


def setup_checkpoint(tmp_path):
    root=tmp_path;current=root/'data/.versions/accepted';stage=root/'data/.versions/active'
    (current/'clean').mkdir(parents=True);(stage/'clean/daily/year=2026').mkdir(parents=True)
    (root/'data/current').symlink_to('.versions/accepted')
    part=stage/'clean/daily/year=2026/part.parquet';part.write_bytes(b'parquet content')
    store=SimpleNamespace(stage=stage,flush_state=lambda:None)
    d=MemoryDrive();c=dc.DriveCache(root,d,'target',reserve=0)
    return d,c,store,part


def test_checkpoint_dedup_and_pointer_last(tmp_path):
    d,c,s,part=setup_checkpoint(tmp_path);c.checkpoint(s,force=True)
    assert d.commits==1 and dc.stub_info(part)
    objects=[k for k,v in d.parents.items() if v=='objects'];assert len(objects)==1
    c.checkpoint(s,force=True)
    assert [k for k,v in d.parents.items() if v=='objects']==objects
    manifest=json.loads(d.read(json.loads(d.read([i['id'] for i in d.children(c.folder) if i['name']=='current.json'][0]))['manifest_file_id']))
    assert manifest['schema_version']=='working-2' and len(manifest['files'])==1
    c2=dc.DriveCache(tmp_path,d,'target',reserve=0)
    assert c2.hydrate(dc.stub_info(part)).read_bytes()==b'parquet content'


def test_failed_checkpoint_preserves_production_and_previous_pointer(tmp_path):
    d,c,s,part=setup_checkpoint(tmp_path);c.checkpoint(s,force=True)
    pointer=[i for i in d.children(c.folder) if i['name']=='current.json'][0]['id'];old=d.read(pointer)
    part.unlink();part.write_bytes(b'changed parquet');d.fail=True
    with pytest.raises(dp.PublishError):c.checkpoint(s,force=True)
    assert d.read(pointer)==old and part.read_bytes()==b'changed parquet'
    assert (tmp_path/'data/current').resolve().name=='accepted'


def test_restore_working2_resumes_metadata_and_keeps_parquet_lazy(tmp_path):
    d=MemoryDrive()
    # Build the hierarchy returned by Drive; only local memory is mutated.
    folder={'id':'working','name':'backfill-working-v1'}
    files=[]
    for rel,payload in [
        ('metadata/manifest/active.json',b'{"version":"active"}'),
        ('data/.versions/accepted/acceptance.json',b'{"passed":true}'),
        ('data/.versions/active/clean/daily/year=2026/p.parquet',b'parquet bytes')]:
        p=tmp_path/('payload'+str(len(files)));p.write_bytes(payload)
        fid=d.upload_resuming('objects','object',p,Path('unused'))
        files.append({'path':rel,'file_id':fid,'bytes':len(payload),'sha256':sha(payload)})
    manifest={'schema_version':'working-2','current_version':'accepted','active_version':'active','files':files}
    p=tmp_path/'manifest';p.write_text(json.dumps(manifest));fid=d.upload_resuming('manifests','manifest',p,Path('unused'))
    pointer={'manifest_file_id':fid,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())};p=tmp_path/'pointer';p.write_text(json.dumps(pointer));pfid=d.upload_resuming('working','current.json',p,Path('unused'))
    old=d.children
    d.children=lambda parent:[folder] if parent=='target' else old(parent)
    root=tmp_path/'restore';assert dc.restore_working(root,d,'target')
    assert json.loads((root/'metadata/manifest/active.json').read_text())['version']=='active'
    assert dc.stub_info(root/'data/.versions/active/clean/daily/year=2026/p.parquet')
    assert (root/'data/current').resolve().name=='accepted'
    assert d.commits==0


def test_no_inactivity_wechat_alert(tmp_path,monkeypatch):
    import time
    import data_pipeline.recovery_notifier as n
    runtime=tmp_path/'runtime';runtime.mkdir();now=time.time()
    atomic_json(runtime/'heartbeat.json',{'status':'RUNNING','updated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'no_recent_progress':True})
    atomic_json(runtime/'wechat-state.json',{'recovery_periodic_at':now,'recovery_last_status':'RUNNING'})
    calls=[];monkeypatch.setattr(n,'send',lambda text:calls.append(text))
    n.tick(runtime,tmp_path/'root',now)
    assert calls==[]
    assert json.loads((runtime/'wechat-heartbeat.json').read_text())['inactivity_alerts_enabled'] is False


def test_code_backup_contains_no_credentials_and_is_idempotent(tmp_path):
    from data_pipeline.recovery_backup import ensure_source_backup
    import tarfile
    d=MemoryDrive();d.verified_digest=d.remote_digest
    proof=ensure_source_backup(d,'target',tmp_path/'root');count=d.counter
    assert ensure_source_backup(d,'target',tmp_path/'root')==proof and d.counter==count
    archive=tmp_path/'root/outputs/source-backup/recovery-code.tar'
    with tarfile.open(archive) as tar:
        names=tar.getnames()
        assert 'data_pipeline/recovery_runner.py' in names and 'config/config.yaml' in names
        assert not any('oauth.json' in name or '.env' in name for name in names)
    assert proof['credentials_included'] is False


def test_changed_source_backup_uses_independent_upload_journal(tmp_path,monkeypatch):
    import data_pipeline.recovery_backup as backup
    repo=tmp_path/'repo';(repo/'data_pipeline/recovered_oct08').mkdir(parents=True)
    for rel,body in [('data_pipeline/recovery_backup.py','first source'),('data_pipeline/recovered_oct08/manifest.json','{}'),('requirements.txt','pandas'),('config/config.yaml','scope: 2020'),('docs/recovery-20261018.md','recovery')]:
        path=repo/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
    monkeypatch.setattr(backup,'__file__',str(repo/'data_pipeline/recovery_backup.py'))
    drive=MemoryDrive();drive.verified_digest=drive.remote_digest;upload=drive.upload_resuming;journals=[]
    def guarded_upload(parent,name,path,journal):
        journal=Path(journal);journal.parent.mkdir(parents=True,exist_ok=True)
        if journal.exists():assert json.loads(journal.read_text())['name']==name
        journal.write_text(json.dumps({'name':name}));journals.append(journal)
        return upload(parent,name,path,journal)
    drive.upload_resuming=guarded_upload
    first=backup.ensure_source_backup(drive,'target',tmp_path/'root')
    (repo/'data_pipeline/recovery_backup.py').write_text('changed source')
    second=backup.ensure_source_backup(drive,'target',tmp_path/'root')
    assert first['sha256']!=second['sha256']
    assert journals[0]!=journals[1] and journals[0].exists() and journals[1].exists()
    assert first['file_id'] in drive.data and second['file_id'] in drive.data
