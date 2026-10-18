'use strict';
// Conservative, rules-based Chinese context for English research *titles*.
// This is NOT a translation of paper results or an AI treatment recommendation.
window.BCIChinese = Object.freeze((() => {
  const TOPICS=[
    {words:['triple-negative','triple negative','tnbc'],zh:'三阴性乳腺癌',explain:'这类乳腺癌缺少三种常见治疗靶点，治疗选择需看分期和既往方案。'},
    {words:['her2'],zh:'HER2 相关乳腺癌',explain:'HER2 是一种肿瘤标志物，可能影响部分靶向药物是否适合。'},
    {words:['esr1','estrogen receptor','endocrine','hormone therapy'],zh:'激素受体与内分泌治疗',explain:'研究关注激素相关信号或药物，能否用于患者须由病理和具体获批适应证判断。'},
    {words:['pd-l1','checkpoint','immunotherap','immune'],zh:'免疫治疗',explain:'研究关注身体的免疫系统如何与肿瘤相互作用，不代表所有患者都可使用免疫药。'},
    {words:['antibody-drug','trastuzumab deruxtecan','adc'],zh:'抗体偶联药物（ADC）',explain:'这是一类把抗体与药物连接的治疗方式，是否适用要看分型和相关安全风险。'},
    {words:['radiotherap','radiation','breath-hold'],zh:'放射治疗',explain:'研究关注放疗技术、过程或副作用，不等于每位患者都需要放疗。'},
    {words:['reconstruct','surgery','breast-conserv','mastectomy'],zh:'手术与乳房重建',explain:'研究关注某类手术或术后问题，具体可选手术方式需要专科医生评估。'},
    {words:['lymphedema','survivorship','rehabilitation','quality of life','follow-up'],zh:'康复和长期随访',explain:'研究关注治疗后的身体或生活问题，康复计划应由医疗团队个别制定。'},
    {words:['mri','mammograph','ultrasound','screening','detection'],zh:'检查、影像与早期发现',explain:'研究关注筛查或检查技术，不能把检测工具直接当作确诊。'},
    {words:['genetic','germline','genomic','brca','biomarker'],zh:'遗传与肿瘤标志物',explain:'研究关注基因或肿瘤分子特征，不代表每个人都需要同一种检测。'},
    {words:['mitochondri','metaboli','multi-omics','proteomic','kinase'],zh:'肿瘤细胞和分子机制',explain:'主要探索肿瘤相关生物学现象，距离成为成熟诊疗方法可能仍很远。'},
    {words:['ferroptosis','nanogel','nanoparticle','nano-','mice','mouse','cell line','in vitro'],zh:'实验室基础研究',explain:'涉及细胞、材料或实验模型的研究，不能直接认为对患者已经有效。'},
    {words:['randomized','phase iii','phase 3','clinical trial'],zh:'临床研究设计',explain:'研究对象、比较方案、主要结果和副作用均需读原文确认。'}
  ];
  function match(title){
    const t=String(title||'').toLowerCase();
    return TOPICS.find(x=>x.words.some(w=>t.includes(w)))||null;
  }
  function isLab(title){
    return /(?:cell line|in vitro|in mice|in mouse|mouse model|murine|nanogel|photoacoustic synthetic|molecular network|proteomic|inhibitors in cells)/i.test(title||'');
  }
  function describeArticle(a){
    const subject=match(a.title);
    let title='乳腺癌相关医学研究：主题待人工核对';
    let summary='目前只有论文题录，尚未读取并专业审核完整研究设计和结果，不能据此判断治疗是否有效。';
    if(subject){
      title='关于“'+subject.zh+'”的研究';
      summary=subject.explain+' 这只是根据英文标题关键词生成的中文主题提示，并非研究结果或完整翻译。';
    }
    const lab=isLab(a.title);
    if(lab)summary+=' 题目提示可能涉及实验室、细胞或模型研究，不能直接用于患者治疗。';
    if(a.category==='case')summary='这是公开发表的病例报告索引：个别病例不能代表普遍治疗效果；请与医生讨论是否与自身情况有关。';
    if(a.category==='recovery'&&subject===null){title='康复与长期随访相关文献';summary='关注乳腺癌治疗后的身体和生活质量问题，具体康复建议需要医生评估。';}
    return {title,summary,original:a.title||'',sourceType:lab?'基础研究线索':'文献题录'};
  }
  const TRIAL_STATES={
    RECRUITING:'登记显示正在招募',NOT_YET_RECRUITING:'登记显示尚未开始招募',
    ACTIVE_NOT_RECRUITING:'研究进行中但不再公开招募',COMPLETED:'研究登记显示已完成',
    TERMINATED:'研究登记显示已终止',SUSPENDED:'登记显示暂停',
    WITHDRAWN:'研究已撤回',ENROLLING_BY_INVITATION:'仅邀请入组'
  };
  function describeTrial(t){
    const subject=match(t.title);
    return {
      title:'临床试验：'+(subject?subject.zh:'乳腺癌相关研究（具体方向待核对）'),
      summary:(subject?subject.explain:'试验登记记录的是研究计划或进度，不是证明治疗有效的结论。')+
        ' '+(TRIAL_STATES[t.status]||'招募状态尚未确认')+
        '；这只是国际注册状态，不代表在中国有招募地点，也不代表适合任何特定患者。',
      original:t.title||'',sourceType:'国际临床试验登记'
    };
  }
  function describeFDA(f){
    const subject=match(f.title);
    return {
      title:'美国药品监管动态：'+(subject?subject.zh:'乳腺癌相关药物'),
      summary:'美国 FDA 发布了有关该主题的官方公告线索。具体药物、适应证、风险和批准日期需逐条核实；美国批准不等于中国大陆批准。',
      original:f.title||'',sourceType:'美国药物监管公告'
    };
  }
  return {describeArticle,describeTrial,describeFDA};
})());
