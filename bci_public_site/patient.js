'use strict';
(function(){
  const $=id=>document.getElementById(id);
  const link=(text,url)=>{
    const a=document.createElement('a');
    a.textContent=text;
    try {
      const parsed=new URL(url);
      if(parsed.protocol!=='https:' || !['pubmed.ncbi.nlm.nih.gov','www.cancer.gov','www.china-oncology.com'].includes(parsed.hostname)){
        throw new Error('invalid public medical source');
      }
      a.href=parsed.href;a.target='_blank';a.rel='noopener noreferrer';
    }catch{a.removeAttribute('href');}
    return a;
  };
  function dateText(iso){
    const date=new Date(iso);
    return Number.isNaN(date.getTime()) ? '采集日期未知' : date.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',timeZone:'Asia/Shanghai'});
  }
  function addUpdate(parent,item){
    const row=document.createElement('article');row.className='update-card';
    const kicker=document.createElement('div');kicker.className='update-kicker';kicker.textContent='康复研究 · 原始文献题录';
    const title=document.createElement('h3');title.append(link(item.title||'查看原文',item.url));
    const meta=document.createElement('p');meta.className='update-meta';meta.textContent=[item.journal,item.year ? '期刊记录年份 '+item.year : '',item.id ? 'PMID '+item.id : ''].filter(Boolean).join(' · ');
    row.append(kicker,title,meta);parent.append(row);
  }
  async function load(){
    const holder=$('patient-updates');
    try{
      const response=await fetch('./data/site-data.json',{cache:'no-store'});
      if(!response.ok)throw new Error('公开资料暂不可用');
      const data=await response.json();
      if(data.schemaVersion!==1 || !Array.isArray(data.articles))throw new Error('数据格式暂不支持');
      const date=dateText(data.collectedAt);
      $('snapshot-date').textContent='资料采集于 '+date;
      $('footer-update').textContent='最近资料采集：'+date;
      const rec=data.articles.filter(a=>a.category==='recovery'&&a.url?.startsWith('https://pubmed.ncbi.nlm.nih.gov/'));
      holder.replaceChildren();
      if(rec.length===0){
        const empty=document.createElement('p');empty.className='loading';empty.textContent='暂无符合条件的公开研究题录。';holder.append(empty);
      }else{
        rec.slice(0,3).forEach(item=>addUpdate(holder,item));
      }
      const collected=new Date(data.collectedAt);
      const age=Number.isNaN(collected.getTime())?NaN:Math.floor((Date.now()-collected.getTime())/86400000);
      const limit=(data.scope||'')+' 近期研究题录为英文原文，未进行独立医学审核；'+
        (Number.isFinite(age)&&age>7?'快照已超过 7 天，可能并非最新资料。':'详情以原始医学机构与医师意见为准。');
      $('data-note').textContent=limit;
    }catch{
      holder.replaceChildren();
      const p=document.createElement('p');p.className='loading';p.textContent='近期研究列表暂时无法更新，其他权威资料链接仍可使用。';holder.append(p);
      $('snapshot-date').textContent='数据暂不可用';
      $('footer-update').textContent='近期研究未成功加载';
      $('data-note').textContent='请直接查看上方权威资料，并以接诊医生建议为准。';
    }
  }
  load();
})();
