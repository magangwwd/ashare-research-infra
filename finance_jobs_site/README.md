# 财会校招情报站（2027届）

公开网址：https://magangwwd.github.io/ashare-research-infra/

## 目标

面向应届毕业生，特别关注境外留学生、QS大学排名和财会/会计/审计/税务/金融岗位。以江浙沪、深圳、安徽马鞍山为重点，国家电网、银行、央国企和事业单位优先。使用此前已上线的 GitHub Pages 框架，**完全替换原先的乳腺科普公开页面**。

## 公开信息质量约束

**招聘单位的公告 ≠ 在招的财会岗位；官网HTTP 200 ≠ 报名仍开放。**

一条已确认的职位必须有明确公告出处、单位及岗位名称。本站不会因集团公告提到“财会类”就宣称江苏某县国家电网在招财会岗位，也不会推断报名截止日、名额或QS门槛。

详细档案包含：
- 招聘单位和确切岗位（若未知明确标示）；职位归类、工作地点、招聘人数、专业、学历、毕业届别；
- 留学生资格、留服认证截止、学制限制、是否有明确 QS 排名门槛；
- 招聘发布时间、报名开始/截止时间、考试流程、报名材料、正式报名链接与公告；
- 证据层级、风险提醒、资料核查日期、是否需要校对实际工作城市；
- **距报名截止不足7天的记录加粗优先提示**；已结束或资格受限的公告不能伪装成可报名岗位。

用户可按城市、招聘主体、是否接受海外毕业生、岗位实际核实状态筛选；QS是可输入的个人指标，不会代替招聘条件判定录取资格。默认本地交互，不收集个人简历与隐私。

## 更新机制（独立，不影响暂停的医学项目）

专用 Google Drive 文件夹：[Finance_Careers_Intelligence](https://drive.google.com/drive/folders/1_-tsriPoUm2KosCuC8xHIuzAwZLvweub)。

1. `.github/workflows/finance-jobs-daily.yml`：约北京时间 07:35 每天检查国家电网、银行、国资委、人社等官方招聘入口；有限提取有相关关键词的公告**线索**（不作为核实后的职位）；合并经过核对的岗位档案，保存不可变的 `finance_YYYYMMDDTHHMMSSZ_runid.json` 到专用云盘。
2. GitHub Actions 从云盘读回上传文件，**SHA-256必须一致**，否则工作流失败。
3. `.github/workflows/finance-jobs-pages.yml`：收集成功后自动从该专用 Drive 文件夹下载最新快照，并重新生成只含公开信息的网页；另于约北京时间 08:20 备用检查。
4. `.github/workflows/finance-jobs-public-smoke.yml`：对公网 HTML、脚本、样式以及最新公开JSON做独立验证。

每日更新包括**实际监控时间与采集状态**，不代表每天都有新增岗位。岗位官方截止日按北京时间计算，展示的截止日期始终以公告为准。用人单位网站如果采用登录/JS动态接口导致无法直接读取，网站标明“无法自动读取”，并提供官方跳转供人工核对。**没有核实到的官方QQ群、二次采集线索或学历限制，不自动升级为正式空缺。**

### 与原医学项目的隔离

原 `Breast_Cancer_Intelligence` 云盘资料和旧工作流已暂停，并继续保留归档；**不重新开启其定时流程**。财务项目有独立的目录、脚本、数据文件、工作流与 GitHub Pages 部署，公开网站不含医学内容。

## 目录

- `data/opportunities.json`：有出处的详细公告档案和官方监测入口（版本管理）
- `scripts/update_sources.py`：限制精确域名的官方来源监测；状态和新链接均不推断岗位
- `scripts/create_snapshot.py`：合成每日事实快照
- `scripts/publish_drive.py`：Drive JSON 只追加上传 + 真实读回SHA验证
- `scripts/read_drive.py`：下载云盘最新符合格式的日期快照
- `scripts/extract_public.py`：公开数据字段/结构验证后写入 `_site/data`
- `app.js`, `styles.css`, `index.html`：响应式互动招聘页
- `tests/test_contracts.py` & `tests/test_ui.cjs`：数据准确、筛选互动、截止日期与链接安全测试

## 维护方法

要将某条监测线索升级为“真实财会招聘岗位”，应查看原始公告确认单位、职位、专业、报名时间、岗位地点、留学生条件及其他边界，并更新 `opportunities.json` 后由 Actions 重新生成云盘快照。**非实际报名岗位的数据永远不能因为定时程序运行就变成“正在招聘”。**

可用手动 `workflow_dispatch` 验证真实链路；自动运行由 GitHub 默认分支定时任务负责（可能因平台拥塞延迟）。
