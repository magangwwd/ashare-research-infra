# 通用自主研发与 GPT 审核接力自动化标准方案

> 标准：AUTO-RD-SOP-001 | 版本：v1.0.1 | 2026-10-21 | 范围：BTC、ETF、A股和后续研发项目  
> 规范代码以 GitHub 主分支版本为准；Google Docs 是易读副本。严格区分“设计方案”“已部署程序”“真实端到端验收”。所有量化研究默认 NOT_FOR_TRADING。

## 一、总原则与目标

**GitHub负责可信、可重复的计算；GPT负责基于证据分析问题、自主选择下一任务并在授权范围内实现和验收；共享任务队列和不可变验收包保证跨轮次接续；独立看门狗保障故障不静默。**

期望闭环：项目目标→任务拆解→GitHub运行→证据验收→事件触发GPT审核→GPT派发可执行下一项→GitHub继续。任务完成不等于策略批准，更不等于交易许可。

## 二、ETF与BTC现状：可复用的两种优点

| 项目 | 运行设计 | 已核实历史证据（2026-10-21） | 尚需完善 |
| --- | --- | --- | --- |
| ETF | GPT每小时条件检查；GitHub一次运行预设阶段；research_backlog.json记载优先级、依赖；run_checkpoint.json记录阶段/校验/恢复 | 运行39597990839成功；DATA-003尚在推进，DIV-004具备执行条件 | 统一跨项目验收包、真正事件接力、变更评估 |
| BTC | GitHub每小时计划巡检，按数据+代码指纹决定深度实验；单独漏触发兜底；GPT每天02/08/14/20（北京）审核 | 运行39589940046：第一次完整研究并Drive读回，第二次相同指纹真实跳过重复回测；39596575303真实schedule成功 | 建立与ETF同等结构化Backlog及依赖图；事件唤醒GPT |

这些是过去已核实的运行记录，不代表后续一直无故障。当前GPT定时任务不是24小时在线的自主编程系统。

## 三、统一架构和职责边界

1. **目标与约束 Project Mandate**：研究目标、数据许可、时间范围、风险目标、禁止动作、验收门槛；默认不交易。
2. **任务队列 Backlog**：唯一结构化任务列表，包含ID、优先级、依赖、状态、验收标准、风险级别、负责人、上次证据、下一行动。GPT每次从READY队列自主挑一个最重要且可验收的任务。
3. **GitHub执行器**：有界工作流，预定脚本可以连续完成多个阶段；互斥并发、费用预算、超时、幂等指纹、失败重试和熔断。
4. **验收收据 Receipt**：repo/project/task_id、run_id/attempt、触发事件、代码/数据/配置SHA、数据截止日、测试、输出文件SHA、Drive读回、风险判定和下一任务。必须能独立追查。
5. **GPT决策层**：读最新Backlog和真实GitHub证据→先处理P0/P1异常→选择最高价值READY事项→必要时编写代码/测试、建隔离PR、尝试触发一次研究→验证实际结果→将状态与下一步写回GitHub。不能执行时写明阻碍，不假称已经派工。
6. **独立监控与调度**：不把“工作流存在”当作“已自动运行”；独立检查GitHub schedule、数据新鲜度和Drive字节，并去重告警。
7. **自我评估与治理**：每周检验成果、时效、成本、出错/误报、权限和策略有效性；改变研究方案必须进行有对照的真实测试。

## 四、状态机与有据接续

**Receipt 事件真实性（v1.0.1）**：GitHub PR 触发的独立 CI 必须在收据中记录真实事件 `pull_request`；Schema 已兼容该枚举。不得为使校验通过而伪写为 `manual_external` 或 `push`。本次为向后兼容的枚举补充，现存 Receipt 的 `schema_version: 1.0.0` 不变。

状态：NEW → READY → CLAIMED → RUNNING → VERIFYING → REVIEW_REQUIRED → VERIFIED_DONE；失败进入RETRYABLE_FAILED或BLOCKED；可进入CANCELLED。只有证据完整并由验收程序复核才是VERIFIED_DONE；策略层另有NOT_FOR_TRADING安全状态。

- 依赖只接受VERIFIED_DONE，不能因为“测试存在”“代码推送”“自述成功”解锁。
- 任务唯一锁：task_id + repo_sha + data_sha + config_sha，禁止双代理重复执行。相同数据/参数/代码且已有合格回测必须跳过。
- 失败先区分环境/网络、数据缺失、程序缺陷、研究假设失败。网络类有限指数退避；同一失败超限则阻塞并记录Issue。
- 优先级：安全与数据真实性 > 因果与样本外验收 > 周期识别及V3/HMG/CASH映射 > 费用/回撤压力 > 前瞻账本 > 报告；无需GPT时GitHub先完成预编排的全部阶段，不必每步等待GPT。
- 必须区分CODE_WRITTEN / UNIT_TESTED / RUN_CONFIRMED / ARTIFACT_VERIFIED / DRIVE_READBACK_VERIFIED / RESEARCH_ACCEPTED，严禁跨级报告。

### 建议频率

| 功能 | ETF | BTC |
| --- | --- | --- |
| 数据更新 | 按交易日/官方源实际发布 | 按官方数据发布、市场粒度与交易所限流 |
| GitHub轻量监控 | 独立告警或每小时 | 每小时 |
| GPT自主接续 | 现有每小时条件巡检 | 现有北京02/08/14/20（每6小时） |
| 完整研究 | 有READY任务且符合成本/依赖时 | 有新已核验数据、代码或不同待验任务时 |
| 日志和结果归档 | 每个真实新Run | 每个真实新Run |
| 架构复盘 | 每周一次 | 每周一次 |

## 五、实现“GitHub完成立即唤醒GPT”的技术路线（关键）

### 路线A：ChatGPT Work原生GitHub事件任务——建议先试点

官方已提供Work中基于GitHub**支持的Pull Request活动**创建事件触发任务的功能，但**不等于任意GitHub Actions完成就会唤醒当前对话GPT**。必须在Work中检查自己账户实际可用的GitHub事件及授权；可能需要账户主动配置。参考：https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt

可选桥接（尚未实施）：
1. GitHub workflow_run.completed监听白名单研究workflow，读取可信元数据，生成**脱敏验收收据**。
2. 新建唯一的“待审收据”Draft PR（不能让工作流自动合并），PR内容仅包含task_id、run链接、验证哈希与结果状态；严禁秘密信息。
3. 在ChatGPT Work设置授权仓库的GitHub PR活动触发器，配置只处理已验证研究收据的审核任务。
4. 实测GitHub完工→PR→Work任务启动→审查→GitHub下一任务的时间与结果。机器创建PR能否触发、实际延迟和审批行为必须实测；若不支持，则不可声称成功。

设置入口：ChatGPT Work → 创建事件触发任务 → GitHub连接 → Trigger/Condition/Prompt/授权 → Scheduled管理。**当前普通的GPT日程任务接口不能代替这一步**。

### 路线B：GitHub Webhook + 事件服务 + OpenAI API智能体——全面事件覆盖

当需要监听任意成功/失败的GitHub Actions完成事件时，使用GitHub webhook (workflow_run.completed) → Cloud Run/Cloudflare Worker或其它受控事件入口 → 验签/幂等/队列 → OpenAI API Agent读取GitHub日志与Artifact并审查 → 通过最小权限GitHub App创建PR或触发workflow_dispatch → 新一次GitHub真实运行和验证。**这启动的是独立OpenAI API Agent，不是“同一ChatGPT聊天窗口立刻在线”**。需要新建外部服务、密钥管理、账户授权与API计费；本方案仅设计，尚未开通或部署。

必须实现：X-Hub-Signature-256验签、仓库/分支/workflow白名单、run_id+attempt去重、超时/队列/失败死信、按项目成本上限、最多自动连续循环次数、审计日志、停止开关。日志/PR正文为不可信输入，不能当作GPT指令。App Token仅授权所需Repo和动作，OpenAI Key仅保存在服务端Secret Manager，禁止放在公开仓库/Artifact；高风险行为一律人工批准。

GitHub workflow_run能访问更高权限，链式工作流层数有限制；避免执行不可信PR代码或Artifact而继承写令牌。参考：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

**路线选择：**已有Work连接且PR事件真实可用时先选A以降低外部运维；希望任意工作流完工近实时接力则选B。可把B用于工程状态派工、A用于人工可解释PR审查，必须同一个幂等锁，不能两个GPT同时改同一任务。

## 六、逐阶段实施及验收

| 阶段 | 工作 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| P0标准化 | 本文、Backlog模板、Receipt Schema、优化日志及Drive副本 | 可打开并能从GitHub追溯版本 | 本次落地范围 |
| P1任务协议 | ETF沿用现有队列，BTC新增依赖队列和统一收据，不改冻结策略 | 一个任务GPT选择、Github运行、验收并解锁下一READY | 待实施 |
| P2原生事件桥 | Work GitHub PR触发 + 沙盒的测试收据PR | 真正由PR事件启动审查，有Run/事件/审查时间 | 待用户在Work授权及实测 |
| P3独立事件代理 | 按需开发Webhook服务和OpenAI API Agent | 真正workflow完成→Agent审核→下发下一任务，P95延迟<5分钟（目标非保证） | 未部署，需外部权限和预算 |
| P4可靠性改进 | 7天与30天跟踪，真实故障注入、成本/安全复核 | 故障、重复事件、恶意日志、数据缺失都不会错误推进 | 待持续采样 |

完成等级：L1固定流水线能正常归档；L2有GPT读Backlog并真实接续；L3完成事件唤醒真实链路；L4累计7/30天有据稳定。**即使到L4也不等于允许量化策略实盘**。

## 七、每周优化指标

- **接力延迟**：GPT或API Agent实际开始审查的时间 − GitHub运行完成时间，分别记录固定定时与真正事件触发。
- **有效推进率**：本期达到VERIFIED_DONE并形成下一READY的独立任务数，而不是GitHub运行次数。
- **重复计算率**：相同指纹又重复全量回测的次数/总回测次数，越低越好。
- **真实性**：宣称完成的任务是否都同时有Git SHA、原始数据来源和日期、官方Run、产出哈希/校验、必要Drive回读。目标100%。
- **调度可靠性**：预期计划次数、真实schedule次数、迟到/失败/补启动次数，切勿把push当schedule。
- **失效诊断**：不同市场状态的策略收益、回撤、成本、误切换、历史样本外与真正逐日前向，不能混写。
- **预算和安全**：Actions分钟数、Drive使用量、API token与调用成本、失败重试预算、错误权限请求与高风险动作。越权次数必须为0。

改进方法：提出单项假设→冻结评估方法→创建隔离PR→真实运行比较→保存证据→批准推广。每个版本追加CHANGELOG，不覆盖历史失败凭据，不能用反复看过的历史段宣称独立盲样。

## 八、故障优先级和边界

- P0：泄密、原始数据污染、交易权限越权、冻参被改 → 暂停相关写操作，留证据并等用户明确授权。
- P1：官方数据缺口、云盘读回不一致、前向账本缺日、时间泄漏 → 阻断验收，有限自动修复，不将失败标成功。
- P2：schedule迟到、重复回测、非关键单测失败 → 有界重试、记录Issue并继续不相关READY项。
- 不接实盘、不改冻结V3 SHA d60c667b3654dbfb00efea9bc72241cca3bb8d5c、不编K线、不在跌势/高波动HMG加仓、不偷改ETF8只基金和单仓原则；25%历史回撤目标非未来保证。

## 九、新项目复用清单

1. 建立PROJECT_MANDATE.md：真实目标、数据来源、权限、不可变配置和终点验收。
2. 复制本目录research_backlog.template.json，明确任务优先级、依赖、验收；禁止没有验收标准的无限任务。
3. GitHub预编排有界执行，设置并发锁、重试/算力配额、数据/代码/实验指纹和独立Watchdog。
4. 使用handoff_receipt.schema.json产生可机审的结果，执行完先VERIFYING，再审查，只有证据充分才VERIFIED_DONE。
5. 选择GPT定时审核/Work PR事件/API webhook agent，配置恰当权限；新项目默认先从L1和L2实施，不直接上L3无权限通道。
6. 每次GPT轮次先读**GitHub本规范最新版→项目Backlog→最新真实Run/Artifact和Drive证据**，先异常后READY任务；能做就实施并验收，不能做就标BLOCKED。每周记录改进。

## 十、持久存储与记忆

**GitHub的本SOP、Schema、Backlog和CHANGELOG是项目长期记忆的权威资料**，Drive Google Doc是方便阅读的副本；今后无论新会话、新项目还是定时GPT都应优先读取最新仓库文件，避免依赖模型可能过时的记忆摘要。此类文档持久保存并不代表已直接写入ChatGPT个人Memory；个人Memory需在ChatGPT设置里的Personalization自行查看/调整。

**2026-10-21的真实边界**：本次实施通用标准和模板/副本；并未部署或验收GitHub→ChatGPT Work事件触发与Webhook+OpenAI API服务。GitHub完成就立即唤醒GPT、无限自主创建任务、全自动达到策略目标仍属于后续需独立验收的工程目标。
