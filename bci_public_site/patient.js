'use strict';
(function(){
  const $=id=>document.getElementById(id);
  const SOURCE_HOSTS=new Set(['pubmed.ncbi.nlm.nih.gov','clinicaltrials.gov','www.fda.gov','www.cancer.gov','www.china-oncology.com']);
  const CATEGORIES={
    all:'全部',recent:'近期研究',recovery:'康复与随访',case:'公开病例题录',
    trials:'临床试验登记',fda:'美国 FDA 公告',guides:'诊疗指南'
  };
  const TRIAL_STATUS={
    RECRUITING:'招募中',NOT_YET_RECRUITING:'尚未开始招募',
    ACTIVE_NOT_RECRUITING:'进行中 · 暂不招募',COMPLETED:'已完成',
    TERMINATED:'已终止',SUSPENDED:'暂停',WITHDRAWN:'撤回',
    ENROLLING_BY_INVITATION:'邀请入组'
  };
  const state={filter:'all',query:'',shown:10,items:[],data:null};
  function externalLink(label,href){
    const a=document.createElement('a');
    a.textContent=String(label||'阅读原文');
    try{
      const u=new URL(String(href));
      if(u.protocol!=='https:' || !SOURCE_HOSTS.has(u.hostname))throw Error('invalid source');
      a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';
    }catch{a.removeAttribute('href');a.title='未核实的来源链接';}
    return a;
  }
  function dateText(iso){
    const d=new Date(iso);
    if(!iso||Number.isNaN(d.getTime()))return '未知';
    return d.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',
      hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）';
  }
  function totalAgeDays(iso){
    const d=new Date(iso);return Number.isNaN(d.getTime())?null:Math.max(0,Math.floor((Date.now()-d.getTime())/86400000));
  }
  function addRecovery(parent,item){
    const row=document.createElement('article');row.className='update-card';
    const kicker=document.createElement('div');kicker.className='update-kicker';kicker.textContent='康复研究 · 原文题录';
    const title=document.createElement('h3');title.append(externalLink(item.title||'查看原文',item.url));
    const meta=document.createElement('p');meta.className='update-meta';
    meta.textContent=[item.journal,item.year?'期刊记录年份 '+item.year:'',item.id?'PMID '+item.id:''].filter(Boolean).join(' · ');
    row.append(kicker,title,meta);parent.append(row);
  }
  function flatten(d){
    const list=[];
    for(const a of d.articles){
      if(!['recent','recovery','case'].includes(a.category))continue;
      list.push({kind:a.category,title:a.title||'未提供标题',url:a.url,
        details:[a.journal||'PubMed 公开题录',a.year?'期刊年份 '+a.year:null,a.id?'PMID '+a.id:null],
        search:[a.title,a.journal,a.doi,a.id].filter(Boolean).join(' ').toLowerCase(),
        note:a.category==='case'?'公开病例文献索引；不含患者私人病历。':a.category==='recovery'?'仅供研究了解，不能据此选择个人康复方案。':'论文发表不等于已经形成有效治疗。'});
    }
    for(const t of d.trials){
      list.push({kind:'trials',title:t.title||'试验登记',url:t.url,
        details:[t.id,t.date?'登记信息更新 '+t.date:null,TRIAL_STATUS[t.status]||t.status||'状态未提供'],
        search:[t.title,t.id,t.status,...(t.conditions||[])].filter(Boolean).join(' ').toLowerCase(),
        note:'试验登记与招募状态不能证明治疗获益或在中国可参加。'});
    }
    for(const f of d.fda){
      list.push({kind:'fda',title:f.title||'监管公告',url:f.url,
        details:['美国 FDA · 公告索引','具体日期与适应证需进一步核验'],
        search:[f.title].filter(Boolean).join(' ').toLowerCase(),
        note:'美国监管公告不等于中国 NMPA 已批准。'});
    }
    for(const g of d.guidelines){
      list.push({kind:'guides',title:g.title||'临床指南出版条目',url:g.url,
        details:['指南出版索引',g.doi?'DOI '+g.doi:null],
        search:[g.title,g.doi,g.publisher].filter(Boolean).join(' ').toLowerCase(),
        note:'收录正式出版信息，不提供未经审核的治疗方案解读。'});
    }
    return list;
  }
  function recordCard(item){
    const card=document.createElement('article');card.className='cloud-record';
    const header=document.createElement('div');header.className='cloud-record-head';
    const category=document.createElement('span');category.className='cloud-record-type';category.textContent=CATEGORIES[item.kind]||'公开医学资料';
    header.append(category);
    const title=document.createElement('h4');title.append(externalLink(item.title,item.url));
    const details=document.createElement('p');details.className='cloud-record-meta';details.textContent=item.details.filter(Boolean).join(' · ');
    const note=document.createElement('p');note.className='cloud-record-note';note.textContent=item.note;
    const go=externalLink('查看官方或期刊原文 ↗',item.url);go.className='cloud-record-go';
    card.append(header,title,details,note,go);return card;
  }
  function renderFeed(){
    const filtered=state.items.filter(item=>(state.filter==='all'||item.kind===state.filter||
      (state.filter==='articles'&&['recent','recovery','case'].includes(item.kind)))&&item.search.includes(state.query));
    const feed=$('cloud-feed');feed.replaceChildren();
    $('cloud-matches').textContent='当前分类符合条件 '+filtered.length+' 条 / 快照收录 '+state.items.length+' 条';
    for(const item of filtered.slice(0,state.shown))feed.append(recordCard(item));
    if(!filtered.length){
      const no=document.createElement('p');no.className='loading';
      no.textContent='当前云盘快照中没有符合条件的记录。';feed.append(no);
    }
    $('cloud-more').hidden=filtered.length<=state.shown;
    $('cloud-more').textContent='继续查看（已显示 '+Math.min(state.shown,filtered.length)+' / '+filtered.length+'） ↓';
    document.querySelectorAll('[data-cloud-filter]').forEach(btn=>{
      const active=btn.dataset.cloudFilter===state.filter || (state.filter==='articles'&&btn.dataset.cloudFilter==='articles');
      btn.classList.toggle('active',active);
      if(btn.classList.contains('cloud-filter'))btn.setAttribute('aria-pressed',String(active));
    });
  }
  function setFilter(filter){
    if(!(filter in CATEGORIES)&&filter!=='articles')return;
    state.filter=filter;state.shown=10;renderFeed();
    if(filter==='articles')document.getElementById('cloud-data').scrollIntoView({behavior:'smooth',block:'start'});
  }
  function fillCloud(d){
    $('cloud-collected').textContent=dateText(d.collectedAt);
    $('cloud-generated').textContent=dateText(d.generatedAt);
    $('cloud-snapshot').textContent=d.snapshotId||'未提供';
    const count=d.counts||{};
    $('cloud-count-articles').textContent=(Number(count.recent)||0)+(Number(count.case)||0)+(Number(count.recovery)||0);
    $('cloud-count-trials').textContent=Number(count.trials)||0;
    $('cloud-count-fda').textContent=Number(count.fda)||0;
    $('cloud-count-guides').textContent=Number(count.guidelines)||0;
    const age=totalAgeDays(d.collectedAt);
    let freshness='数据采集时间未知';
    if(age!==null) freshness=age===0?'今日有已核验数据快照':age<=2?'最近 '+age+' 天内采集':age<=7?'快照距今 '+age+' 天 · 请核对时效':'快照距今 '+age+' 天 · 数据较旧';
    $('cloud-freshness').textContent=freshness;
    $('cloud-freshness').classList.toggle('stale',age===null||age>7);
    $('snapshot-date').textContent='资料采集：'+dateText(d.collectedAt);
    $('footer-update').textContent='当前云盘快照采集：'+dateText(d.collectedAt);
    $('data-note').textContent=(d.scope||'当前仅有有限样本。')+
      ' 康复研究仅列公开题录，不构成医学治疗建议。'+
      (age!==null&&age>7?' 当前快照已超过一周，请优先核对原始来源。':'');
    const sourceNotice=age!==null&&age>7?'本快照已较久未更新；页面的每日刷新不会自动产生新研究数据。':
      '网页定期从云盘读取最新符合校验要求的快照；资料有变化才会出现新的记录。';
    $('cloud-qualifier').textContent=sourceNotice+' 所有分类均为有限采样、未经独立医学审核；不进行医院疗效与医生排名。';
    state.items=flatten(d);renderFeed();
    const rec=d.articles.filter(a=>a.category==='recovery'&&
      (a.url||'').startsWith('https://pubmed.ncbi.nlm.nih.gov/'));
    const holder=$('patient-updates');holder.replaceChildren();
    if(rec.length)rec.slice(0,3).forEach(item=>addRecovery(holder,item));
    else {const no=document.createElement('p');no.className='loading';no.textContent='没有符合条件的康复研究题录。';holder.append(no);}
  }
  function errorState(){
    for(const id of ['cloud-collected','cloud-generated','cloud-snapshot'])$(id).textContent='暂不可用';
    for(const id of ['cloud-count-articles','cloud-count-trials','cloud-count-fda','cloud-count-guides'])$(id).textContent='—';
    $('cloud-freshness').textContent='数据载入失败';
    $('cloud-freshness').classList.add('stale');
    $('cloud-matches').textContent='无法确认数据数量';
    const feed=$('cloud-feed');feed.replaceChildren();
    const message=document.createElement('p');message.className='loading';
    message.textContent='云盘公开快照未能载入。请稍后重试，或先阅读下方权威医学原文。';
    feed.append(message);$('cloud-more').hidden=true;
    $('patient-updates').replaceChildren(message.cloneNode(true));
    $('snapshot-date').textContent='数据暂不可用';$('footer-update').textContent='数据暂不可用';
    $('cloud-qualifier').textContent='无法确认数据更新时间；不能把上次已保存的结果当作最新数据。';
  }
  document.querySelectorAll('[data-cloud-filter]').forEach(btn=>btn.addEventListener('click',()=>setFilter(btn.dataset.cloudFilter)));
  $('cloud-search').addEventListener('input',evt=>{state.query=evt.target.value.trim().toLowerCase();state.shown=10;renderFeed();});
  $('cloud-more').addEventListener('click',()=>{state.shown+=10;renderFeed();});
  async function load(){
    try{
      const response=await fetch('./data/site-data.json',{cache:'no-store'});
      if(!response.ok)throw Error('HTTP status '+response.status);
      const d=await response.json();
      if(d.schemaVersion!==1||!Array.isArray(d.articles)||!Array.isArray(d.trials)||
         !Array.isArray(d.fda)||!Array.isArray(d.guidelines)||!d.counts)throw Error('Invalid snapshot schema');
      state.data=d;fillCloud(d);
    }catch{errorState();}
  }
  load();
})();
