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
const dom=new JSDOM(page,{url:'https://example.test/ashare-research-infra/',runScripts:'outside-only'});
dom.window.fetch=async url=>({ok:true,json:async()=>String(url).includes('monitor.json')?monitor:data});
dom.window.eval(fs.readFileSync(path.join(ROOT,'app.js'),'utf8'));
const $=id=>dom.window.document.getElementById(id);
function trigger(id,type){$(id).dispatchEvent(new dom.window.Event(type,{bubbles:true}));}
(async()=>{
 await new Promise(r=>setTimeout(r,80));
 assert.equal($('stat-items').textContent,String(curated.listings.length));
 assert.ok($('jobs-list').querySelectorAll('.job-card').length>0);
 assert.ok($('jobs-list').querySelector('.expanded .detail-matrix dd'));
 assert.ok($('sources-list').querySelectorAll('.source-card').length>=10);
 assert.ok($('sync-text').textContent.includes('Google Drive'));
 assert.ok($('jobs-list').textContent.includes('岗位'));
 // Selecting state grid really filters, not just decorates labels.
 $('filter-type').value='国家电网';trigger('filter-type','change');
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
 for(const a of dom.window.document.querySelectorAll('.jobs-list a[href]')){
   assert.equal(a.rel,'noopener noreferrer');
   assert.equal(a.protocol,'https:');
 }
 console.log('FINANCE_INTERACTIVE_DOM_OK initial_listings='+curated.listings.length);
 dom.window.close();
})().catch(e=>{console.error(e);process.exit(1);});
