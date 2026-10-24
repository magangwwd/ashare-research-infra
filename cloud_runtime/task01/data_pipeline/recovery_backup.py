"""Durable credential-free source backup, separate from data current pointers."""
import hashlib,io,json,tarfile
from pathlib import Path
from .manifest import atomic_json,read_json,utcnow
from .drive_publish import digest,PublishError

def ensure_source_backup(drive,target,root):
    repo=Path(__file__).resolve().parents[1];out=Path(root)/'outputs/source-backup';out.mkdir(parents=True,exist_ok=True)
    files=sorted(set(list((repo/'data_pipeline').glob('*.py'))+list((repo/'data_pipeline/recovered_oct08').glob('*.bin'))+[repo/'data_pipeline/recovered_oct08/manifest.json',repo/'requirements.txt',repo/'config/config.yaml',repo/'docs/recovery-20261018.md']))
    archive=out/'recovery-code.tar'
    with tarfile.open(archive,'w') as tar:
        for path in files:
            if path.name.startswith('test_'):continue
            payload=path.read_bytes();info=tarfile.TarInfo(path.relative_to(repo).as_posix());info.size=len(payload);info.mode=0o600;info.mtime=0;tar.addfile(info,io.BytesIO(payload))
    size,sha=digest(archive);proof=read_json(out/'verified.json')
    if proof.get('sha256')==sha:return proof
    folder=drive.folder(target,'runtime-code-backups');name=sha+'-'+str(size)+'.tar'
    found=[i for i in drive.children(folder) if i['name']==name]
    if len(found)>1:raise PublishError('Ambiguous source backup')
    sessions=out/'sessions';sessions.mkdir(exist_ok=True)
    fid=found[0]['id'] if found else drive.upload_resuming(folder,name,archive,sessions/(sha+'.json'))
    if drive.verified_digest(fid)!=(size,sha):raise PublishError('Source backup SHA256 mismatch')
    proof={'file_id':fid,'bytes':size,'sha256':sha,'verified_at':utcnow(),'python':'3.12','credentials_included':False}
    atomic_json(out/'verified.json',proof)
    print(f'RECOVERY_CODE_BACKUP_VERIFIED bytes={size}; credential-free source preserved',flush=True)
    return proof
