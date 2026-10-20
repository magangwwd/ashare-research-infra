'use strict';
/* Static, local-only interactions. No patient input is transmitted or stored. */
(() => {
  const $=id=>document.getElementById(id);
  const APPROVED=new Set(['www.shca.org.cn','www.cicams.ac.cn','www.bjcancer.org','bjcancer.org','www.sysucc.org.cn','sysucc.org.cn','xuebao.301hospital.com.cn','www.zchospital.com','www.wchscu.cn','wchscu.cn','www.tjmuch.com','tmucih.com','www.nj12320.org','www.haodf.com','www.fmmu.edu.cn','www.cancer.gov','www.china-oncology.com','pubmed.ncbi.nlm.nih.gov','clinicaltrials.gov','www.fda.gov']);
  const state={guide:null,research:null,stage:'screen',type:'all',region:'all',focus:'all',hospitalQuery:'',hospitalShown:6,compare:new Set(),category:'recent',researchShown:4};
  function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e;}
  function link(label,url,cls){
    const el=node('a',cls,label);
    try{
      const u=new URL(String(url||''));
      if(u.protocol!=='https:'||!APPROVED.has(u.hostname)||u.username||u.password)throw new Error('invalid');
      el.href=u.href;el.target='_blank';el.rel='noopener noreferrer';
    }catch{el.removeAttribute('href');el.setAttribute('aria-disabled','true');el.title='该来源链接尚未核实';}
    return el;
  }
  function dateString(date){
    const d=new Date(date);
    return !date||Number.isNaN(d.valueOf())?'未标明':d.toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric'});
  }
  function errorBox(parent,message){parent.replaceChildren(node('p','empty',message));}
  function stageRender(){
    const guide=state.guide;if(!guide)return;
    const data=guide.journeys.find(x=>x.id===state.stage)||guide.journeys[0];if(!data)return;
    $('stage-title').textContent=data.title;$('stage-intro').textContent=data.brief;
    const steps=$('stage-steps');steps.replaceChildren();
    (data.steps||[]).forEach((str,i)=>{const row=node('li','',str);row.dataset.step=String(i+1);steps.append(row);});
    const ask=$('stage-ask');ask.replaceChildren();(data.ask||[]).forEach(str=>ask.append(node('li','',str)));
    const source=link('查看医学机构原始说明 ↗',data.source_url,'source-link');source.id='stage-source';
    $('stage-source').replaceWith(source);
    document.querySelectorAll('[data-stage]').forEach(button=>{
      const active=button.dataset.stage===data.id;
      button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));
    });
    $('question-fallback').hidden=true;$('copy-message').textContent='可复制到手机备忘录，就诊时使用。';
  }
  function questionsText(){
    const data=state.guide?.journeys.find(x=>x.id===state.stage);
    if(!data)return '';
    return ['【我想咨询】'+data.title,'【准备问医生】',...(data.ask||[]).map((q,i)=>(i+1)+'、'+q),
      '【带上】既往检查报告、影像和用药清单（如果有）',
      '【提醒】这只是问诊准备，不是个人诊断或治疗建议。'].join('\n');
  }
  async function copyQuestions(){
    const text=questionsText();if(!text){$('copy-message').textContent='就医问题正在加载，请稍后重试。';return;}
    try{
      if(!navigator.clipboard?.writeText)throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(text);
      $('copy-message').textContent='已复制。可以粘贴到手机备忘录里。';
    }catch{
      const field=$('question-fallback');field.value=text;field.hidden=false;field.focus();field.select();
      $('copy-message').textContent='浏览器无法自动复制，已为你显示清单，可以手动复制。';
    }
  }
  function hospitalMatch(h){
    const query=state.hospitalQuery;
    return (state.type==='all'||h.type===state.type) &&
      (state.region==='all'||h.region===state.region) &&
      (state.focus==='all'||h.focus?.includes(state.focus)) &&
      (!query||[h.name,h.city,h.type,h.highlight,...(h.focus||[]),...(h.match||[])].join(' ').toLowerCase().includes(query));
  }
  function official(h){return String(h.verified||'').includes('医院官网');}
  function evidenceBadge(h){return node('span','evidence-pill '+(official(h)?'good':'caution'),official(h)?'专科官网有资料':'现行服务待核实');}
  function details(title,cls){const d=node('details',cls),s=node('summary','',title);d.append(s);return d;}
  function hospitalCard(h){
    const box=node('article','hospital-card');
    const top=node('div','hospital-head');
    const title=node('div');title.append(node('small','hospital-place',h.city+' · '+h.type),node('h3','',h.name));
    top.append(title,evidenceBadge(h));box.append(top);
    const chips=node('div','hospital-specialties');
    (h.focus||[]).slice(0,4).forEach(x=>chips.append(node('span','',x)));box.append(chips);
    box.append(node('p','hospital-summary',h.highlight));
    const group=node('div','patient-ask');
    group.append(node('strong','','可以先向这个专科咨询：'));
    const list=node('ul');(h.match||[]).slice(0,3).forEach(x=>list.append(node('li','',x)));group.append(list);box.append(group);
    const book=node('div','hospital-booking');
    book.append(node('strong','',official(h)?'就诊渠道怎么查？':'先核实科室与预约情况'));
    book.append(node('p','',h.appointment?.summary||'以医院最新官方通知为准。'));
    book.append(link(official(h)?'查看医院就医信息 ↗':'查看现有线索（非保证可预约） ↗',h.appointment?.url,'source-link'));
    box.append(book);
    const doctorList=details('医生团队与公开专业方向','hospital-detail');
    if(h.doctors?.length){
      h.doctors.forEach(d=>{
        const entry=node('div','doctor-row');entry.append(link(d.name,d.url),node('p','',d.focus),
          node('small','','来源核查：'+(d.checked_at||'未标注')+' · 排班和是否在岗需再次确认'));
        doctorList.append(entry);
      });
    }else doctorList.append(node('p','micro','当前缺少可核实的医生名单，请查询医院官方门诊。'));
    box.append(doctorList);
    const sourceList=details('数据来源和更新日期','hospital-detail');
    sourceList.append(node('p','micro','核查日期：'+(h.checked_at||'未标注')+'。核查时间不等于原资料发布日期。'));
    (h.sources||[]).forEach(src=>{
      const row=node('p','source-row');row.append(link(src.title,src.url),node('small','','原资料日期：'+(src.source_date||'未注明')+' · 复核：'+(src.checked_at||'未标注')));
      sourceList.append(row);
    });
    sourceList.append(node('p','limitation','需要向医院确认：'+h.caution));box.append(sourceList);
    const footer=node('div','hospital-footer');
    const button=node('button','compare-button'+(state.compare.has(h.id)?' chosen':''),state.compare.has(h.id)?'已加入对比 ✓':'加入对比');
    button.type='button';button.setAttribute('aria-pressed',String(state.compare.has(h.id)));
    button.addEventListener('click',()=>{
      if(state.compare.has(h.id))state.compare.delete(h.id);
      else if(state.compare.size>=2){$('hospital-count').textContent='最多只能同时对比两家，请先取消一家。';return;}
      else state.compare.add(h.id);
      hospitalRender();comparisonRender();
    });
    footer.append(node('small','',official(h)?'专科资料：医院官网':'专科与接诊：待进一步核实'),button);box.append(footer);
    return box;
  }
  function hospitalRender(){
    if(!state.guide)return;
    const visible=state.guide.hospitals.filter(hospitalMatch);
    $('hospital-count').textContent='匹配 '+visible.length+' 家（资料库共 '+state.guide.hospitals.length+' 家）';
    const dest=$('hospital-list');dest.replaceChildren();
    visible.slice(0,state.hospitalShown).forEach(h=>dest.append(hospitalCard(h)));
    if(!visible.length)dest.append(node('p','empty','当前可核实资料中没有符合条件的医院，请尝试其他地区或专科。'));
    const more=$('show-more-hospitals');more.hidden=state.hospitalShown>=visible.length;
    if(!more.hidden)more.textContent='再看 '+Math.min(6,visible.length-state.hospitalShown)+' 家医院 ↓';
    document.querySelectorAll('[data-type]').forEach(button=>{
      const active=state.type===button.dataset.type;button.classList.toggle('on',active);
      button.setAttribute('aria-pressed',String(active));
    });
  }
  function comparisonRender(){
    const selected=[...state.compare].map(id=>state.guide?.hospitals.find(h=>h.id===id)).filter(Boolean);
    $('compare-count').textContent=selected.length+'/2 家';
    $('clear-compare').hidden=selected.length===0;
    const dest=$('comparison');dest.replaceChildren();
    if(selected.length!==2){dest.append(node('p','empty soft','请选择 2 家医院开始比较。'));return;}
    for(const h of selected){
      const card=node('article','comparison-card');
      card.append(node('h3','',h.name),evidenceBadge(h),node('h4','','公开专科方向'),
        node('p','',(h.focus||[]).join(' · ')),node('h4','','适合优先咨询的问题'),
        node('p','',(h.match||[]).join('；')),
        node('h4','','就诊前仍要核实'),node('p','',h.caution),
        link('打开原始医院资料 ↗',h.url,'source-link'));
      dest.append(card);
    }
  }
  function initHospitalFilters(){
    const guide=state.guide;
    const regions=[...new Set(guide.hospitals.map(h=>h.region))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    const focuses=[...new Set(guide.hospitals.flatMap(h=>h.focus||[]))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    regions.forEach(r=>$('region-filter').append(new Option(r,r)));
    focuses.forEach(f=>$('focus-filter').append(new Option(f,f)));
    document.querySelectorAll('[data-type]').forEach(b=>b.addEventListener('click',()=>{state.type=b.dataset.type;state.hospitalShown=6;hospitalRender();}));
    $('region-filter').addEventListener('change',ev=>{state.region=ev.target.value;state.hospitalShown=6;hospitalRender();});
    $('focus-filter').addEventListener('change',ev=>{state.focus=ev.target.value;state.hospitalShown=6;hospitalRender();});
    $('hospital-search').addEventListener('input',ev=>{state.hospitalQuery=ev.target.value.toLowerCase().trim();state.hospitalShown=6;hospitalRender();});
    $('show-more-hospitals').addEventListener('click',()=>{state.hospitalShown+=6;hospitalRender();});
    $('clear-compare').addEventListener('click',()=>{state.compare.clear();hospitalRender();comparisonRender();});
    hospitalRender();comparisonRender();
  }
  function treatmentRender(){
    const dest=$('frontier-list');dest.replaceChildren();
    (state.guide.frontiers||[]).forEach((f,i)=>{
      const d=details(f.topic,'knowledge-item');
      const panel=node('div','knowledge-content');
      panel.append(node('span','tag-info',f.kind),node('h4','','简单理解'),
        node('p','',f.scope),node('h4','','要注意'),node('p','',f.limits),
        link('查看权威医学来源 ↗',f.source_url,'source-link'));
      d.append(panel);dest.append(d);
    });
    if(!state.guide.frontiers?.length)errorBox(dest,'暂时没有经过结构核对的前沿治疗资料。');
  }
  function glossaryRender(text=''){
    const dest=$('term-list');dest.replaceChildren();
    const query=text.trim().toLowerCase();
    const matches=(state.guide?.glossary||[]).filter(x=>(x.term+' '+x.meaning).toLowerCase().includes(query));
    matches.forEach(g=>{
      const d=details(g.term,'glossary-item');
      d.append(node('p','',g.meaning));dest.append(d);
    });
    if(!matches.length)dest.append(node('p','empty','没有找到这个词，可尝试其他关键词或咨询医生。'));
  }
  function researchItems(){
    const d=state.research;if(!d)return[];
    if(state.category==='recent')return (d.articles||[]).filter(a=>a.category==='recent').map(a=>({raw:a,kind:'article'}));
    if(state.category==='recovery')return (d.articles||[]).filter(a=>a.category==='recovery').map(a=>({raw:a,kind:'article'}));
    if(state.category==='trials')return (d.trials||[]).map(a=>({raw:a,kind:'trial'}));
    if(state.category==='fda')return (d.fda||[]).map(a=>({raw:a,kind:'fda'}));
    return (d.guidelines||[]).map(a=>({raw:a,kind:'guide'}));
  }
  function researchDescription({raw,kind}){
    const cn=window.BCIChinese;
    if(kind==='article')return cn.describeArticle(raw);
    if(kind==='trial')return cn.describeTrial(raw);
    if(kind==='fda')return cn.describeFDA(raw);
    return {title:raw.title||'乳腺癌诊疗指南出版记录',summary:'这是已收录的正式出版线索，请向接诊医生咨询其实际适用范围；网页不复制版权受限的指南全文。',original:raw.title||''};
  }
  function researchRender(){
    const all=researchItems(),dest=$('research-list');dest.replaceChildren();
    $('research-count').textContent=all.length+' 条收录';
    all.slice(0,state.researchShown).forEach((entry,i)=>{
      const raw=entry.raw,desc=researchDescription(entry);
      const card=node('article','research-card');
      card.append(node('small','research-label',entry.kind==='trial'?'临床试验登记':entry.kind==='fda'?'国外药物公告':entry.kind==='guide'?'诊疗指南':'医学研究题录'));
      card.append(node('h3','',desc.title),node('p','',desc.summary));
      const meta=node('div','research-card-footer');
      meta.append(node('small','',raw.id||raw.doi||raw.journal||'公开医学来源'),link('查看原始来源 ↗',raw.url,'source-link'));
      card.append(meta);
      if(desc.original&&desc.original!==desc.title){
        const original=details('英文原标题（核对用）','source-original');
        original.append(node('p','',desc.original));card.append(original);
      }
      dest.append(card);
    });
    if(!all.length)dest.append(node('p','empty','本次云盘快照中暂无这一类信息。'));
    const more=$('show-more-research');more.hidden=all.length<=state.researchShown;
    document.querySelectorAll('[data-research]').forEach(b=>{const yes=b.dataset.research===state.category;b.classList.toggle('on',yes);b.setAttribute('aria-pressed',String(yes));});
  }
  function researchLoad(d){
    state.research=d;
    const captured=d.collectedAt,age=Math.floor((Date.now()-new Date(captured).getTime())/86400000);
    $('research-update').textContent='医学资料实际采集于 '+dateString(captured);
    $('research-scope').textContent=Number.isFinite(age)&&age>7?'这批资料距今已超过一周，请核对官方最新内容。':'显示云盘里有真实记录的日期；不是实时医疗新闻。';
    researchRender();
  }
  function prepareUI(){
    document.querySelectorAll('[data-stage]').forEach(b=>b.addEventListener('click',()=>{state.stage=b.dataset.stage;stageRender();}));
    $('copy-questions').addEventListener('click',copyQuestions);
    $('term-search').addEventListener('input',ev=>{if(state.guide)glossaryRender(ev.target.value);});
    document.querySelectorAll('[data-research]').forEach(b=>b.addEventListener('click',()=>{state.category=b.dataset.research;state.researchShown=4;researchRender();}));
    $('show-more-research').addEventListener('click',()=>{state.researchShown+=4;researchRender();});
  }
  async function load(){
    prepareUI();
    const results=await Promise.allSettled([
      fetch('./data/care-guide.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('guide unavailable');return r.json();}),
      fetch('./data/site-data.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('research unavailable');return r.json();})
    ]);
    if(results[0].status==='fulfilled'){
      const d=results[0].value;
      if(d?.schema_version===2&&Array.isArray(d.hospitals)&&Array.isArray(d.journeys)&&Array.isArray(d.glossary)){
        state.guide=d;stageRender();initHospitalFilters();treatmentRender();glossaryRender();
        $('guide-updated').textContent='医院公开资料上次核查：'+dateString(d.checked_at||d.updated_at_utc)+' · 就诊前以医院最新通知为准';
      }else guideError();
    }else guideError();
    if(results[1].status==='fulfilled'&&results[1].value?.schemaVersion===1){
      researchLoad(results[1].value);
    }else{
      $('research-update').textContent='当前研究快照无法核实';
      $('research-scope').textContent='没有可信数据时不显示假更新。请到原始医学机构网站核对。';
      errorBox($('research-list'),'暂时不能加载云盘研究内容，请稍后重试。');
      $('research-count').textContent='未加载';
    }
  }
  function guideError(){
    $('stage-title').textContent='就医流程资料暂时不可用';
    $('stage-intro').textContent='请到正规医疗机构咨询。';
    errorBox($('hospital-list'),'医院资料未能加载。请前往医院官方渠道核对。');
    errorBox($('frontier-list'),'暂无可核实的前沿治疗科普内容。');
    errorBox($('term-list'),'医学词典暂不可用。');
    $('hospital-count').textContent='目前不能确认医院信息';
    $('guide-updated').textContent='医院资料核查时间暂不可用';
  }
  load();
})();
