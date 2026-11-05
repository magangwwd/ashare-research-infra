"""Isolated Actions runner: read Gold, persist verified immutable snapshots."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .data import load_gold, scan, day_bars, track_events, sha
from .engine import initial_state, step, digest

CODE=Path(__file__).resolve().parent


def canonical(doc):
    return (json.dumps(doc,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def rc(args, root_id, binary=False):
    cmd=['rclone',*map(str,args),'--drive-root-folder-id',root_id,
         '--retries','3','--low-level-retries','3','--contimeout','30s','--timeout','2m']
    # Never print config, credentials, stdout data, positions or candidate lists.
    r=subprocess.run(cmd,capture_output=True,check=False,timeout=1500)
    if r.returncode:
        raise RuntimeError('rclone_operation_failed_'+str(r.returncode)+'_'+str(args[0]))
    return r.stdout if binary else r.stdout.decode()


def exists(name,cfg):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):
        raise ValueError('invalid_output_name')
    rows=json.loads(rc(['lsjson','gdrive:','--max-depth','1','--files-only'],cfg['output_folder_id']))
    found=[r for r in rows if r['Name']==name]
    if len(found)>1:
        raise ValueError('duplicate_drive_output_name')
    return bool(found)


def read_previous(cfg):
    if not exists('current_actions.json',cfg):
        return None,None
    pointer=json.loads(rc(['cat','gdrive:current_actions.json'],cfg['output_folder_id'],True))
    if pointer.get('schema')!='sdm_pointer_v1' or not re.fullmatch(r'actions_snapshot_[A-Za-z0-9_.-]+\.json',pointer.get('name','')):
        raise ValueError('invalid_previous_pointer')
    raw=rc(['cat','gdrive:'+pointer['name']],cfg['output_folder_id'],True)
    if hashlib.sha256(raw).hexdigest()!=pointer['sha256']:
        raise ValueError('previous_snapshot_hash_mismatch')
    bundle=json.loads(raw)
    if bundle.get('schema')!='sdm_bundle_v1' or bundle['config_sha256']!=digest(cfg):
        raise ValueError('incompatible_state_config')
    return bundle,pointer


def download_gold(dest,cfg):
    root=cfg['gold_folder_id']
    before=rc(['cat','gdrive:manifest.json'],root,True)
    include=['--include','/manifest.json','--include','/instruments.parquet',
             '--include','/daily_snapshot.parquet','--include','/name_history.parquet',
             '--include','/trading_calendar.parquet']
    for dataset in ('daily_prices','adjustment_factors','daily_limits'):
        include+=['--include','/'+dataset+'/**']
    rc(['copy','gdrive:',dest,*include,'--transfers','6','--checkers','12'],root)
    # Drive MD5 verifies transfer bytes; snapshot records SHA256 of each local file.
    rc(['check','gdrive:',dest,*include,'--one-way','--checksum'],root)
    after=rc(['cat','gdrive:manifest.json'],root,True)
    if before!=after or (Path(dest)/'manifest.json').read_bytes()!=after:
        raise ValueError('gold_changed_during_download')


def render_report(bundle):
    q=bundle['quality']
    lines=['# A股供需动量：条件信号与模拟跟踪',
           '',f"数据截止：{q['as_of']}；状态：{q['status']}。",'',
           '当前为修正后规则的研究模拟，历史1022.7%收益尚未复现；以下不代表真实持仓或成交。',
           '',f"本轮候选 {len(bundle['signals'])} 只；模拟持仓 {len(bundle['state']['positions'])} 只。",'']
    if not bundle['signals']:
        lines.append('无合适新候选，继续持有现金或跟踪已有模拟持仓。')
    for s in bundle['signals']:
        lines.append(f"- {s['code']} {s['name']}：{s['score']}分；下一交易日开盘条件限价≤{s['entry_limit']:.2f}，止损参考{s['stop']:.2f}，止盈参考{s['target']:.2f}；每仓费用合计≤30000元，满3仓跳过。")
    lines+=['','买入提醒仅在新信号出现时发送；卖出提醒依据模拟持仓。真实买入后需另行提供实际成交，系统不会把提醒当成交。',
            '采用日线收盘后跟踪，盘中触价不会即时推送；无法成交则保留风险，绝不假称第5日强制成交。',
            '',f"代码：{bundle['github_sha']}；运行：{bundle['run_id']}；规则：{bundle['config_sha256']}。"]
    return '\n'.join(lines)+'\n'


def publish(bundle,previous_pointer,work,cfg):
    name='actions_snapshot_'+bundle['quality']['as_of'].replace('-','')+'_'+bundle['run_id']+'.json'
    if exists(name,cfg):
        raise ValueError('immutable_run_already_exists')
    path=work/name
    path.write_bytes(canonical(bundle))
    rc(['copyto',path,'gdrive:'+name],cfg['output_folder_id'])
    readback=rc(['cat','gdrive:'+name],cfg['output_folder_id'],True)
    if readback!=path.read_bytes():
        raise ValueError('snapshot_remote_readback_failed')
    # Same shared writer concurrency group prevents competing deployed writers.
    _,current=read_previous(cfg)
    if current!=previous_pointer:
        raise ValueError('pointer_changed_during_computation')
    report=work/('actions_report_'+bundle['run_id']+'.md')
    report.write_text(render_report(bundle),encoding='utf-8')
    rc(['copyto',report,'gdrive:'+report.name],cfg['output_folder_id'])
    if rc(['cat','gdrive:'+report.name],cfg['output_folder_id'],True)!=report.read_bytes():
        raise ValueError('report_remote_readback_failed')
    pointer=dict(schema='sdm_pointer_v1',name=name,sha256=sha(path),as_of=bundle['quality']['as_of'],
                 run_id=bundle['run_id'],github_sha=bundle['github_sha'],report_name=report.name,
                 mode=cfg['mode'],previous=previous_pointer)
    # Keep history bounded in the pointer; immutable snapshots remain intact.
    if pointer['previous']:
        pointer['previous']=dict(previous_pointer)
        pointer['previous'].pop('previous',None)
    marker=work/'current_actions.json'
    marker.write_bytes(canonical(pointer))
    rc(['copyto',marker,'gdrive:current_actions.json'],cfg['output_folder_id'])
    if rc(['cat','gdrive:current_actions.json'],cfg['output_folder_id'],True)!=marker.read_bytes():
        raise ValueError('pointer_remote_readback_failed')
    return pointer


def execute(work,cfg,previous=None,gold=None):
    import pandas as pd
    root=Path(gold) if gold else work/'gold'
    if gold is None:
        download_gold(root,cfg)
    prices,opens,quality=load_gold(root,cfg)
    state=previous['state'] if previous else initial_state(cfg)
    asof=quality['as_of']
    if state['last_date'] and state['last_date']>asof:
        raise ValueError('source_older_than_previous_state')
    if state['last_date']==asof:
        return None
    # No fabricated history: first activation starts at the current close.
    days=opens[(opens>pd.Timestamp(state['last_date'])) & (opens<=pd.Timestamp(asof))] if state['last_date'] else [pd.Timestamp(asof)]
    alerts=[]
    signals=scan(prices,asof,cfg)
    for d in days:
        day=str(d.date())
        bars=day_bars(prices,day,[p['code'] for p in state['positions']])
        state,new=step(state,day,bars,signals if day==asof else [],cfg)
        alerts.extend(new)
    return dict(schema='sdm_bundle_v1',mode=cfg['mode'],real_orders_enabled=False,
                generated_at=datetime.now(timezone.utc).isoformat(),
                github_sha=os.getenv('GITHUB_SHA','local'),
                run_id=os.getenv('GITHUB_RUN_ID','local')+'-'+os.getenv('GITHUB_RUN_ATTEMPT','1'),
                config_sha256=digest(cfg),quality=quality,state=state,signals=signals,
                alerts=alerts,tracking=track_events(state['events'],prices,opens,asof),
                strategy_effectiveness='NOT_VALIDATED',
                limitations=['Daily OHLC paper fills are estimates; no intraday alert delivery.',
                    'Opening limit entry, known-zone timing, point-in-time top300 and accounting were corrected; old results do not apply.',
                    'Corporate actions on held shares block ledger progression until independently settled.',
                    'No actual holdings were supplied; all positions in this file are simulated.'])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--local-gold',type=Path)
    p.add_argument('--no-publish',action='store_true')
    args=p.parse_args()
    cfg=json.loads((CODE/'config.json').read_text())
    if cfg['real_orders_enabled'] or cfg['mode']!='RESEARCH_PAPER_ONLY':
        raise ValueError('unsupported_execution_mode')
    work=Path(tempfile.mkdtemp(prefix='sdm-',dir=os.getenv('RUNNER_TEMP')))
    previous,pointer=read_previous(cfg) if not args.no_publish else (None,None)
    try:
        bundle=execute(work,cfg,previous,args.local_gold)
        if bundle is None:
            print('UNCHANGED_VALIDATED_DATE: skipped duplicate calculation/publication')
            return 0
        if args.no_publish:
            (work/'bundle.json').write_bytes(canonical(bundle))
            print('LOCAL_RESEARCH_RUN_COMPLETE')
        else:
            result=publish(bundle,pointer,work,cfg)
            print('VERIFIED_DRIVE_PUBLICATION',result['run_id'],result['sha256'])
        return 0
    except Exception as exc:
        # Diagnostics contain neither credentials nor portfolio details.
        status=dict(schema='sdm_failure_v1',status='FAILED_NO_NEW_SIGNAL',
                    time=datetime.now(timezone.utc).isoformat(),reason=str(exc)[:240],
                    github_sha=os.getenv('GITHUB_SHA'),run_id=os.getenv('GITHUB_RUN_ID'))
        print(json.dumps(status,ensure_ascii=False))
        if not args.no_publish:
            try:
                path=work/('actions_failure_'+str(os.getenv('GITHUB_RUN_ID','local'))+'.json')
                path.write_bytes(canonical(status))
                rc(['copyto',path,'gdrive:'+path.name],cfg['output_folder_id'])
            except Exception:
                print('FAILURE_DIAGNOSTIC_UPLOAD_UNAVAILABLE')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
