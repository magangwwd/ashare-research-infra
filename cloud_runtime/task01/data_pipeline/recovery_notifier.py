"""WeChat status reports; inactivity alerts remain disabled."""
import argparse,fcntl,json,os,time
from datetime import datetime,timezone
from pathlib import Path
import requests
from .manifest import read_json,atomic_json

def send(text):
    token=os.environ.get('PUSHPLUS_TOKEN')
    if not token:return False
    try:
        r=requests.post('https://www.pushplus.plus/send',json={'token':token,'title':'A股回补状态','content':text,'template':'txt'},timeout=20,allow_redirects=False)
        return r.status_code==200 and r.json().get('code')==200
    except Exception:return False

def tick(runtime,root,now=None):
    now=now or time.time();h=read_json(runtime/'heartbeat.json');state=read_json(runtime/'wechat-state.json')
    try:fresh=now-datetime.fromisoformat(h['updated_at']).timestamp()<180
    except (ValueError,KeyError,TypeError):fresh=False
    status=h.get('status','UNKNOWN') if fresh else 'UNKNOWN'
    last=state.get('recovery_last_status');due=now-state.get('recovery_periodic_at',0)>=4*3600
    alert=status in ('BLOCKED','STOPPED') and status!=last
    if (due or alert) and now-state.get('recovery_attempted_at',0)>=300:
        plan=read_json(root/'metadata/manifest/history_scope_plan.json');total=plan.get('total_logical_history_requests')
        restored=(root/'metadata/manifest/requests.json').exists();count=None
        if restored:count=len(read_json(root/'metadata/manifest/requests.json'))
        text='程序状态：'+status+'\n'+('正在恢复并验证 Drive checkpoint。' if not restored else '已恢复 checkpoint，正在启动或回补。')
        if count is not None:text+='\n请求回执：'+str(count)
        if count is not None and total:text+=f'\n回执/逻辑计划总数：约 {count/total:.2%}（非数据量或最终验收比例）'
        text+='\n范围：其它数据2020年至今；15分钟2026年至今。\n剩余时长：恢复稳定吞吐后再估算。'
        state['recovery_attempted_at']=now;ok=send(text);state['last_send_accepted']=ok
        if ok:state.update(recovery_periodic_at=now,recovery_last_status=status)
        atomic_json(runtime/'wechat-state.json',state)
    atomic_json(runtime/'wechat-heartbeat.json',{'pid':os.getpid(),'updated_at':datetime.now(timezone.utc).isoformat(),'observed_status':status,'last_send_accepted':state.get('last_send_accepted'),'inactivity_alerts_enabled':False})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('/workspace/ashare-task01-resumed-20261018'));parser.add_argument('--runtime',type=Path,default=Path('/workspace/task01-runtime'));a=parser.parse_args()
    with (a.runtime/'wechat.lock').open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        while True:
            try:tick(a.runtime,a.root)
            except Exception:pass
            time.sleep(30)
if __name__=='__main__':main()
