'use strict';
(() => {
const $=id=>document.getElementById(id);
const SAFE_HOSTS=new Set([
 'zhaopin.sgcc.com.cn','gwjsdl.zhaopin.com','job2.ccb.cn','job.ccb.com','e1.ccb.com',
 'www.boc.cn','job.icbc.com.cn','www.chinapost.com.cn','chinapost2027.zhaopin.com',
 'zhaopin.chng.com.cn','wap.sasac.gov.cn','jshrss.jiangsu.gov.cn','rsj.sh.gov.cn',
 'hrss.sz.gov.cn','jyw.mastc.edu.cn','picc.zhiye.com','www.psbc.com',
 'psbc2027.zhaopin.com','campus.chinahr.com','job.bankcomm.com','zhcpa.zhiye.com','zp.czbank.com.cn','sdg.zhaopin.com','zhaopin.cscec3b.com.cn','hr.jsbchina.cn','career.hebut.edu.cn','career.nankai.edu.cn','sgdt.iguopin.com','job.tiangong.edu.cn','docs.qq.com'
]);
const state={feed:null,search:'',region:'all',type:'all',status:'all',overseas:false,rolesOnly:false,qs:'',
  monitor:null,category:'all',ledger:null,sort:'recommended',selectedId:null,lastCardFocus:null,qq:null};
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
function deadlineTimeElapsed(job){
  const m=String(job.application_deadline_time||'').match(/^(\d{1,2}):(\d{2})/);
  if(!m)return false;  // No confirmed cutoff time: do not invent one.
  const target=Number(m[1])*60+Number(m[2]);
  if(target>=1440)return false; // A published "24:00" is end of day.
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{
    timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'
  }).formatToParts(new Date()).map(p=>[p.type,p.value]));
  return Number(parts.hour)*60+Number(parts.minute)>=target;
}
function briefStatus(job){
  if(!job.deadline)return {key:'unknown',tag:'报名日期待核实',days:null};
  const days=daysBetween(job.deadline);
  if(days<0 || (days===0&&deadlineTimeElapsed(job)))return {key:'closed',tag:'本批已截止',days};
  if(days<=7)return {key:'urgent',tag:days===0?'今天截止 · 请立即核实':'距离截止 '+days+' 天',days};
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
function resumeDirectionScore(j){
  // Broad non-identifying priorities only. The private original resume is NEVER in public JS.
  const roles=[...(j.roles||[]),...(j.majors_exact||[]),j.title||''].join(' ');
  const cities=[...(j.regions||[]),j.work_city||''].join(' ');
  let v=0;
  if(/财务|会计|审计|税务|预算|内控|经营分析|资金管理/.test(roles))v+=8;
  else if(/银行|金融|风险|财会/.test(roles))v+=4;
  if(/上海|江苏|浙江|深圳|马鞍山|安徽/.test(cities))v+=5;
  if(j.overseas==='explicit')v+=3;
  if(j.role_confirmed)v+=3;
  if(j.type==='国家电网'||j.type==='银行'||j.type==='事业单位')v+=2;
  return v;
}
function sortJobs(a,b){
  const sa=briefStatus(a),sb=briefStatus(b);
  const aClosed=sa.key==='closed'?1:0,bClosed=sb.key==='closed'?1:0;
  if(aClosed!==bClosed)return aClosed-bClosed;
  if(state.sort==='deadline'){
    const av=sa.days===null?Infinity:sa.days,bv=sb.days===null?Infinity:sb.days;
    if(av!==bv)return av-bv;
  }
  if(state.sort==='newest'){
    const av=Date.parse(a.published||'1970-01-01'),bv=Date.parse(b.published||'1970-01-01');
    if(av!==bv)return bv-av;
  }
  if(state.sort==='recommended'){
    const am=resumeDirectionScore(a),bm=resumeDirectionScore(b);
    if(am!==bm)return bm-am;
  }
  return compareSort(a,b);
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
  const status=briefStatus(job),group=categoryOf(job),selected=state.selectedId===job.id;
  const card=n('article','job-card'+(status.key==='urgent'?' near-deadline':'')+(selected?' selected':''));
  card.dataset.jobId=job.id;
  card.tabIndex=0;
  card.setAttribute('aria-label','查看 '+job.title+' 的岗位详情');
  card.setAttribute('aria-current',selected?'true':'false');
  const top=n('div','job-card-top');
  const badges=n('div','job-tags');
  badges.append(n('span','tag '+(group==='core'?'actual':group==='related'?'related':'pending'),categoryLabel(group)));
  if(job.type==='国家电网')badges.append(n('span','tag focus','国家电网'));
  if(job.priority_tier===1)badges.append(n('span','tag priority','重点关注'));
  badges.append(n('span','tag '+(status.key==='closed'?'closed':status.key==='urgent'?'warning':'muted'),status.tag));
  top.append(badges,n('span','type-name',job.type));
  card.append(top);
  card.append(n('h3','job-title',job.title),n('p','company',job.company));
  const location=String(job.work_city||(job.regions||[]).join('、')+'（城市待核实）');
  const degree=String(job.min_degree||'学历以公告为准');
  const body=n('p','job-description',String(job.summary||'').slice(0,92)+(String(job.summary||'').length>92?'…':''));
  card.append(body);
  const facts=n('div','job-info');
  facts.append(valueTile('工作城市',location.length>30?location.slice(0,29)+'…':location),
    valueTile('学历要求',degree.length>21?degree.slice(0,20)+'…':degree),
    valueTile('专业方向',(job.roles||[]).slice(0,3).join(' / ')),
    valueTile('截止时间',job.deadline?(job.deadline+(status.days>=0?' · 剩'+status.days+'天':' · 已截止')):'未核实',
      status.key==='urgent'?'highlight':status.key==='closed'?'past':''));
  card.append(facts);
  card.append(n('p','evidence-note',(job.role_confirmed?'✓ 已核实职位名称':'◇ 招聘专业或入口待核实')+' · 资料核查 '+(job.checked_at||'未知')));
  if(job.checked_at&&daysBetween(job.checked_at)<-10)card.append(n('p','stale-note','这条职位条件超过10天未人工复核；请核实最新公告。'));
  const see=n('button','job-card-action','查看岗位详情 →');see.type='button';
  see.setAttribute('aria-label','查看 '+job.title+' 的完整要求');
  see.addEventListener('click',()=>selectJob(job.id,{openMobile:true}));
  card.append(see);
  card.addEventListener('click',ev=>{
    if(ev.target.closest('button,a,details,summary'))return;
    selectJob(job.id,{openMobile:true});
  });
  card.addEventListener('keydown',ev=>{
    if(ev.target!==card)return;
    if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();selectJob(job.id,{openMobile:true});}
  });
  return card;
}
function showDetailOnMobile(){
  return typeof window.matchMedia==='function'&&window.matchMedia('(max-width: 860px)').matches;
}
function closeMobileDetail(restoreFocus=true){
  $('job-detail-pane').classList.remove('mobile-open');
  $('detail-backdrop').hidden=true;
  if(restoreFocus){
    const card=state.lastCardFocus||document.querySelector('.job-card.selected');
    if(card&&typeof card.focus==='function')card.focus();
  }
}
function selectJob(jobId,{openMobile=false}={}){
  if(!state.feed)return;
  const job=state.feed.listings.find(x=>x.id===jobId);if(!job)return;
  state.selectedId=jobId;
  state.lastCardFocus=document.querySelector('.job-card[data-job-id="'+jobId+'"]');
  const pane=$('job-detail-pane'),dest=$('job-detail');
  pane.scrollTop=0;dest.replaceChildren();
  const status=briefStatus(job),group=categoryOf(job);
  const head=n('div','detail-company-top');
  const monogram=n('div','company-monogram',String(job.company||'财').trim()[0]||'财');
  const info=n('div','detail-company-info');
  info.append(n('small','',job.type+' · '+(job.graduate_batch||'校招')),
    n('h3','',job.title),n('p','detail-company-name',job.company));
  head.append(monogram,info);
  dest.append(head);
  const badges=n('div','detail-tags');
  badges.append(n('span','tag '+(group==='core'?'actual':group==='related'?'related':'pending'),categoryLabel(group)));
  badges.append(n('span','tag '+(status.key==='urgent'?'warning':status.key==='closed'?'closed':'muted'),status.tag));
  if(job.priority_tier===1)badges.append(n('span','tag priority','重点关注'));
  dest.append(badges);
  const keyFacts=n('div','detail-keyfacts');
  keyFacts.append(
    valueTile('工作地点',job.work_city||(job.regions||[]).join('、')+'；具体地市待核实'),
    valueTile('学历要求',job.min_degree||'以具体岗位为准'),
    valueTile('专业类别',(job.majors_exact||[]).slice(0,3).join('、')||'具体以职位专业表为准'),
    valueTile('报名截止',job.deadline?(job.deadline+(status.key==='closed'?' · 本批已截止':status.key==='urgent'?' · 请尽快确认':' · 见公告')):'截止日期未核实',status.key==='urgent'?'highlight':'')
  );
  dest.append(keyFacts);
  const truth=n('p','detail-notice',job.role_confirmed
     ?'岗位名称有资料支持，但网申是否仍可投递、资格与人数须以该单位最新页面确认。'
     :'这是一条招聘公告、专业类别或单位跟踪记录，尚未核实它对应目前可投递的财务岗位。');
  dest.append(truth);
  const desc=n('section','detail-section');
  desc.append(n('h4','','职位简介'),n('p','',job.summary||'请查看原始招聘公告。'));dest.append(desc);
  const important=n('section','detail-section');
  important.append(n('h4','','投递前请特别注意'),n('p','',job.critical||job.notes||'先核实该单位实际岗位开放状态。'));dest.append(important);
  const actions=n('div','detail-actions');
  actions.append(anchor('查看公告原文 ↗',job.source_url,'action-link'),
    anchor(status.key==='closed'?'查看官方历史招聘入口 ↗':'打开官方报名入口 ↗',job.apply_url,'action-link primary-link'));
  dest.append(actions);
  const groupDetails=detailGroup(job);
  groupDetails.open=true;
  dest.append(groupDetails);
  const stamp=n('p','audit-explainer','职位资料核查：'+(job.checked_at||'未标注')+
    ' · 招聘平台可能晚于本站更新 · 最新存档：'+(state.feed.collected_at||'未核实'));
  dest.append(stamp);
  document.querySelectorAll('.job-card').forEach(card=>{
    const current=card.dataset.jobId===jobId;
    card.classList.toggle('selected',current);
    card.setAttribute('aria-current',current?'true':'false');
  });
  if(openMobile&&showDetailOnMobile()){
    pane.classList.add('mobile-open');
    $('detail-backdrop').hidden=false;
    $('close-detail').focus();
  }
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
 const arr=state.feed.listings.filter(jobSearchMatch).sort(sortJobs);
 $('job-total').textContent=arr.length+' 条匹配';
 $('job-filter-count').textContent='符合条件 '+arr.length+' 条 · 已全部展示';
 const target=$('jobs-list');target.replaceChildren();
 arr.forEach(job=>target.append(jobCard(job)));
 if(!arr.some(job=>job.id===state.selectedId))state.selectedId=arr[0]?.id||null;
 if(state.selectedId)selectJob(state.selectedId,{openMobile:false});
 else {$('job-detail').replaceChildren(n('p','loading','没有符合当前条件的招聘记录，请调整筛选。'));closeMobileDetail(false);}
 if(!arr.length)target.append(n('p','loading','没有匹配的官方记录，可放宽条件。网站不虚构工作岗位或QS资格。'));
 const rank=Number(state.qs);
 $('qs-message').textContent=state.qs&&Number.isInteger(rank)&&rank>0?
   '你输入了 QS '+rank+'。当前已核实招聘信息没有通用QS排名门槛；本站不能据此自动判定有资格或筛除岗位，请逐条打开原文核对。':
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
function renderQQ(){
  const status=$('qq-feed-status'),container=$('qq-shortlist');
  if(!status||!container)return;
  const qq=state.qq;
  if(!qq){
    status.textContent='腾讯文档是首要外部招聘来源；当前未取得可验证的表格行数据，无法自动导入岗位。';
    container.hidden=true;return;
  }
  const when=(qq.checked_at_utc||'未知').slice(0,19).replace('T',' ');
  if(qq.status!=='authorized_export_imported'){
    status.textContent='当前状态：表格仅可查看，未取得授权导出的完整单元格数据；已保留链接并每日核查。最近检查 '+when+' UTC。';
    container.hidden=true;return;
  }
  const items=Array.isArray(qq.leads)?qq.leads.filter(x=>!x.deadline||daysBetween(x.deadline)>=0):[];
  const sourceType=qq.source_freshness==='direct_authorized_export'?'本次已授权的腾讯原始导出':'以前保存到云盘的授权导出副本';
  const sourceDate=qq.source_export_modified_at?'，源文件存档时间 '+qq.source_export_modified_at.slice(0,10):'';
  status.textContent='已读取'+sourceType+sourceDate+'；扫描 '+qq.rows_examined+' 行、筛出 '+items.length+' 条非过期招聘线索。未验证腾讯表格当前是否已更新，报名条件请核对官网。';
  $('qq-shortlist-count').textContent=items.length+' 条';
  const el=$('qq-shortlist-list');el.replaceChildren();
  items.slice(0,100).forEach(job=>{
    const row=n('article','qq-lead');
    row.append(n('span','qq-lead-tag','第三方汇总 · 待官网核实'));
    row.append(n('strong','',job.title),n('p','',job.company+' · '+job.work_city+
      (job.deadline?' · 截止（待官方确认）'+job.deadline:' · 截止时间未核实')));
    const reasons=n('p','qq-lead-reasons',(job.match_reasons||[]).join(' · '));
    row.append(reasons,anchor('查看原始腾讯表格 ↗',job.third_party_url||qq.source_url,'source-link'));
    const original=job.original_link;
    if(original&&SAFE_HOSTS.has((()=>{try{return new URL(original).hostname}catch{return ''}})())){
      row.append(anchor('核对单位链接 ↗',original,'source-link'));
    }
    el.append(row);
  });
  container.hidden=items.length===0;
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
 state.qs=$('filter-qs').value.trim();state.sort=$('filter-sort').value;
 $('hero-region').value=state.region;
 if(document.activeElement!==$('hero-search'))$('hero-search').value=$('filter-text').value;
 closeMobileDetail(false);renderJobs();
}
function initControls(){
 for(const id of ['filter-text','filter-region','filter-type','filter-status','filter-overseas','filter-role','filter-qs','filter-sort']){
   $(id).addEventListener(id==='filter-text'||id==='filter-qs'?'input':'change',changeUI);
 }
 document.querySelectorAll('[data-category]').forEach(button=>button.addEventListener('click',()=>{
   state.category=button.dataset.category;closeMobileDetail(false);renderJobs();
 }));
 $('hero-search-go').addEventListener('click',()=>{
   $('filter-text').value=$('hero-search').value;
   $('filter-region').value=$('hero-region').value;
   changeUI();
   $('jobs').scrollIntoView?.({behavior:'smooth',block:'start'});
 });
 $('hero-search').addEventListener('keydown',ev=>{if(ev.key==='Enter'){ev.preventDefault();$('hero-search-go').click();}});
 document.querySelectorAll('[data-quick-region]').forEach(button=>button.addEventListener('click',()=>{
   const region=button.dataset.quickRegion;
   $('hero-region').value=region;
   $('filter-region').value=region;
   changeUI();
   $('jobs').scrollIntoView?.({behavior:'smooth',block:'start'});
 }));
 $('close-detail').addEventListener('click',()=>closeMobileDetail());
 $('detail-backdrop').addEventListener('click',()=>closeMobileDetail());
 document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&$('job-detail-pane').classList.contains('mobile-open'))closeMobileDetail();});
 $('reset-filters').addEventListener('click',()=>{
   $('filter-text').value='';$('filter-region').value='all';$('filter-type').value='all';$('filter-status').value='all';
   $('filter-overseas').checked=false;$('filter-role').checked=false;$('filter-qs').value='';$('filter-sort').value='recommended';
   $('hero-search').value='';$('hero-region').value='all';state.category='all';changeUI();
 });
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
   try{const q=await fetchJSON('./data/qq_feed.json');if(q.schema===1&&q.source_url?.startsWith('https://docs.qq.com/sheet/'))state.qq=q;}
   catch{state.qq=null;}
   renderUrgent();renderJobs();renderSources();renderChanges();renderQQ();
 }catch{
   ['jobs-list','urgent-list','sources-list'].forEach(id=>$(id).replaceChildren(n('p','loading','云盘最新招聘数据暂不可用。不能据旧信息认定报名仍开放，请直接访问国家电网等官方招聘网站。')));
   $('sync-text').textContent='云盘数据校验失败，暂停展示未核实的招聘记录';
   $('footer-update').textContent='数据状态暂不可用';
   renderChanges();renderQQ();
 }
}
init();
})();
