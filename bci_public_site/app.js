'use strict';
const state={data:null,view:'overview',category:'all'};
const $=id=>document.getElementById(id);
const label={overview:'专业研究总览',articles:'医学文献',trials:'临床试验',fda:'药物监管',guides:'指南与来源'};
const typeLabels={recent:'近期研究',case:'公开病例',recovery:'康复与随访'};
const typeClasses={recent:'',case:'blue-chip',recovery:'violet-chip'};
const trialStatus={RECRUITING:'招募中',NOT_YET_RECRUITING:'尚未招募',ACTIVE_NOT_RECRUITING:'进行中 · 不招募',COMPLETED:'已完成',TERMINATED:'已终止',SUSPENDED:'暂停',WITHDRAWN:'撤回',ENROLLING_BY_INVITATION:'邀请入组',UNKNOWN:'状态未知'};
function element(tag,className='',content){const node=document.createElement(tag);if(className)node.className=className;if(content!==undefined)node.textContent=String(content);return node;}
function append(parent,...children){for(const child of children)parent.append(child);return parent;}
function link(text,href){const a=element('a','',text);try{const u=new URL(href);if(u.protocol!=='https:')throw new Error('URL');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}catch{a.removeAttribute('href');}return a;}
function card(kind,title,href,tags,detail,number){
 const item=element('article','result-card');const num=element('div','card-index',String(number).padStart(2,'0'));
 const body=element('div','card-body');const chips=element('div','card-tags');
 for(const t of tags){chips.append(element('span','card-chip '+(t.cls||''),t.name));}
 const h=element('h3','card-title');h.append(link(title,href));
 const meta=element('div','card-meta');for(const d of detail){if(d)meta.append(element('span','',d));}
 append(body,chips,h,meta);append(item,num,body,link('↗',href));item.lastChild.className='card-link';return item;
}
function setView(name,category){if(!label[name])return;state.view=name;if(category)state.category=category;
 document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===name+'-view'));
 document.querySelectorAll('.nav-link').forEach(v=>v.classList.toggle('active',v.dataset.view===name));
 $('breadcrumb-current').textContent=label[name];$('page-title').textContent='';
 const mainTitle={overview:['乳腺癌专业研究','资料库'],articles:['最新医学文献','检索'],trials:['全球临床试验','追踪'],fda:['药物监管动态','索引'],guides:['指南与数据','溯源']};
 $('page-title').append(document.createTextNode(mainTitle[name][0]),element('span','',mainTitle[name][1]));
 const subtitle={overview:'供临床医师与研究人员检索公开论文、临床试验、指南与监管资料；尚未经医学专业审核。',articles:'公开医学文献元数据查询 · 论文摘要请前往原始来源。',trials:'ClinicalTrials.gov 已注册试验，登记状态不代表治疗获益。',fda:'美国 FDA 乳腺癌相关监管公告索引，需注意适用国家差异。',guides:'学术指南目录与官方来源状态，所有记录可核查来源。'};
 $('page-subtitle').textContent=subtitle[name];
 if(name==='articles'&&category){$('article-filter').value=category;renderArticles();}
 if(name==='trials')renderTrials();if(name==='fda')renderFda();if(name==='guides')renderGuides();
 $('sidebar').classList.remove('open');window.scrollTo({top:0,behavior:'instant'});
 try{history.replaceState(null,'','#'+name);}catch{}
}
function renderOverview(){const d=state.data;if(!d)return;
 $('metric-articles').textContent=d.counts.recent;$('metric-trials').textContent=d.counts.trials;
 $('metric-fda').textContent=d.counts.fda;$('metric-recovery').textContent=d.counts.recovery;
 $('side-articles').textContent=d.articles.length;$('side-trials').textContent=d.trials.length;$('side-fda').textContent=d.fda.length;
 const ul=$('overview-list');ul.replaceChildren();for(const [i,a] of d.articles.filter(a=>a.category==='recent').slice(0,4).entries())ul.append(articleCard(a,i+1));
}
function articleCard(a,i){const tags=[{name:typeLabels[a.category]||'公开文献',cls:typeClasses[a.category]||''}];if(a.types&&a.types.includes('Systematic Review'))tags.push({name:'系统评价',cls:'blue-chip'});
 return card('article',a.title,a.url,tags,[a.journal,a.year?'期刊年份：'+a.year:'',a.doi?'DOI: '+a.doi:'','PMID '+a.id],i);}
function renderArticles(){const d=state.data;if(!d)return;const q=$('article-search').value.trim().toLowerCase();const filter=$('article-filter').value;
 const filtered=d.articles.filter(x=>(filter==='all'||x.category===filter)&&[x.title,x.id,x.journal,x.doi].join(' ').toLowerCase().includes(q));
 const result=$('articles-list');result.replaceChildren();$('articles-total').textContent=filtered.length+' 条记录';
 if(!filtered.length){result.append(element('div','empty','没有找到符合条件的论文题录。'));return;}
 filtered.forEach((x,i)=>result.append(articleCard(x,i+1)));
}
function trialCard(a,i){const st=trialStatus[a.status]||a.status;const tags=[{name:st,cls:a.status==='RECRUITING'?'':'blue-chip'}];for(const p of (a.phases||[]).slice(0,2)){tags.push({name:p.replace('PHASE','Phase '),cls:'violet-chip'});}
 const enroll=a.enrollment?.count;return card('trial',a.title,a.url,tags,[a.id,a.date?'注册信息更新：'+a.date:'',a.conditions?.slice(0,2).join(' · '),enroll!=null?'登记入组目标/人数：'+enroll:''],i);}
function renderTrials(){const d=state.data;if(!d)return;const q=$('trial-search').value.trim().toLowerCase();const filter=$('trial-filter').value;
 const known=['RECRUITING','NOT_YET_RECRUITING','ACTIVE_NOT_RECRUITING','COMPLETED'];
 const filtered=d.trials.filter(x=>(filter==='all'||(filter==='other'?!known.includes(x.status):x.status===filter))&&[x.title,x.id,...(x.conditions||[])].join(' ').toLowerCase().includes(q));
 $('trials-total').textContent=filtered.length+' 条记录';const result=$('trials-list');result.replaceChildren();
 if(!filtered.length){result.append(element('div','empty','当前样本中没有符合条件的试验记录。'));return;}
 filtered.forEach((x,i)=>result.append(trialCard(x,i+1)));
}
function renderFda(){const d=state.data;if(!d)return;const q=$('fda-search').value.trim().toLowerCase();const filtered=d.fda.filter(x=>x.title.toLowerCase().includes(q));
 $('fda-total').textContent=filtered.length+' 条官方公告链接';const result=$('fda-list');result.replaceChildren();
 if(!filtered.length){result.append(element('div','empty','无符合条件的监管公告。'));return;}
 filtered.forEach((a,i)=>result.append(card('fda',a.title,a.url,[{name:'FDA 美国',cls:'coral-chip'},{name:'待逐条审查'}],[a.status],i+1)));
}
function renderGuides(){const d=state.data;if(!d)return;const list=$('guides-list');list.replaceChildren();d.guidelines.forEach((g,i)=>list.append(card('guides',g.title,g.url,[{name:'正式出版索引'}],[g.publisher,g.doi?'DOI '+g.doi:'',g.note],i+1)));
 const sources=$('sources-list');sources.replaceChildren();d.sources.forEach(s=>{const row=element('div','source-item'),lhs=element('div');lhs.append(link(s.name,s.url),element('small','',s.url));lhs.firstChild.style.fontWeight='700';const good=s.status==='verified_http_response';append(row,lhs,element('span','source-status'+(good?'':' bad'),good?'HTTP '+s.httpStatus:'待核实'));sources.append(row);});
 const notes=$('data-notes');notes.replaceChildren(element('h3','','数据限制和质量说明'));
 d.dataWarnings.forEach(x=>notes.append(element('p','', '• '+x)));
 const unresolved=d.coverage?.recoveryMissingPmids||[];if(unresolved.length)notes.append(element('p','','• 康复综述存在未能从 PubMed XML 解析的标识符：'+unresolved.join('、')+'，没有伪造记录。'));
 notes.append(element('p','','• 本站展示可公开传播的研究题录与原始医学来源链接；数据收录不等于医学结论已经独立审核。'));
}
function fillHeader(){const d=state.data;if(!d)return;const iso=d.collectedAt||'';const date=iso.slice(0,10);$('snapshot-date').textContent=date||'未知';
 const diff=Math.floor((Date.now()-new Date(iso).getTime())/(86400*1000));$('snapshot-age').textContent=diff>7?'距上次采集 '+diff+' 天 · 数据可能过期':diff>0?'距上次采集 '+diff+' 天':'最近一次采集';
 $('footer-snapshot').textContent='数据快照：'+(d.snapshotId||date);}
async function init(){document.querySelectorAll('[data-view]').forEach(node=>node.addEventListener('click',()=>setView(node.dataset.view,node.dataset.category||null)));
 $('menu').addEventListener('click',()=>$('sidebar').classList.toggle('open'));
 $('disclaimer-close').addEventListener('click',()=>{$('.disclaimer').style.display='none'});
 for(const id of ['article-search','article-filter'])$(id).addEventListener(id==='article-search'?'input':'change',renderArticles);
 for(const id of ['trial-search','trial-filter'])$(id).addEventListener(id==='trial-search'?'input':'change',renderTrials);
 $('fda-search').addEventListener('input',renderFda);
 try{const res=await fetch('./data/site-data.json',{cache:'no-store'});if(!res.ok)throw new Error('HTTP '+res.status);
   const d=await res.json();if(d.schemaVersion!==1||!Array.isArray(d.articles)||!Array.isArray(d.trials)||!Array.isArray(d.fda))throw new Error('数据格式不兼容');
   state.data=d;fillHeader();renderOverview();renderArticles();renderTrials();renderFda();renderGuides();
   const target=location.hash.slice(1);setView(label[target]?target:'overview');
 }catch(err){$('overview-list').replaceChildren(element('div','empty','研究数据暂时无法读取。请稍后重试或打开官方医学来源查询。'));
 $('snapshot-age').textContent='数据加载失败';const t=$('toast');t.textContent='数据载入失败：'+err.message;t.classList.add('show');}}
init();
