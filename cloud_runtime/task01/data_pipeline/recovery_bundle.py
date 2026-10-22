"""Verified backward-compatible readers for working-1 and working-2."""
import hashlib, json, os, re, shutil
from pathlib import Path, PurePosixPath
from .drive_publish import digest, PublishError
MAGIC=b'TASK01-BUNDLE-1\n'
MAX_BYTES=4_500_000

def reference(item):return {k:item[k] for k in ['file_id','bytes','sha256','bundle'] if k in item}
def physical(item):
    p=item.get('bundle',item)
    if not isinstance(p.get('bytes'),int) or isinstance(p['bytes'],bool) or p['bytes']<0 or not re.fullmatch('[0-9a-f]{64}',p.get('sha256','')):raise PublishError('Invalid content reference')
    if not re.fullmatch('[A-Za-z0-9_-]+',item.get('file_id','')):raise PublishError('Invalid content file ID')
    if 'bundle' in item and (not isinstance(p.get('offset'),int) or p['offset']<len(MAGIC) or p['offset']+item['bytes']>p['bytes']):raise PublishError('Invalid bundle member bounds')
    return p['bytes'],p['sha256']
def safe_path(value):
    p=PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or '\\' in value or not p.parts or p.parts[0] not in ('metadata','data'):raise PublishError('Unsafe checkpoint path')
    return Path(*p.parts)

class Reader:
    def __init__(self,drive,cache,reserve=2*1024**3,budget=256*1024**2):
        self.drive=drive;self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True);self.reserve=reserve;self.budget=budget;self.checked={}
    def download(self,item,dest):
        expected=(item['bytes'],item['sha256']);size,sha=physical(item);dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(dest.parent).free<self.reserve+expected[0]+size:raise PublishError('Insufficient disk for verified recovery')
        if 'bundle' not in item:
            if self.drive.download(item['file_id'],dest)!=expected:dest.unlink(missing_ok=True);raise PublishError('Recovered content SHA256 mismatch')
            return expected
        bundle=self.cache/(sha+'.bundle')
        stat=lambda p:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns,p.stat().st_ino)
        if not bundle.exists() or self.checked.get(sha)!=stat(bundle):
            if not bundle.exists() or digest(bundle)!=(size,sha):
                for old in sorted(self.cache.glob('*.bundle'),key=lambda p:p.stat().st_mtime):
                    if sum(p.stat().st_size for p in self.cache.glob('*.bundle'))+size<=self.budget:break
                    old.unlink()
                if size>self.budget:raise PublishError('Bundle exceeds bounded cache')
                if self.drive.download(item['file_id'],bundle)!=(size,sha):bundle.unlink(missing_ok=True);raise PublishError('Bundle SHA256 mismatch')
            self.checked[sha]=stat(bundle)
        with bundle.open('rb') as f:
            if f.read(len(MAGIC))!=MAGIC:raise PublishError('Invalid bundle header')
            f.seek(item['bundle']['offset']);data=f.read(item['bytes'])
        if (len(data),hashlib.sha256(data).hexdigest())!=expected:raise PublishError('Bundle member SHA256 mismatch')
        temp=dest.with_name(dest.name+'.bundle-tmp')
        try:
            with temp.open('wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
            os.replace(temp,dest)
        finally:temp.unlink(missing_ok=True)
        return expected
