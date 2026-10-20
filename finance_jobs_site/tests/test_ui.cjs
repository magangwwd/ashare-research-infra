/* Real DOM interaction smoke, not just syntax: jsdom with local Drive snapshot fixture. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const ROOT=path.resolve(__dirname,'..');
const page=fs.readFileSync(path.join(ROOT,'index.html'),'utf8');
const curated=JSON.parse(fs.readFileSync(path.join(ROOT,'data/opportunities.json'),'utf8'));
const data={...curated,collected_at:'2026-10-20T08:00:00Z',data_as_of:curated.as_of};
const monitor={schema:1,checked_at:'2026-10-20T08:00:00Z',sources:[],potential_new_announcements:[]};
const changes={events:[{kind:'new_curated_record',id:'j1',title:'新收录记录',source:'https://zhaopin.sgcc.com.cn',note:'入库，不代表今天才发布'}],
  total_events:1,counts:{new_curated_record:1},previous_collected_at:'2026-10-19T08:00:00Z'};
const qq={schema:1,source_url:'https://docs.qq.com/sheet/DS210WHFxVXdHcWJS?tab=BB08J2',
 checked_at_utc:'2026-10-20T12:00:00Z',status:'authorized_export_imported',
 qualified_count:2,rows_examined:12,leads:[
 {title:'会计岗',company:'上海某公司',work_city:'上海',deadline:'2026-10-31',
  third_party_url:'https://docs.qq.com/sheet/DS210WHFxVXdHcWJS?tab=BB08J2',
  role_confirmed:false,match_reasons:['专业匹配']},
 {title:'旧审计岗',company:'过期公司',work_city:'南京',deadline:'2026-10-01',
  third_party_url:'https://docs.qq.com/sheet/DS210WHFxVXdHcWJS?tab=BB08J2',
  role_confirmed:false,match_reasons:['过期应跳过']}
 ]};
const dom=new JSDOM(page,{url:'https://example.test/ashare-research-infra/',runScripts:'outside-only'});
dom.window.fetch=async url=>({ok:true,json:async()=>String(url).includes('monitor.json')?monitor:String(url).includes('changes.json')?changes:String(url).includes('qq_feed.json')?qq:data});
dom.window.eval(fs.readFileSync(path.join(ROOT,'app.js'),'utf8'));
const $=id=>dom.window.document.getElementById(id);
function trigger(id,type){$(id).dispatchEvent(new dom.window.Event(type,{bubbles:true}));}
(async()=>{
 await new Promise(r=>setTimeout(r,80));
 assert.equal($('stat-items').textContent,String(curated.listings.length));
 assert.ok($('qq-feed-status').textContent.includes('扫描 12 行'));
 assert.equal($('qq-shortlist').hidden,false);
 assert.equal($('qq-shortlist-list').querySelectorAll('.qq-lead').length,1);
 assert.ok(!$('qq-shortlist-list').textContent.includes('过期公司'));
 assert.ok($('qq-shortlist-list a[href]').getAttribute('href').startsWith('https://docs.qq.com/'));
 // All jobs must be visible immediately, not behind a load-more button.
 assert.equal($('jobs-list').querySelectorAll('.job-card').length,curated.listings.length);
 assert.ok($('job-filter-count').textContent.includes('已全部展示'));
 assert.equal($('jobs-more'),null);
 assert.equal($('back-to-top').getAttribute('href'),'#home');
 assert.equal($('back-to-top').textContent.trim(),'回到首行 ↑');
 assert.ok($('jobs-list').querySelectorAll('.job-card').length>0);
 assert.ok($('job-detail').querySelector('.expanded .detail-matrix dd'));
 assert.ok($('jobs-list').querySelectorAll('.job-card-action').length>0);
 assert.ok($('job-detail').textContent.includes('报名'));
 assert.ok($('job-detail-pane').getAttribute('aria-label').includes('职位'));
 assert.ok($('sources-list').querySelectorAll('.source-card').length>=10);
 assert.ok($('sync-text').textContent.includes('Google Drive'));
 assert.ok($('jobs-list').textContent.includes('岗位'));
 assert.ok(Number($('category-count-all').textContent)>=50);
 assert.ok(Number($('category-count-core').textContent)>0);
 assert.ok(Number($('category-count-related').textContent)>0);
 assert.ok(Number($('category-count-watch').textContent)>0);
 assert.ok($('audit-status').textContent.includes('新入库档案 1 条'));
 assert.ok($('changes-list').textContent.includes('新收录记录'));
 const coreTab=dom.window.document.querySelector('[data-category="core"]');
 coreTab.click();
 assert.equal(coreTab.getAttribute('aria-pressed'),'true');
 assert.ok($('jobs-list').textContent.includes('财务')||$('jobs-list').textContent.includes('审计'));
 const nCore=Number($('category-count-core').textContent);
 assert.equal($('jobs-list').querySelectorAll('.job-card').length,nCore);
 const watchTab=dom.window.document.querySelector('[data-category="watch"]');
 watchTab.click();
 assert.ok($('jobs-list').textContent.includes('招聘')||$('jobs-list').textContent.includes('财务'));
 dom.window.document.querySelector('[data-category="all"]').click();
 assert.equal($('jobs-list').querySelectorAll('.job-card').length,curated.listings.length);
 // A job card opens its own complete detail without routing to an unrelated page.
 const cards=$('jobs-list').querySelectorAll('.job-card');
 assert.ok(cards.length>=2);
 const secondId=cards[1].dataset.jobId;
 cards[1].querySelector('.job-card-action').click();
 assert.equal($('jobs-list').querySelector('.job-card.selected').dataset.jobId,secondId);
 assert.ok($('job-detail').textContent.includes(curated.listings.find(x=>x.id===secondId).company));
 const third=$('jobs-list').querySelectorAll('.job-card')[2];
 third.focus();third.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
 assert.equal($('jobs-list').querySelector('.job-card.selected').dataset.jobId,third.dataset.jobId);
 // Search and quick city chips update the exact same filtering state.
 $('hero-search').value='华能';$('hero-region').value='上海';$('hero-search-go').click();
 assert.equal($('filter-text').value,'华能');assert.equal($('filter-region').value,'上海');
 assert.ok($('jobs-list').textContent.includes('华能'));
 dom.window.document.querySelector('[data-quick-region="马鞍山"]').click();
 assert.equal($('filter-region').value,'马鞍山');
 $('reset-filters').click();
 $('filter-sort').value='newest';trigger('filter-sort','change');
 assert.ok($('jobs-list').querySelectorAll('.job-card').length>0);
 $('filter-sort').value='deadline';trigger('filter-sort','change');
 assert.ok($('jobs-list').querySelectorAll('.job-card').length>0);
 $('reset-filters').click();
 // On mobile, view details as an independent overlay, close with Escape or overlay.
 dom.window.matchMedia=()=>({matches:true,media:'(max-width: 860px)'});
 $('jobs-list').querySelector('.job-card-action').click();
 assert.ok($('job-detail-pane').classList.contains('mobile-open'));
 assert.equal($('detail-backdrop').hidden,false);
 dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 assert.ok(!$('job-detail-pane').classList.contains('mobile-open'));
 $('jobs-list').querySelector('.job-card-action').click();
 $('detail-backdrop').click();
 assert.equal($('detail-backdrop').hidden,true);
 dom.window.matchMedia=()=>({matches:false,media:'(max-width: 860px)'});
 // Selecting state grid really filters, not just decorates labels.
 $('filter-type').value='国家电网';trigger('filter-type','change');
 const gridCount=Number($('job-total').textContent.match(/\d+/)[0]);
 assert.equal($('jobs-list').querySelectorAll('.job-card').length,gridCount);
 assert.ok($('jobs-list').textContent.includes('国家电网'));
 assert.ok(!$('jobs-list').textContent.includes('中国银行2027'));
 $('filter-type').value='all';trigger('filter-type','change');
 $('filter-status').value='closed';trigger('filter-status','change');
 assert.ok($('jobs-list').textContent.includes('中国银行'));
 $('filter-status').value='all';trigger('filter-status','change');
 $('filter-overseas').checked=true;trigger('filter-overseas','change');
 assert.ok($('jobs-list').querySelectorAll('.job-card').length>0);
 $('filter-overseas').checked=false;trigger('filter-overseas','change');
 $('filter-qs').value='85';trigger('filter-qs','input');
 assert.ok($('qs-message').textContent.includes('85'));
 assert.ok($('qs-message').textContent.includes('不能')||$('qs-message').textContent.includes('不据此'));
 $('reset-filters').click();
 assert.equal($('filter-qs').value,'');
 assert.equal($('filter-status').value,'all');
 assert.ok($('jobs-list').querySelectorAll('.job-card').length>0);
 // No unsafe external open; all linked approved source URLs use safe rel.
 for(const a of dom.window.document.querySelectorAll('.job-detail-pane a[href], .jobs-list a[href]')){
   assert.equal(a.rel,'noopener noreferrer');
   assert.equal(a.protocol,'https:');
 }
 console.log('FINANCE_INTERACTIVE_DOM_OK initial_listings='+curated.listings.length);
 dom.window.close();
})().catch(e=>{console.error(e);process.exit(1);});
