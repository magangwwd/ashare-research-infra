"""Bounded, paced Drive requests including interrupted response bodies."""
import hashlib, os, random, time, uuid
from pathlib import Path
from threading import Lock
import requests
from email.utils import parsedate_to_datetime

class Pacer:
    def __init__(self):self.lock=Lock();self.next=0
    def wait(self):
        with self.lock:
            delay=self.next-time.monotonic()
            if delay>0:time.sleep(delay)
            self.next=time.monotonic()+8
PACER=Pacer()

def install(module):
    Drive=module.Drive
    class Transient(module.PublishError):pass
    module.DriveTransientError=Transient
    def retry_after(response):
        raw=getattr(response,'headers',{}).get('Retry-After','')
        try:return max(0,float(raw))
        except (ValueError,TypeError):
            try:return max(0,parsedate_to_datetime(raw).timestamp()-time.time())
            except (ValueError,TypeError,OverflowError):return 0
    def request(self,method,url,expected=(200,),**kwargs):
        for attempt in range(3):
            PACER.wait()
            try:r=self.session.request(method,url,timeout=120,**kwargs)
            except requests.RequestException:r=None
            status=r.status_code if r is not None else 'network';reason='unknown'
            if status in expected:
                print(f'DRIVE_API method={method} status={status} streaming={bool(kwargs.get("stream"))}',flush=True)
                return r
            if r is not None:
                try:
                    reasons={x.get('reason') for x in r.json().get('error',{}).get('errors',[])}
                    reason=next((k for k in ['storageQuotaExceeded','dailyLimitExceeded','insufficientFilePermissions','userRateLimitExceeded','rateLimitExceeded'] if k in reasons),'unknown')
                except (ValueError,AttributeError,TypeError):pass
            transient=status in ('network',429,500,502,503,504) or status==403 and reason in ('userRateLimitExceeded','rateLimitExceeded')
            delay=max(retry_after(r),8*2**attempt+random.uniform(0,3))
            if r is not None:r.close()
            if not transient or method not in ('GET','PUT','PATCH') or attempt==2:
                kind=Transient if transient else module.PublishError
                raise kind(f'Drive request failed ({status}); operation={method} files; reason={reason}; no success claimed') from None
            print(f'DRIVE_BACKOFF status={status} reason={reason} seconds={delay:.1f}',flush=True);time.sleep(delay)
    def consume(self,file_id,callback):
        for attempt in range(3):
            r=self.request('GET',f'{module.API}/{file_id}',params={'alt':'media','supportsAllDrives':'true'},stream=True)
            try:return callback(r)
            except requests.RequestException:
                if attempt==2:raise Transient('Drive request failed (network); operation=GET files; reason=stream_interrupted') from None
                delay=8*2**attempt+random.uniform(0,3);print(f'DRIVE_STREAM_BACKOFF seconds={delay:.1f}',flush=True);time.sleep(delay)
            finally:r.close()
    def remote_digest(self,file_id):
        def read(r):
            h=hashlib.sha256();size=0
            for block in r.iter_content(1024*1024):h.update(block);size+=len(block)
            return size,h.hexdigest()
        return consume(self,file_id,read)
    def download(self,file_id,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.download-'+uuid.uuid4().hex)
        def read(r):
            h=hashlib.sha256();size=0
            with temp.open('wb') as out:
                for block in r.iter_content(1024*1024):out.write(block);h.update(block);size+=len(block)
                out.flush();os.fsync(out.fileno())
            os.replace(temp,path);return size,h.hexdigest()
        try:return consume(self,file_id,read)
        finally:temp.unlink(missing_ok=True)
    def children(self,parent):
        result=[];params={'q':f"'{parent}' in parents and trashed=false",'pageSize':1000,'fields':'nextPageToken,files(id,name,mimeType,size,parents,trashed,sha256Checksum)','supportsAllDrives':'true','includeItemsFromAllDrives':'true'}
        while True:
            d=self.request('GET',module.API,params=params).json();result.extend(d.get('files',[]))
            if not d.get('nextPageToken'):return result
            params['pageToken']=d['nextPageToken']
    Drive.request=request;Drive.read=lambda self,fid:consume(self,fid,lambda r:r.content)
    Drive.download=download;Drive.remote_digest=remote_digest;Drive.children=children
