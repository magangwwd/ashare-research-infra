'use strict';
(() => {
const $=id=>document.getElementById(id);
const SAFE_HOSTS=new Set([
 'zhaopin.sgcc.com.cn','gwjsdl.zhaopin.com','job2.ccb.cn','job.ccb.com','e1.ccb.com',
 'www.boc.cn','job.icbc.com.cn','www.chinapost.com.cn','chinapost2027.zhaopin.com',
 'zhaopin.chng.com.cn','wap.sasac.gov.cn','jshrss.jiangsu.gov.cn','rsj.sh.gov.cn',
 'hrss.sz.gov.cn','jyw.mastc.edu.cn','picc.zhiye.com','www.psbc.com',
 'psbc2027.zhaopin.com','campus.chinahr.com','job.bankcomm.com','zhcpa.zhiye.com','zp.czbank.com.cn','sdg.zhaopin.com','zhaopin.cscec3b.com.cn','hr.jsbchina.cn','career.hebut.edu.cn'
]);
const state={feed:null,search:'',region:'all',type:'all',status:'all',overseas:false,rolesOnly:false,qs:'',
  visible:8,monitor:null};
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
function priorityScore(j){
  const s=briefStatus(j);
  const urgent=s.key==='urgent'?0:s.key==='open'?1:s.key==='unknown'?2:3;
  return [urgent,j.priority_tier??3,j.role_confirmed?0:1,s.days===null?999:s.days];
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
  if(job.priority_tier===1)badges.append(n('span','tag priority','重点关注'));
  if(job.type==='国家电网')badges.append(n('span','tag focus','国家电网'));
  badges.append(n('span','tag '+(status.key==='closed'?'closed':status.key==='urgent'?'warning':'muted'),status.tag));
  top.append(badges,n('span','type-name',job.type));
  card.append(top);
  card.append(n('h3','job-title',job.title),n('p','company',job.company));
  const brief=n('p','job-description',job.summary);card.append(brief);
  const columns=n('div','job-info');
  columns.append(valueTile('关注地区',(job.regions||[]).join('、')),valueTile('专业方向',(job.roles||[]).slice(0,5).join(' / ')),
    valueTile('截止时间',job.deadline?job.deadline+(status.days>=0?' · 剩余'+status.days+'天':' · 已过期'):'未确认具体日期',
      status.key==='urgent'?'highlight':status.key==='closed'?'past':''));
  card.append(columns);
  const note=n('p','evidence-note',(job.role_confirmed?'✓ ':'! ')+levelText(job));
  card.append(note);
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
 return true;
}
function renderJobs(){
 if(!state.feed)return;
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
 $('stat-items').textContent=String(state.feed.listings.length);
 $('stat-urgent').textContent=String(important.length);
 $('stat-sources').textContent=String(state.feed.sources.length);
 $('stat-update').textContent=String((state.feed.collected_at||state.feed.as_of||'待核实').slice(5,10));
 const slot=$('urgent-list');slot.replaceChildren();
 if(!important.length){slot.append(n('p','loading','当前未找到近7天内截止且日期已确认的招聘公告。具体岗位开放情况请检查下方官网。'));return;}
 important.forEach(j=>{
   const card=n('article','urgent-card');
   const head=n('div','urgent-head');
   head.append(n('strong','','【距截止 '+daysBetween(j.deadline)+' 天】'),n('span','','截止：'+j.deadline));
   card.append(head,n('h3','',j.title),n('p','',j.critical||j.notes));
   const label=n('p','urgent-validity',j.role_confirmed?'已核实财会职位名称；其他条件以官网为准':'该公告不等于已核实财会空缺，先查具体岗位表');
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
 $('reset-filters').addEventListener('click',()=>{
   $('filter-text').value='';$('filter-region').value='all';$('filter-type').value='all';$('filter-status').value='all';
   $('filter-overseas').checked=false;$('filter-role').checked=false;$('filter-qs').value='';changeUI();
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
   renderUrgent();renderJobs();renderSources();
 }catch{
   ['jobs-list','urgent-list','sources-list'].forEach(id=>$(id).replaceChildren(n('p','loading','云盘最新招聘数据暂不可用。不能据旧信息认定报名仍开放，请直接访问国家电网等官方招聘网站。')));
   $('sync-text').textContent='云盘数据校验失败，暂停展示未核实的招聘记录';
   $('footer-update').textContent='数据状态暂不可用';
 }
}
init();
})();
