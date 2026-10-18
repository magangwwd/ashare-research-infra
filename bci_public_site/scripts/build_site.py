#!/usr/bin/env python3
"""Build publicly distributable *metadata only* site dataset from verified Drive archive.
No medical advice, copyrighted abstracts, patient data, private credentials, or Drive links.
"""
from __future__ import annotations
import argparse, io, json, hashlib, re, sys, zipfile
from pathlib import Path
from datetime import datetime, timezone

EXPECTED = {
  'pubmed/recent_articles.jsonl','pubmed/case_reports.jsonl','pubmed/survivorship_reviews.jsonl',
  'clinical_trials/recently_updated.jsonl','official_sources/fda_breast_approval_links.jsonl',
  'official_sources/source_checks.jsonl','guidelines/verified_publication_index.jsonl'
}

def sha(raw): return hashlib.sha256(raw).hexdigest()
def read_inner(outer_bytes):
    with zipfile.ZipFile(io.BytesIO(outer_bytes)) as outer:
        zip_files=[n for n in outer.namelist() if re.fullmatch(r'(?:data_snapshots/)?\d{8}T\d{6}Z_[A-Za-z0-9_-]+\.zip',n)]
        if zip_files:
            if len(zip_files)!=1: raise ValueError('Ambiguous nested snapshot archives')
            blob=outer.read(zip_files[0])
            return blob,zip_files[0]
        return outer_bytes,'raw_snapshot'

def normalize_article(item,category):
    pmid=str(item.get('pmid') or '')
    if not re.fullmatch(r'\d{1,10}',pmid): raise ValueError('Invalid PMID')
    # Public metadata ONLY, deliberately exclude potentially copyrighted abstracts.
    return {'id':pmid, 'category':category,'title':item.get('title') or '未提供标题',
            'journal':item.get('journal') or '', 'year':item.get('published_year') or '',
            'doi':item.get('doi') or '', 'types':item.get('publication_types') or [],
            'url':'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/'}

def normalize_trial(item):
    nct=str(item.get('nct_id') or '')
    if not re.fullmatch(r'NCT\d{8}',nct): raise ValueError('Invalid NCT identifier')
    return {'id':nct,'title':item.get('title') or '', 'date':item.get('last_update_posted') or '',
            'status':item.get('status') or 'UNKNOWN', 'phases':item.get('phases') or [],
            'conditions':item.get('conditions') or [],'studyType':item.get('study_type') or '',
            'enrollment':item.get('enrollment') or {},'url':'https://clinicaltrials.gov/study/'+nct}

def safe_https(value):
    v=str(value or '')
    if not v.startswith('https://') or len(v)>1024: raise ValueError('Unsafe URL')
    return v

def build(archive, target):
    raw=Path(archive).read_bytes()
    if len(raw)>25_000_000: raise ValueError('Archive too large')
    inner,source_format=read_inner(raw)
    with zipfile.ZipFile(io.BytesIO(inner)) as z:
        names=set(z.namelist())
        if not EXPECTED.issubset(names) or 'manifest.json' not in names:
            raise ValueError('Incomplete snapshot archive')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('status')!='pass_core': raise ValueError('Source snapshot failed core validation')
        manifest_entry_map={Path(x['path']).name:(int(x['count']),x['sha256']) for x in manifest['files']}
        datasets={}
        for name in EXPECTED:
            b=z.read(name)
            rows=[json.loads(s) for s in b.decode('utf-8').splitlines() if s.strip()]
            expected_count,expected_sha=manifest_entry_map[Path(name).name]
            if len(rows)!=expected_count or sha(b)!=expected_sha: raise ValueError('Mismatch for '+name)
            datasets[name]=rows
    docs=[]
    for path,cat in [('pubmed/recent_articles.jsonl','recent'),('pubmed/case_reports.jsonl','case'),('pubmed/survivorship_reviews.jsonl','recovery')]:
        docs.extend(normalize_article(x,cat) for x in datasets[path])
    trials=[normalize_trial(x) for x in datasets['clinical_trials/recently_updated.jsonl']]
    fda=[{'title':x['headline'],'url':safe_https(x['official_url']),
          'status':'美国 FDA 官方公告链接，获批日期及适应证尚待逐条核实'}
         for x in datasets['official_sources/fda_breast_approval_links.jsonl']]
    guidelines=[{'title':x['title'],'doi':x.get('doi') or '',
       'publisher':x.get('issued_by') or '', 'url':safe_https(x['url']),
       'note':'指南出版信息；不代表全文授权公开或个体医疗建议'}
      for x in datasets['guidelines/verified_publication_index.jsonl']]
    source_checks=[{'name':x['id'],'status':x['status'],'httpStatus':x.get('http_status'),
       'url':safe_https(x['url'])} for x in datasets['official_sources/source_checks.jsonl']]
    data={'schemaVersion':1,'siteName':'乳腺癌医学证据观察站',
      'snapshotId':manifest['snapshot_id'],'collectedAt':manifest['captured_at_utc'],
      'generatedAt':datetime.now(timezone.utc).isoformat(),
      'archiveSha256':sha(raw), 'nestedSnapshotSha256':sha(inner),
      'scope':'最近一次云盘有效快照中的有限抽样；非全量医学数据库，资料尚未经专业医学审核。',
      'counts':{'recent':sum(a['category']=='recent' for a in docs),'case':sum(a['category']=='case' for a in docs),
        'recovery':sum(a['category']=='recovery' for a in docs),'trials':len(trials),
        'fda':len(fda),'guidelines':len(guidelines),'sources':len(source_checks)},
      'articles':docs,'trials':trials,'fda':fda,'guidelines':guidelines,'sources':source_checks,
      'excluded':['患者个人病历与未授权病例全文','医院未经核实的年收治量与治疗质量排名','未经专业审核的个体治疗方案'],
      'dataWarnings':[
        '来自公开官方和文献数据库；内容为研究索引，不构成医疗建议。',
        '临床试验登记状态不是疗效证明，且不表示在中国可参与。',
        'FDA公告为美国监管资料，不能代表中国NMPA适应证。',
        '文献仅展示题录元数据及来源链接；摘要请访问原始来源。',
        '检索采用有限时间窗及最大返回条数，并非完整历史数据。'
      ],
      'coverage':{'pubmed30DayMatches':manifest.get('sources',{}).get('pubmed_recent_articles',{}).get('total_matching'),
        'trialMatches':manifest.get('sources',{}).get('clinical_trials',{}).get('total_matching'),
        'recoveryMissingPmids':manifest.get('sources',{}).get('pubmed_survivorship_reviews',{}).get('unresolved_pmids',[])},
      'provenance':{'source':'Google Drive 已核验的公开医学研究快照',
        'reviewed':'not_medically_reviewed'}}
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'out':str(target),'snapshot':data['snapshotId'],
      'counts':data['counts'],'sha256':sha(target.read_bytes()),'source_format':source_format},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--out',required=True)
    x=p.parse_args();build(x.zip,x.out)
