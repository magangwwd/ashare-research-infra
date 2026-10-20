'use strict';
(() => {
const $=id=>document.getElementById(id);
const SAFE_HOSTS=new Set([
 'zhaopin.sgcc.com.cn','gwjsdl.zhaopin.com','job2.ccb.cn','job.ccb.com','e1.ccb.com',
 'www.boc.cn','job.icbc.com.cn','www.chinapost.com.cn','chinapost2027.zhaopin.com',
 'zhaopin.chng.com.cn','wap.sasac.gov.cn','jshrss.jiangsu.gov.cn','rsj.sh.gov.cn',
 'hrss.sz.gov.cn','jyw.mastc.edu.cn','picc.zhiye.com','www.psbc.com',
 'psbc2027.zhaopin.com','campus.chinahr.com','job.bankcomm.com','zhcpa.zhiye.com','zp.czbank.com.cn','sdg.zhaopin.com','zhaopin.cscec3b.com.cn','hr.jsbchina.cn','career.hebut.edu.cn','career.nankai.edu.cn','sgdt.iguopin.com','job.tiangong.edu.cn'
]);
const state={feed:null,search:'',region:'all',type:'all',status:'all',overseas:false,rolesOnly:false,qs:'',
  visible:8,monitor:null,category:'all',ledger:null};
const cnTime=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
function today(){const ps=cnTime.formatToParts(new Date());const x=Object.fromEntries(ps.map(p=>[p.type,p.value]));return x.year+'-'+x.month+'-'+x.day;}
function daysBetween(day){return Math.round((Date.parse(day+'T00:00:00Z')-Date.parse(today()+'T00:00:00Z'))/86400000);}
function n(tag,cls,txt){const x=document.createElement(tag);if(cls)x.className=cls;if(txt!==undefined)x.textContent=String(txt);return x;}
function anchor(txt,href,cls){
  const a=n('a',cls,txt);
  try{
    const u=new URL(String(href||''));
    if(u.protocol!=='https:'||!SAFE_HOSTS.has(u.hostname)||u.username||u.password)throw Error('unapproved URL');
    a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';
  }catch{a.removeAttribute('href');a.title='尚未核实官方链接';}
  return a;
}
function dateLabel(v){return v||'官方尚未公布';}
function briefStatus(job){
  if(!job.deadline)return {key:'unknown',tag:'报名日期待核实',days:null};
  const days=daysBetween(job.deadline);
  if(days<0)return {key:'closed',tag:'本批已截止',days};
  if(days<=7)return {key:'urgent',tag:'距离截止 '+days+' 天',days};
  return {key:'open',tag:'已公布截止日',days};
}
function openState(job){const r=briefStatus(job);return r.key==='closed'?'closed':r.key==='unknown'?'unknown':'open';}
function levelText(job){
  if(job.role_confirmed)return '已核实招聘岗位名称（其他条件继续核对）';
  if((job.evidence||'').includes('official_portal')||(job.posting_level||'').includes('入口'))return '官方招聘入口，未确认具体财会空缺';
  return job.posting_level||'招聘公告，具体财会职位待核实';
}
function overseasText(job){
  if(job.overseas==='explicit')return '公告明确包含境外应届毕业生';
  if(job.overseas==='domestic_scope')return '当前已见条件主要面向境内高校；海外资格待核实';
  return '海外毕业生需逐岗核对';
}
function categoryOf(j){
  if(j.role_confirmed && (j.relevance==='core_finance' ||
      (!j.relevance && (j.roles||[]).some(r=>['财务','会计','审计','税务'].includes(r)))))return 'core';
  if(j.relevance==='bank_related'||j.relevance==='finance_adjacent'||j.role_confirmed)return 'related';
  return 'watch';
}
function categoryLabel(kind){
  return kind==='core'?'财会审计明确岗位':kind==='related'?'银行及金融相关职位':'集团公告或待核实专业';
}
function priorityScore(j){
  const s=briefStatus(j);
  const urgent=s.key==='urgent'?0:s.key==='open'?1:s.key==='unknown'?2:3;
  const level=categoryOf(j)==='core'?0:categoryOf(j)==='related'?1:2;
  return [s.key==='closed'?1:0,level,urgent,j.priority_tier??3,s.days===null?999:s.days];
}
function compareSort(a,b){
  const x=priorityScore(a),y=priorityScore(b);
  for(let i=0;i<x.length;i++)if(x[i]!==y[i])return x[i]-y[i];
  return String(a.title).localeCompare(String(b.title),'zh-CN');
}
function valueLine(label,val){
  const div=n('div','detail-row');
  div.append(n('dt','',label),n('dd','',val===undefined||val===null||val===''?'官方暂未明确':val));
  return div;
}
function detailGroup(job){
  const details=n('details','expanded');
  details.append(n('summary','','展开查看岗位完整要求、留学生资格及报名流程'));
  const inner=n('div','details-inner');
  const matrix=n('dl','detail-matrix');
  const fields=[
    ['招聘单位',job.recruiting_unit||job.company],
    ['岗位名称及级别',job.posting_level],
    ['工作地点',job.work_city||(job.regions||[]).join('、')+'；具体地市待核实'],
    ['招录人数',job.vacancies===null?'未核实到具体岗位人数':job.vacancies+'人'],
    ['应届届别',job.graduate_batch],
    ['学历要求',job.min_degree],
    ['允许报考的专业',(job.majors_exact||[]).join('、')||'请核对原文岗位专业表'],
    ['接受境外毕业生',overseasText(job)],
    ['留学生具体条件',job.overseas_details],
    ['QS排名要求',job.qs_details||'官方未公布统一QS门槛'],
    ['招聘公告发布日期',job.published||'日期待核实'],
    ['开始报名时间',job.application_open||'未核实'],
    ['报名截止时间',job.deadline?(job.deadline+' '+(job.application_deadline_time||'具体时间以系统为准')):'尚未核实；请打开官网'],
    ['考试或面试形式',job.assessment],
    ['报名流程',job.application_process],
    ['报名材料',(job.documents||[]).join('、')||'看官方公告'],
    ['最新已确认说明',job.critical],
    ['公告资料核查日期',job.checked_at||'未注明'],
    ['来源与证据层级',job.verified_from||job.evidence||'待核实']
  ];
  fields.forEach(([name,val])=>matrix.append(valueLine(name,val)));inner.append(matrix);
  const warn=n('p','detail-reminder','提醒：招聘计划、学历要求、网申开放状态可能改变。一定要进入单位官方网站再提交个人资料。');
  inner.append(warn);
  const actions=n('div','detail-actions');
  actions.append(anchor('查看原始公告 ↗',job.source_url,'action-link'),
    anchor('进入官方报名入口 ↗',job.apply_url,'action-link primary-link'));
  inner.append(actions);details.append(inner);
  return details;
}
function jobCard(job){
  const status=briefStatus(job);
  const card=n('article','job-card'+(status.key==='urgent'?' near-deadline':''));
  const top=n('div','job-card-top');
  const badges=n('div','job-tags');
  const group=categoryOf(job);
  badges.append(n('span','tag '+(group==='core'?'actual':group==='related'?'related':'pending'),categoryLabel(group)));
  if(job.priority_tier===1)badges.append(n('span','tag priority','重点关注'));
  if(job.type==='国家电网')badges.append(n('span','tag focus','国家电网'));
  badges.append(n('span','tag '+(status.key==='closed'?'closed':status.key==='urgent'?'warning':'muted'),status.tag));
  top.append(badges,n('span','type-name',job.type));
  card.append(top);
  card.append(n('h3','job-title',job.title),n('p','company',job.company));
  const location=String(job.work_city||(job.regions||[]).join('、')+'（需核实）');
  const brief=n('p','job-description',String(job.summary||'').slice(0,155)+(String(job.summary||'').length>155?'…':''));
  card.append(brief);
  const columns=n('div','job-info');
  columns.append(valueTile('工作地点',location.length>58?location.slice(0,55)+'…':location),
    valueTile('学历要求',String(job.min_degree||'以岗位公告为准').slice(0,35)),
    valueTile('目标方向',(job.roles||[]).slice(0,3).join(' / ')),
    valueTile('报名截止',job.deadline?job.deadline+(status.days>=0?' · 剩'+status.days+'天':' · 本批结束'):'截止日期待核实',
      status.key==='urgent'?'highlight':status.key==='closed'?'past':''));
  card.append(columns);
  const note=n('p','evidence-note',(job.role_confirmed?'✓ ':'! ')+levelText(job)+' · 核对日期 '+(job.checked_at||'未标注'));
  card.append(note);
  if(job.checked_at){
    const days=daysBetween(job.checked_at);
    if(days < -10)card.append(n('p','stale-note','该职位核心条件已超过10天未人工复核；请优先查看最新官方公告。'));
  }
  const crit=n('p','critical-note',(status.key==='urgent'?'【请尽快核对报名】':'注意：')+(job.critical||job.notes));
  card.append(crit);
  const bottom=n('div','job-links');
  bottom.append(anchor('查看原公告 ↗',job.source_url,'source-link'),
    anchor('官方招聘入口 ↗',job.apply_url,'source-link'));
  card.append(bottom,detailGroup(job));
  return card;
}
function valueTile(label,value,cls=''){
  const item=n('div','info-item'+(cls?' '+cls:''));item.append(n('span','',label),n('strong','',value));return item;
}
function jobSearchMatch(j){
 const q=state.search;
 const allText=[j.title,j.company,j.type,...(j.regions||[]),...(j.roles||[]),...(j.majors_exact||[]),j.summary,j.critical,j.notes].filter(Boolean).join(' ').toLowerCase();
 if(q&&!allText.includes(q))return false;
 if(state.region!=='all'&&!(j.regions||[]).includes(state.region))return false;
 if(state.type!=='all'&&j.type!==state.type)return false;
 if(state.status!=='all'&&openState(j)!==state.status)return false;
 if(state.overseas&&j.overseas!=='explicit')return false;
 if(state.rolesOnly&&!j.role_confirmed)return false;
 if(state.category!=='all'&&categoryOf(j)!==state.category)return false;
 return true;
}
function renderJobs(){
 if(!state.feed)return;
 for(const category of ['all','core','related','watch']){
   const count=category==='all'?state.feed.listings.length:state.feed.listings.filter(j=>categoryOf(j)===category).length;
   $('category-count-'+category).textContent=String(count);
 }
 document.querySelectorAll('[data-category]').forEach(button=>{
   const yes=button.dataset.category===state.category;button.classList.toggle('selected',yes);
   button.setAttribute('aria-pressed',String(yes));
 });
 const arr=state.feed.listings.filter(jobSearchMatch).sort(compareSort);
 $('job-total').textContent=arr.length+' 条匹配';
 $('job-filter-count').textContent='符合条件 '+arr.length+' 条 · 已显示 '+Math.min(state.visible,arr.length)+' 条';
 const target=$('jobs-list');target.replaceChildren();
 arr.slice(0,state.visible).forEach(job=>target.append(jobCard(job)));
 if(!arr.length)target.append(n('p','loading','没有匹配的官方记录，可放宽条件。网站不虚构工作岗位或QS资格。'));
 const more=$('jobs-more');more.hidden=state.visible>=arr.length;
 if(!more.hidden)more.textContent='再看 '+Math.min(8,arr.length-state.visible)+' 条详细公告 ↓';
 const rank=Number(state.qs);
 $('qs-message').textContent=state.qs&&Number.isInteger(rank)&&rank>0?
   '你输入了 QS '+rank+'。当前已核实招聘信息没有通用QS排名门槛；网站不据此自动判定有资格或筛除岗位，请逐条打开原文核对。':
   'QS排名仅供自我规划。官方没有写QS门槛的公告，不会被错误筛掉或判定符合要求。';
}
function renderUrgent(){
 if(!state.feed)return;
 const important=state.feed.listings.filter(j=>{
   const s=briefStatus(j);return s.key==='urgent'&&j.type!=='招聘活动';
 }).sort(compareSort);
 // Condense multiple bank branches sharing one company-wide deadline.
 const condensed=[],seen=new Set();
 for(const job of important){
   const key=job.id.startsWith('cz2027')?'czbank':job.id.startsWith('cgws-2027')?'cgws':job.company;
   if(seen.has(key))continue;
   seen.add(key);condensed.push(job);
 }
 $('stat-items').textContent=String(state.feed.listings.length);
 $('stat-urgent').textContent=String(important.length);
 $('stat-sources').textContent=String(state.feed.sources.length);
 $('stat-update').textContent=String((state.feed.collected_at||state.feed.as_of||'待核实').slice(5,10));
 const slot=$('urgent-list');slot.replaceChildren();
 if(!important.length){slot.append(n('p','loading','当前未找到近7天内截止且日期已确认的招聘公告。具体岗位开放情况请检查下方官网。'));return;}
 condensed.slice(0,8).forEach(j=>{
   const card=n('article','urgent-card');
   const head=n('div','urgent-head');
   head.append(n('strong','','【距截止 '+daysBetween(j.deadline)+' 天】'),n('span','','截止：'+j.deadline));
   card.append(head,n('h3','',j.title),n('p','',j.critical||j.notes));
   const label=n('p','urgent-validity',categoryOf(j)==='core'?'有具体财会岗位名称；仍须确认实际报名开放':'银行相关岗位或招聘公告；是否属于具体财会职位须核对');
   card.append(label,anchor('立即核对官方公告与报名要求 ↗',j.source_url,'urgent-link'));
   slot.append(card);
 });
}
function renderSources(){
 if(!state.feed)return;
 const statusById=new Map((state.monitor?.sources||[]).map(s=>[s.id,s]));
 const box=$('sources-list');box.replaceChildren();
 state.feed.sources.sort((a,b)=>a.priority-b.priority).forEach(src=>{
   const card=n('div','source-card');
   const monitor=statusById.get(src.id);
   card.append(n('strong','',src.name),n('small','',monitor?.status||'待每日巡检确认'));
   card.append(anchor('打开招聘官网 ↗',src.url,'source-link'));
   box.append(card);
 });
 const leads=$('leads-list');leads.replaceChildren();
 const arr=state.monitor?.potential_new_announcements||[];
 $('leads-count').textContent=arr.length+' 条';
 if(!arr.length){leads.append(n('p','small-muted','本次未获取经初筛的候选公告线索，可能是招聘站点使用动态页面。请直接查看官网。'));return;}
 arr.slice(0,30).forEach(a=>{
   const row=n('div','lead-item');row.append(anchor(a.title,a.url,'source-link'),
     n('small','','待人工核验岗位专业/截止时间/资格；不能当作开放职位'));leads.append(row);
 });
}
function renderChanges(){
 const el=$('changes-list');el.replaceChildren();
 const log=state.ledger;
 if(!log){
   $('audit-status').textContent='当前云盘变化记录暂不可用；不展示推测的招聘变化。';
   $('changes-total').textContent='待核实';
   el.append(n('p','loading','无法取得已验证的每日变化记录。请查看招聘原始公告。'));
   return;
 }
 const count=log.counts||{};
 $('changes-total').textContent=log.total_events+' 条核查变化';
 $('audit-status').textContent='本次对比 '+(log.previous_collected_at?'上一份快照 '+log.previous_collected_at.slice(0,10):'初始基线')+
   '：新入库档案 '+(count.new_curated_record||0)+' 条，人工字段修改 '+(count.curated_field_change||0)+
   ' 条，来源网页变化 '+(count.official_page_changed_review_needed||0)+
   ' 条，新抓取待核链接 '+(count.unreviewed_link_discovered||0)+' 条。';
 const priority=['curated_field_change','new_curated_record','removed_from_directory','source_access_status_changed','official_page_changed_review_needed','unreviewed_link_discovered'];
 const sorted=[...(log.events||[])].sort((a,b)=>priority.indexOf(a.kind)-priority.indexOf(b.kind));
 if(!sorted.length){el.append(n('p','loading','与上一份云盘快照相比，未记录到档案字段或招聘来源变化。'));return;}
 const labels={new_curated_record:'新增入库资料',curated_field_change:'招聘字段修改',removed_from_directory:'档案移出',
   source_access_status_changed:'来源访问变化',official_page_changed_review_needed:'来源页面指纹变化',unreviewed_link_discovered:'发现待核公告'};
 sorted.slice(0,18).forEach(change=>{
   const row=n('article','change-row');
   row.append(n('span','change-kind '+(change.kind==='curated_field_change'?'critical':''),
     labels[change.kind]||'来源监测记录'));
   row.append(n('strong','',change.title||change.id||'记录变更'));
   row.append(n('p','',change.note||'须核实原始资料'));
   if(change.source)row.append(anchor('查看对应来源 ↗',change.source,'source-link'));
   el.append(row);
 });
 if(sorted.length>18)el.append(n('p','audit-explainer','还有 '+(sorted.length-18)+' 条变化保留在 Google Drive 每日历史快照中。'));
}
function changeUI(){
 state.search=$('filter-text').value.trim().toLowerCase();
 state.region=$('filter-region').value;state.type=$('filter-type').value;
 state.status=$('filter-status').value;
 state.overseas=$('filter-overseas').checked;state.rolesOnly=$('filter-role').checked;
 state.qs=$('filter-qs').value.trim();state.visible=8;renderJobs();
}
function initControls(){
 for(const id of ['filter-text','filter-region','filter-type','filter-status','filter-overseas','filter-role','filter-qs']){
   $(id).addEventListener(id==='filter-text'||id==='filter-qs'?'input':'change',changeUI);
 }
 document.querySelectorAll('[data-category]').forEach(button=>button.addEventListener('click',()=>{
   state.category=button.dataset.category;state.visible=8;renderJobs();
 }));
 $('reset-filters').addEventListener('click',()=>{
   $('filter-text').value='';$('filter-region').value='all';$('filter-type').value='all';$('filter-status').value='all';
   $('filter-overseas').checked=false;$('filter-role').checked=false;$('filter-qs').value='';state.category='all';changeUI();
 });
 $('jobs-more').addEventListener('click',()=>{state.visible+=8;renderJobs();});
}
async function fetchJSON(path){
 const response=await fetch(path,{cache:'no-store'});
 if(!response.ok)throw Error('Cannot read verified public data');
 return response.json();
}
async function init(){
 initControls();
 try{
   const feed=await fetchJSON('./data/latest.json');
   if(feed.schema!==1||!Array.isArray(feed.listings)||feed.listings.length<8||!Array.isArray(feed.sources))throw Error('Invalid verified snapshot');
   state.feed=feed;
   $('sync-text').textContent='Google Drive 存档 '+(feed.collected_at||'日期待核实').slice(0,19).replace('T',' ')+' UTC · 公告原始发布日期分别标注';
   $('footer-update').textContent='云盘数据存档：'+(feed.collected_at||'未知')+'；招聘公告以原始来源时间为准';
   try{const check=await fetchJSON('./data/monitor.json');if(check.schema===1)state.monitor=check;}
   catch{$('leads-count').textContent='（本次检测未更新）';}
   try{const log=await fetchJSON('./data/changes.json');if(Array.isArray(log.events))state.ledger=log;}
   catch{state.ledger=null;}
   renderUrgent();renderJobs();renderSources();renderChanges();
 }catch{
   ['jobs-list','urgent-list','sources-list'].forEach(id=>$(id).replaceChildren(n('p','loading','云盘最新招聘数据暂不可用。不能据旧信息认定报名仍开放，请直接访问国家电网等官方招聘网站。')));
   $('sync-text').textContent='云盘数据校验失败，暂停展示未核实的招聘记录';
   $('footer-update').textContent='数据状态暂不可用';
   renderChanges();
 }
}
init();
})();
