"""Optional passive external uptime probe. No external check is created here."""
import argparse,fcntl,os,time
from pathlib import Path
from urllib.parse import urlparse
from .runtime_lifecycle import collect,atomic_json,utc,DEFAULT_ROOT,DEFAULT_RUNTIME


def valid_ping_url(value):
    try:
        url=urlparse(value)
        return (url.scheme=='https' and url.hostname=='hc-ping.com' and not url.username and not url.password and url.port in (None,443) and len(url.path.strip('/'))>=20 and not url.query and not url.fragment)
    except ValueError:return False


def eligible(snapshot):
    # Observe environment and supervision, including intentional cooling/pauses.
    # Download progress is deliberately irrelevant to an uptime heartbeat.
    return len(snapshot['services']['watchdog'])==1 and snapshot['guard_heartbeat']['fresh']


def ping(runtime,root,url,sender=None):
    if not valid_ping_url(url):raise ValueError('Invalid external heartbeat configuration')
    snapshot=collect(runtime,root)
    if not eligible(snapshot):
        atomic_json(runtime/'external-heartbeat.json',{'checked_at':utc(),'status':'WITHHELD','reason':'No fresh live watchdog','endpoint_recorded':False});return False
    if sender is None:
        import requests
        sender=requests.post
    ok=False
    try:
        response=sender(url,data=b'ashare runtime and watchdog alive',timeout=20,allow_redirects=False);ok=response.status_code==200;response.close()
    except Exception:pass
    atomic_json(runtime/'external-heartbeat.json',{'checked_at':utc(),'status':'PING_ACCEPTED' if ok else 'PING_FAILED','endpoint_recorded':False})
    return ok


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=DEFAULT_ROOT);p.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME);a=p.parse_args();url=os.environ.get('HEALTHCHECKS_PING_URL')
    if not url:print('EXTERNAL_MONITOR_NOT_CONFIGURED');return
    if not valid_ping_url(url):print('EXTERNAL_MONITOR_INVALID_CONFIGURATION');return
    with (a.runtime/'external-heartbeat.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        while True:
            try:ping(a.runtime,a.root,url)
            except Exception as exc:print('EXTERNAL_HEARTBEAT_ERROR '+type(exc).__name__,flush=True)
            time.sleep(120)
if __name__=='__main__':main()
