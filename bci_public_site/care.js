'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const state={guide:null,research:null,stage:null,region:'all',focus:'all',term:'',compare:new Set()};
  const APPROVED_HOSTS=new Set(['www.shca.org.cn','www.cicams.ac.cn','www.bjcancer.org','bjcancer.org','www.sysucc.org.cn','xuebao.301hospital.com.cn','www.cancer.gov','www.china-oncology.com','pubmed.ncbi.nlm.nih.gov','clinicaltrials.gov','www.fda.gov']);
  const CATEGORIES={
    screen:'发现异常或考虑筛查',
    diagnosis:'检查和病理结果',
    plan:'治疗方案讨论',
    recovery:'术后随访和康复'
  };
  function e(tag,cls,content){
    const a=document.createElement(tag);
    if(cls)a.className=cls;
    if(content!==undefined)a.textContent=String(content);
    return a;
  }
  function href(url){
    try {
      const u=new URL(url);
      if(u.protocol!=='https:'||!APPROVED_HOSTS.has(u.hostname)||u.username||u.password)throw Error('invalid');
      return u.href;
    }catch{return null;}
  }
  function ext(label,url,cls){
    const a=e('a',cls,label);const valid=href(url);
    if(valid){a.href=valid;a.rel='noopener noreferrer';a.target='_blank';}
    else {a.setAttribute('aria-disabled','true');a.title='无法验证来源链接';}
    return a;
  }
  function renderStage(stageId){
    if(!state.guide)return;
    state.stage=stageId;
    const target=state.guide.journeys.find(x=>x.id===stageId)||state.guide.journeys[0];
    if(!target)return;
    document.querySelectorAll('.care-stage-btn').forEach(b=>{
      const on=b.dataset.stage===target.id;
      b.classList.toggle('active',on);
      b.setAttribute('aria-pressed',String(on));
    });
    $('care-stage-name').textContent=target.title;
    $('care-stage-desc').textContent=target.brief;
    const list=$('care-steps');list.replaceChildren();
    (target.steps||[]).forEach((step,i)=>{const li=e('li','',step);li.dataset.number=String(i+1);list.append(li);});
    const ask=$('care-questions');ask.replaceChildren();
    (target.ask||[]).forEach(x=>ask.append(e('li','',x)));
    $('care-stage-source').replaceWith(ext('查看原始医学资料 ↗',target.source_url,'care-source-link'));
    document.querySelector('.care-source-link').id='care-stage-source';
  }
  function compareRender(){
    const panel=$('care-compare');panel.replaceChildren();
    const picks=[...state.compare].map(id=>state.guide.hospitals.find(h=>h.id===id)).filter(Boolean);
    $('care-compare-count').textContent=picks.length?'已选择 '+picks.length+' 家':'请选择 2–3 家进行资料对照';
    $('care-compare-clear').hidden=picks.length===0;
    if(picks.length<2){panel.append(e('p','care-empty','选择至少两家医院，可并排比较已核实专科方向与待确认问题。不生成全国实力排名。'));return;}
    const scroll=e('div','care-compare-scroller');
    const tbl=e('table','care-compare-table');
    const caption=e('caption','sr-only','医院公开资料对照，未计算疗效或排名');tbl.append(caption);
    const rows=[
      ['医院',picks.map(h=>h.name)],
      ['所在地 / 类别',picks.map(h=>h.city+' · '+h.type)],
      ['公开专科方向',picks.map(h=>h.focus.join('、'))],
      ['已查到的资料',picks.map(h=>h.highlight)],
      ['需确认或资料局限',picks.map(h=>h.caution)],
      ['证据级别',picks.map(h=>h.verified)]
    ];
    for(const [label,values] of rows){
      const tr=e('tr');tr.append(e('th','',label));
      values.forEach(v=>tr.append(e('td','',v)));tbl.append(tr);
    }
    const tr=e('tr');tr.append(e('th','','核实链接'));for(const h of picks){const td=e('td');td.append(ext('打开原始资料 ↗',h.url));tr.append(td);}tbl.append(tr);
    scroll.append(tbl);panel.append(scroll);
  }
  function hospitalCard(h){
    const card=e('article','care-hospital-card');
    const head=e('div','care-hospital-head');
    const group=e('div');group.append(e('small','care-hospital-label',h.type+' · '+h.city));
    group.append(e('h4','',h.name));head.append(group);
    const label=e('span','care-verification',h.verified==='官方科室页面'?'医院官网证实':'仅学术记录');head.append(label);
    card.append(head);
    const focuses=e('div','care-hospital-tags');(h.focus||[]).forEach(x=>focuses.append(e('span','',x)));card.append(focuses);
    card.append(e('h5','','可核实的公开特点'));
    card.append(e('p','',h.highlight));
    card.append(e('h5','care-caution-title','局限与就诊前要核实'));
    card.append(e('p','care-caution',h.caution));
    card.append(e('small','care-evidence','依据：'+h.evidence+'；'+(h.year_note||'年份未标注')));
    const footer=e('div','care-hospital-actions');
    footer.append(ext('核对科室原始资料 ↗',h.url,'care-evidence-link'));
    const btn=e('button','care-compare-toggle',state.compare.has(h.id)?'取消对照':'加入对照');
    btn.type='button';btn.setAttribute('aria-pressed',String(state.compare.has(h.id)));
    btn.addEventListener('click',()=>{
      if(state.compare.has(h.id)){state.compare.delete(h.id);}
      else if(state.compare.size<3){state.compare.add(h.id);}
      else {$('care-hospital-message').textContent='最多并排对照 3 家，请先取消一家。';return;}
      $('care-hospital-message').textContent='';
      renderHospitals();
      compareRender();
    });
    footer.append(btn);card.append(footer);
    return card;
  }
  function renderHospitals(){
    if(!state.guide)return;
    const hospitals=state.guide.hospitals.filter(h=>
      (state.region==='all'||h.region===state.region)&&
      (state.focus==='all'||(h.focus||[]).includes(state.focus))&&
      [h.name,h.city,h.type,h.highlight,...(h.focus||[])].join(' ').toLowerCase().includes(state.term));
    const list=$('care-hospital-list');list.replaceChildren();
    hospitals.forEach(h=>list.append(hospitalCard(h)));
    if(!hospitals.length)list.append(e('p','care-empty','当前有可靠出处的医院资料中没有符合筛选条件的记录。请扩大地区或专科条件。'));
    $('care-hospital-total').textContent='当前有来源档案 '+state.guide.hospitals.length+' 家 · 筛选显示 '+hospitals.length+' 家 · 不排序';
  }
  function initHospitalFilters(){
    const h=state.guide.hospitals;
    const region=$('care-region'),focus=$('care-focus');
    const unique=items=>[...new Set(items)].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    for(const val of unique(h.map(x=>x.region)))region.append(new Option(val,val));
    for(const val of unique(h.flatMap(x=>x.focus||[])))focus.append(new Option(val,val));
    region.addEventListener('change',()=>{state.region=region.value;renderHospitals();});
    focus.addEventListener('change',()=>{state.focus=focus.value;renderHospitals();});
    $('care-hospital-search').addEventListener('input',event=>{
      state.term=event.target.value.trim().toLowerCase();renderHospitals();
    });
    $('care-compare-clear').addEventListener('click',()=>{state.compare.clear();renderHospitals();compareRender();});
  }
  function frontierCard(x,index){
    const card=e('article','care-frontier-card');
    const top=e('div','care-frontier-top');top.append(e('small','',String(index+1).padStart(2,'0')+' · 国际研究与治疗方向'));
    card.append(top,e('h4','',x.topic),e('span','care-frontier-status',x.kind),e('p','',x.scope));
    const note=e('div','care-frontier-caution');note.append(e('b','','证据与可及性限制：'),document.createTextNode(x.limits));card.append(note);
    card.append(ext('查看证据出处 ↗',x.source_url,'care-evidence-link'));
    return card;
  }
  function linkedResearch(){
    const d=state.research,el=$('care-research-list');
    if(!el)return;
    el.replaceChildren();
    if(!d){el.append(e('p','care-empty','云盘近期研究数据暂不可用，请查看上方权威来源。'));return;}
    const docs=(d.articles||[]).filter(x=>x.category==='recent').slice(0,3);
    const trials=(d.trials||[]).slice(0,3);
    for(const a of docs){const item=e('div','care-research-item');item.append(e('span','care-research-type','PubMed 新增研究'),ext(a.title,a.url),e('small','',a.journal||'研究题录'));el.append(item);}
    for(const t of trials){const item=e('div','care-research-item');item.append(e('span','care-research-type','临床试验登记'),ext(t.title,t.url),e('small','',t.id+' · '+(t.date||'日期未提供')));el.append(item);}
    const date=d.collectedAt?new Date(d.collectedAt):null;
    $('care-research-updated').textContent=date&&!Number.isNaN(date.getTime())?
      '云盘研究数据采集时间：'+date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）':'未获得可信采集时间';
  }
  function renderFrontiers(){
    const root=$('care-frontier-list');root.replaceChildren();
    state.guide.frontiers.forEach((x,i)=>root.append(frontierCard(x,i)));
  }
  function buildQuestions(){
    const stage=state.guide?.journeys.find(x=>x.id===state.stage);
    const stageQuestions=stage?.ask||[];
    const extra=[
      '医生建议去哪个专科复诊或会诊？是否需要准备全部影像和病理切片？',
      '目前可选择的标准治疗是什么，是否有证据支持其他方案？',
      '哪里可以查看正式指南、最新获批适应证及临床研究入组条件？',
      '我需要什么随访计划，以及遇到问题应该联系谁？'
    ];
    const content=[stage?.title?'【当前关注阶段】'+stage.title:'【就医问题清单】',...stageQuestions.map((q,i)=>(i+1)+'. '+q),...extra.map((q,i)=>(stageQuestions.length+i+1)+'. '+q),'提醒：此清单仅帮助与医护团队沟通，不构成诊疗建议。'].join('\n');
    $('care-copy-text').value=content;
    $('care-copy-result').textContent='已生成通用就诊问题清单，可直接复制或保存。请勿在本站输入姓名、电话或病历。';
    $('care-copy-text').hidden=false;
  }
  async function copyQuestions(){
    const text=$('care-copy-text').value;
    if(!text){buildQuestions();return;}
    try{await navigator.clipboard.writeText(text);$('care-copy-result').textContent='已复制问题清单，可粘贴到个人笔记中。';}
    catch{$('care-copy-text').select();$('care-copy-result').textContent='请选中文本并使用系统复制操作。';}
  }
  function initFAQ(){
    // Native details/summary disclosure is keyboard accessible and does not transmit visitor data.
    document.querySelectorAll('.care-faq details').forEach(d=>d.addEventListener('toggle',()=>{
      if(!d.open)return;
      document.querySelectorAll('.care-faq details').forEach(other=>{if(other!==d)other.open=false;});
    }));
  }
  async function start(){
    try{
      const [guideResponse,researchResponse]=await Promise.all([
        fetch('./data/care-guide.json',{cache:'no-store'}),
        fetch('./data/site-data.json',{cache:'no-store'})
      ]);
      if(!guideResponse.ok)throw Error('Guide unavailable');
      const guide=await guideResponse.json();
      if(guide.schema_version!==1||!Array.isArray(guide.hospitals)||!Array.isArray(guide.journeys)||!Array.isArray(guide.frontiers))throw Error('Bad guide');
      state.guide=guide;
      if(researchResponse.ok){const d=await researchResponse.json();if(d.schemaVersion===1)state.research=d;}
      initHospitalFilters();renderStage(guide.journeys[0]?.id||'screen');
      document.querySelectorAll('.care-stage-btn').forEach(b=>b.addEventListener('click',()=>renderStage(b.dataset.stage)));
      renderHospitals();compareRender();renderFrontiers();linkedResearch();
      $('care-guide-date').textContent='就医导航资料：'+(guide.updated_at_utc||'时间未标注').slice(0,10)+' · 编辑资料，尚未经临床专家审阅';
      $('care-copy-generate').addEventListener('click',buildQuestions);
      $('care-copy-button').addEventListener('click',copyQuestions);
    }catch{
      ['care-hospital-list','care-frontier-list','care-research-list'].forEach(id=>{
        $(id).replaceChildren(e('p','care-empty','当前医学导航数据尚未通过加载或校验。请直接使用原始医学机构网页。'));
      });
      $('care-guide-date').textContent='就医导航资料暂不可用';
      $('care-stage-desc').textContent='需要就医时请咨询正规医疗机构，不根据网页自行判断病情。';
    }
    initFAQ();
  }
  start();
})();
