"""Recovery cache with immutable bundle reuse and pointer-last checkpoints."""
import hashlib,json,os,re,shutil,time
from pathlib import Path
from threading import Lock
from .manifest import atomic_json,read_json,utcnow
from .drive_publish import PublishError,digest
from .recovery_bundle import Reader,reference,physical,safe_path,MAGIC,MAX_BYTES


def install(module):
    legacy=module.DriveCache
    def restore(root,drive,target):
        root=Path(root);root.mkdir(parents=True,exist_ok=True)
        if (root/'data').exists() or (root/'metadata').exists():return False
        folders=[x for x in drive.children(target) if x['name']=='backfill-working-v1']
        if len(folders)!=1:raise PublishError('Missing or ambiguous working checkpoint folder')
        pointers=[x for x in drive.children(folders[0]['id']) if x['name']=='current.json']
        if len(pointers)!=1:raise PublishError('Missing or ambiguous working checkpoint pointer')
        pointer=json.loads(drive.read(pointers[0]['id']));raw=drive.read(pointer['manifest_file_id'])
        if (len(raw),hashlib.sha256(raw).hexdigest())!=(pointer['bytes'],pointer['sha256']):raise PublishError('Working manifest SHA256 mismatch')
        manifest=json.loads(raw)
        if manifest.get('schema_version') not in ('working-1','working-2'):raise PublishError('Unsupported working checkpoint')
        for k in ('current_version','active_version'):
            if not re.fullmatch('[A-Za-z0-9_.+-]+',manifest[k]):raise PublishError('Unsafe version')
        work=root/'.working-restore';reader=Reader(drive,root/'outputs/drive-cache/bundles');seen=set()
        # Interrupted restoration reuses only independently hash-verified files.
        for pos,item in enumerate(manifest['files'],1):
            rel=safe_path(item['path'])
            if str(rel) in seen:raise PublishError('Duplicate working checkpoint path')
            seen.add(str(rel));physical(item);dest=work/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.suffix=='.parquet':
                if not dest.exists() or module.stub_info(dest)!=reference(item):module.write_stub(dest,reference(item))
            elif not dest.exists() or digest(dest)!=(item['bytes'],item['sha256']):reader.download(item,dest)
            if pos%500==0:print(f'RECOVERY_RESTORE files={pos}/{len(manifest["files"])}',flush=True)
        current=manifest['current_version'];active=manifest['active_version']
        if read_json(work/'metadata/manifest/active.json').get('version')!=active:raise PublishError('Active checkpoint version mismatch')
        if not read_json(work/f'data/.versions/{current}/acceptance.json').get('passed'):raise PublishError('Missing accepted production base')
        for name,destination in [('current','.versions/'+current),('clean','current/clean'),('raw','current/raw')]:
            link=work/'data'/name;link.unlink(missing_ok=True);link.symlink_to(destination)
        atomic_json(root/'outputs/working-install.json',{'current_version':current,'active_version':active})
        module.finish_working_install(root)
        atomic_json(root/'outputs/drive-cache/restored-manifest.json',manifest)
        atomic_json(root/'outputs/drive-cache/restored-pointer.json',pointer)
        atomic_json(root/'outputs/recovery-base.json',{'schema_version':manifest['schema_version'],'manifest_sha256':pointer['sha256'],'restored_at':utcnow(),'parquet_verification':'ON_ACCESS','final_recovery_drill_required':True})
        print(f'RECOVERY_RESTORED files={len(seen)}; parquet SHA256 checked on access; final drill still required',flush=True)
        return True

    class Cache(legacy):
        def __init__(self,root,drive,target,budget=8*1024**3,reserve=2*1024**3):
            self.root=Path(root).resolve();self.drive=drive;self.target=target;self.budget=budget;self.reserve=reserve
            self.local=self.root/'outputs/drive-cache';self.local.mkdir(parents=True,exist_ok=True)
            self.folder=drive.folder(target,'backfill-working-v1');self.objects=drive.folder(self.folder,'objects');self.manifests=drive.folder(self.folder,'manifests')
            self.index=read_json(self.local/'index.json');self.index_lock=Lock();self.reader=Reader(drive,self.local/'bundles',reserve=reserve)
            code_proof=read_json(self.root/'outputs/source-backup/verified.json')
            if code_proof:atomic_json(self.root/'metadata/manifest/source_recovery.json',code_proof)
            self.reuse={};self.members={};self.verified={};self.pending_bytes=0
            previous=read_json(self.local/'restored-manifest.json')
            for item in previous.get('files',[]):self.members[(item['bytes'],item['sha256'])]=reference(item)
            for value in self.index.values():
                info=value.get('info')
                if info:self.members[(info['bytes'],info['sha256'])]=reference(info)
            # A single list pass supplies server checksums for all referenced objects.
            for item in drive.children(self.objects):
                match=re.fullmatch(r'([0-9a-f]{64})-(\d+)',item['name'])
                if match and item.get('trashed') is False and str(item.get('size'))==match[2] and item.get('sha256Checksum')==match[1] and self.objects in item.get('parents',[]):
                    self.verified[item['id']]=(int(match[2]),match[1]);self.reuse[item['name']]=[item['id']]
            print(f'DRIVE_BATCH_VERIFIED objects={len(self.verified)}',flush=True)
            self.last_remote=time.monotonic()
            module._ACTIVE=self
        def verify_object(self,file_id):
            if file_id not in self.verified:self.verified[file_id]=module.remote_hash(self.drive,file_id)
            return self.verified[file_id]
        def hydrate(self,info):
            physical(info);p=self.local/(info['sha256']+'.parquet')
            if p.exists() and digest(p)==(info['bytes'],info['sha256']):return p
            for old in sorted(self.local.glob('*.parquet'),key=lambda p:p.stat().st_mtime):
                if sum(p.stat().st_size for p in self.local.glob('*.parquet'))+info['bytes']<=self.budget and shutil.disk_usage(self.root).free>=self.reserve+info['bytes']:break
                old.unlink()
            if info['bytes']>self.budget or shutil.disk_usage(self.root).free<self.reserve+info['bytes']:raise PublishError('Insufficient bounded hydration disk')
            self.reader.download(info,p);return p
        def upload(self,path):
            path=Path(path);stub=module.stub_info(path) if path.suffix=='.parquet' else None
            if stub:
                if self.verify_object(stub['file_id'])!=physical(stub):raise PublishError('Missing or changed remote stub')
                return reference(stub)
            key=digest(path);info=self.members.get(key)
            if info:
                if self.verify_object(info['file_id'])!=physical(info):raise PublishError('Missing or changed reusable object')
                return reference(info)
            name=f'{key[1]}-{key[0]}'
            ids=self.reuse.get(name,[])
            if ids and self.verify_object(ids[0])==key:fid=ids[0]
            else:
                fid=self.drive.upload_resuming(self.objects,name,path,self.local/'sessions'/(name+'.json'))
                if self.verify_object(fid)!=key:raise PublishError('New upload SHA256 mismatch')
            info={'bytes':key[0],'sha256':key[1],'file_id':fid};self.members[key]=info;return info
        def checkpoint(self,store,evict=True,force=False):
            store.flush_state()
            free=shutil.disk_usage(self.root).free
            if not force and time.monotonic()-self.last_remote<4*3600 and free>self.reserve+4*1024**3:return {'local_checkpoint':True,'remote_checkpoint_deferred':True}
            stage=store.stage.resolve();current=(self.root/'data/current').resolve();paths=[]
            for base in [stage/'clean',current/'clean']:
                if base.exists():paths.extend(base.rglob('*.parquet'))
            for base in [stage,current]:
                if (base/'acceptance.json').exists():paths.append(base/'acceptance.json')
            for name in ['manifest','schemas','capabilities']:
                paths.extend((self.root/'metadata'/name).rglob('*.json'))
            for name in ['smoke.json','effective_config.json','acceptance.json']:
                if (self.root/'metadata'/name).exists():paths.append(self.root/'metadata'/name)
            paths=sorted(set(paths));files=[];fresh=[]
            for path in paths:
                if path.is_symlink():raise PublishError('Unexpected checkpoint symlink')
                rel=path.relative_to(self.root).as_posix();safe_path(rel)
                stub=module.stub_info(path) if path.suffix=='.parquet' else None
                key=(stub['bytes'],stub['sha256']) if stub else digest(path)
                info=stub or self.members.get(key)
                if info:
                    if self.verify_object(info['file_id'])!=physical(info):raise PublishError('Checkpoint content missing or changed')
                    files.append({'path':rel,**reference(info)})
                else:fresh.append((path,rel,key))
            # Pack new small objects by dataset; preserve existing bundle identities.
            groups={}
            for item in fresh:
                group=str(item[0].parent) if item[0].suffix!='.parquet' else '/'.join(item[1].split('/')[:5])
                groups.setdefault(group,[]).append(item)
            for entries in groups.values():
                batch=[];size=len(MAGIC)
                def flush(batch):
                    if not batch:return
                    payload=bytearray(MAGIC);infos=[]
                    for path,rel,key in batch:
                        data=path.read_bytes()
                        if (len(data),hashlib.sha256(data).hexdigest())!=key:raise PublishError('Local content changed during checkpoint')
                        infos.append((rel,key,len(payload)));payload.extend(data)
                    sha=hashlib.sha256(payload).hexdigest();packed=self.local/(sha+'.upload-bundle');packed.write_bytes(payload)
                    uploaded=self.upload(packed)
                    for rel,key,offset in infos:
                        info={'bytes':key[0],'sha256':key[1],'file_id':uploaded['file_id'],'bundle':{'bytes':len(payload),'sha256':sha,'offset':offset}}
                        self.members[key]=info;files.append({'path':rel,**info})
                    packed.unlink(missing_ok=True)
                for item in entries:
                    if item[2][0]+len(MAGIC)>MAX_BYTES:
                        flush(batch);batch=[];size=len(MAGIC);files.append({'path':item[1],**self.upload(item[0])});continue
                    if size+item[2][0]>MAX_BYTES:flush(batch);batch=[];size=len(MAGIC)
                    batch.append(item);size+=item[2][0]
                flush(batch)
                # Durable mappings allow an interrupted checkpoint to reuse uploads.
                atomic_json(self.local/'index.json',{str(k):{'info':v} for k,v in self.members.items()})
            files.sort(key=lambda i:i['path'])
            manifest={'schema_version':'working-2','history_complete':False,'created_at':utcnow(),'active_version':stage.name,'current_version':current.name,'files':files}
            path=self.local/'working-manifest.json';atomic_json(path,manifest);size,sha=digest(path)
            fid=self.drive.upload_resuming(self.manifests,sha+'.json',path,self.local/'sessions'/(sha+'.json'))
            if self.verify_object(fid)!=(size,sha):raise PublishError('Working manifest verification failed')
            pointer={'schema_version':'working-2','manifest_file_id':fid,'bytes':size,'sha256':sha,'created_at':utcnow()};pp=self.local/'working-pointer.json';atomic_json(pp,pointer)
            pointers=[i for i in self.drive.children(self.folder) if i['name']=='current.json']
            if len(pointers)>1:raise PublishError('Ambiguous working pointer')
            old_id=pointers[0]['id'] if pointers else None;old=self.drive.read(old_id) if old_id else None
            self.drive.commit_pointer(self.folder,old_id,old,pp)
            atomic_json(self.local/'restored-manifest.json',manifest);atomic_json(self.local/'restored-pointer.json',pointer)
            self.last_remote=time.monotonic()
            if evict:
                mapping={i['path']:i for i in files}
                for path in paths:
                    if path.is_relative_to(stage/'clean') and not module.stub_info(path) and path.stat().st_nlink==1:
                        module.write_stub(path,reference(mapping[path.relative_to(self.root).as_posix()]))
            print(f'DRIVE_WORKING_COMMITTED files={len(files)}; pointer updated last',flush=True)
            return pointer
    module.DriveCache=Cache;module.restore_working=restore
