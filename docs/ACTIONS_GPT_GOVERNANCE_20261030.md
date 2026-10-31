# GitHub Actions × GPT 统一调度与审核（2026-10-30）

## 执行原则
- GitHub Actions 负责确定性的定时采集、计算、质量门禁、归档和告警；不把自动计算等同于 GPT 决策。
- GPT 通过已存在的定时任务审核 GitHub 证据，决定修复、继续研究、验收或停止；禁止重复触发同一轮采集。
- 失败保留日志、run ID、commit SHA、数据日期、Drive 回执；不得伪造成功或删除历史。
- 仅在确认调度重复且有替代门禁后才精简 cron；不可直接关闭唯一数据采集或唯一健康检查。

## 项目分工
| 工作域 | GitHub 主执行 | GPT 审核入口 | 后续治理 |
| --- | --- | --- | --- |
| A股 Task01 | task01.yml 夜间主备 | 每天 08:00 晨检 | 由晨检检查成功、缺口、质量、Drive 发布，不重复启动已成功任务 |
| A股 Gold/主板整合 | astock-quant-drive-first.yml 与整合验证 | 主板整合自主推进任务；完成后纳入 08:00 | 数据源、存储和口径统一验收前不删旧数据 |
| 600031 | main 根目录未发现600031专属 Actions 自动触发（详见清单） | 专项 GPT 定时已暂停 | 不得将600351误认作600031，也不得误停共享Task01/Gold |
| ETF | etf-research-one-shot.yml 显式触发 | 每4小时 GPT 研究接续 | 不擅自增加 GitHub 定时回测；按研究队列与回执决策 |
| BTC | 小时监测、有限深度研究、OKX归档、前向观察与看门狗 | 每天2次 GPT 研发审核 | 对比 run ID、输入日期、产物哈希，防止重复回测；保留必要数据与告警 |
| 海外数据 | overseas-data-collector.yml 主采与补采 | 已由现有每天08:00 A股晨检覆盖（Issue #124）；不另建任务 | 由现有 A股晨检的海外数据章节实际审核最新运行、Drive快照、质量与前瞻验证；不得重复创建任务 |
| 财会招聘 | finance-jobs-daily.yml / finance-jobs-pages.yml | 用户已要求取消 | 2026-10-30 移除 schedule、自动 push/联动触发，保留手动和历史数据 |

## 验收标准
1. 每个启用的 cron 必须有目的、负责人（GitHub/GPT）、失败告警、输出证据、复查频率。
2. 每轮 GPT 报告仅说明：成功/失败/未核实、影响、是否需处理、下一步。
3. 一周观察重复执行与空跑率；没有证据不直接合并不同策略/数据口径。
4. 未核验全部 Actions 启用状态、最近 runs 和日志之前，不宣称已完成全仓调度去重。

## 本次实施范围
- 已修改招聘自动化为仅手动；其他业务 cron 暂时保留，避免误停采集。
- 本文是治理基线，不代表 GPT 新审核任务已建立或 GitHub 运行验收通过。

## 2026-10-30 逐文件核查记录
- 在 main 逐一读取13个重点 workflow 文件：task01、astock-quant-drive-first、overseas-data-collector、btc-v3-watchdog、btc-research-scheduler-guard、btc-okx-freshness-watchdog、btc-okx-market-archive、btc-v3-forward、btc-regime-router、btc-hourly-research-cycle、finance-jobs-daily、finance-jobs-pages、gdrive-auth-healthcheck。
- 其中11个保留 schedule；finance-jobs-daily 和 finance-jobs-pages 均已无 schedule。前者仅 workflow_dispatch，后者保留 pull_request 与 workflow_dispatch。招聘 public-smoke 仍是由网页发布成功后 workflow_run 触发，手动发布仍会触发验收。
- 现有08:00 GPT任务已包含海外数据质量检查、Issue #124、Drive回读、海外研究决策及前瞻跟踪，**无需新增 GPT 任务**；自动化账户启用任务数为5/5。
- 历史 run 40868708466（海外）两项 jobs success；39599580852（ETF）单项 job success。它们仅证明历史运行，**不代表2026-10-30最新定时运行正常**。
- 当前连接器不支持列举所有 workflows / 最新全部 runs，不能声称完成全仓运行验收；在获取实时运行列表与完整日志之前，不关闭其他11个 cron，也不删除旧数据。
- 后续重点：获取每个 cron 最近3次运行的状态、耗时、失败步骤和产物；BTC按采集/研究/前瞻/健康/调度分组判断重复，A股按 Task01/Gold 输出与Drive版本判断重复。

## 2026-10-30 最新执行审计
- 全量读取根目录55个 `.github/workflows/*.yml`，其中11个带 cron；完整清单见 `docs/ACTIONS_INVENTORY_20261030.md`。这替代早先仅13个重点文件的局部盘点，不代表全部最近运行已验收。
- 根目录55个 workflow YAML 未出现精确股票代码600031；因此目前**未识别需停用的600031专属GitHub自动动作**，但专项GPT定时已暂停。保留共享行情和Gold采集。
- 发现离线多角色投研 Run 40954899551 因真实候选4只、请求上限5只而被等值断言误拒；修复及五项离线回归测试提交至草稿PR #134，待CI、复核及真实发布验收，不视为已上线。
- 本次专项 GPT 决策证据与完成条件统一追踪 Issue #128；专项验收完成后仅停用整合 GPT 任务。

## 2026-10-30 多轮续查：关键证据与安全决策
- PR #134 为草稿、未合并；变更仅限 `ai-research-data-only.yml` 的报告数量断言及5项回归测试。CI run 40992107948 offline-unit-tests=success、verified-drive=skipped，故不得宣称真实发布修复成功。原 run 40954899551 verified-drive=failure。
- Task01 run 40948715414 与 40972437556 的 task01 job 最终均为 cancelled，drive-publish 均 skipped；必须调查取消原因及是否有其他已验收主备运行覆盖，不能仅凭这两次推断全日数据缺失。
- Task01 `concurrency.group=task01-data` 且 `cancel-in-progress=false`；有 schedule 三时点及 daily-guard，故取消状态不应在无证据下归咎于 concurrency 自动取消。下一轮应检查对应 run/job 事件和取消者、日志、后续运行。
- 海外采集的 `push` 允许 main 及两个明确的 feature 分支，且 collect-and-publish 条件允许这些 feature 分支进入带凭证发布作业；这是需安全审查的授权边界，不能贸然取消已部署的业务流程。建议最小化为仅 main 可发布，PR/feature 分支只测试；通过隔离分支PR及CI验证后再合并，保留必要手动发布的main权限。
- 暂未修改生产 cron；没有经过实际运行/Drive读回的精简不得标为完成。

## 2026-10-30 三轮执行续记
- 轮1：复核PR #134（draft/open，报告数量限制1..requested及Clean一致性、5个回归用例），CI 40992107948 offline-unit-tests success、verified-drive skipped；原40954899551 verified-drive failure，未合并。
- 轮2：复核Task01 runs 40948715414、40972437556，均 task01 cancelled、drive-publish skipped；`cancel-in-progress=false`，不可武断归因于并发自动取消。海外工作流发现两个feature分支也可触发带凭证发布作业。
- 轮3：已在隔离分支 `fix/overseas-main-only-publish-20261030` 提交最小权限门禁修复 `dbf2cd4034999f9e1f928f238b78b74e53914072`，新建草稿PR #138，仅main可执行海外`collect-and-publish`，PR测试仍保留；待CI与正式Drive回读验收，不能宣称已上线。
- 本轮未改生产cron、未删除数据、未合并PR。下轮优先查PR #138 CI与Task01取消原因，随后推进PR #134的生产验收。

## 2026-10-30 19:00 后续实装与根因验收
- **已合并海外发布权限门禁**：PR #138，merge `ab88d9a09f41bfd70b1461f7d4c3016b9c21932e`；主分支 `overseas-data-collector.yml` 的 `collect-and-publish` 只允许非PR且 `refs/heads/main`，两个开发分支只运行安全测试。新回归测试已进main。CI 41045431736、41045431722 success。正式Drive读回待后续运行证据。
- **Task01 取消直接原因明确**：run 40948715414 job 23:01:08Z→03:01:26Z、run 40972437556 job 03:04:16Z→07:04:42Z，均运行约240分钟，与 `task01.timeout-minutes: 240` 一致；两次都在Drive发布步骤取消，前置验收success，checkpoint成功上传，生产current未获新发布回执。不是已证实的HTTP410或concurrency取消。
- **已有PR #123** 提出超时后独立发布/只读staging审计，最新head 05af987e... 的Pipeline CI 41023592444 success，但尚无真实Drive成功回读及额外运行成本证明；继续保留草稿，不重复提交同类生产补丁。
- **下一轮**：确认合并后海外主分支首次实际发布与Drive指针，核对Task01恢复方案及成本，推进PR #134正式发布；所有数据和长期守卫保留。

### 2026-10-30 19:xx 海外发布权限变更：生产验收完成
- PR #138 合并后 main run [41045590115](https://github.com/magangwwd/ashare-research-infra/actions/runs/41045590115) 两个jobs均success，artifact 12972626053。
- Google Drive `Ashare_Quant/overseas_research/current.json` (ID `1V9QaRl4Sn6DJE9e0XzkgjfP2APnElgqG`) 实际读取后指向 `snapshot.json` (ID `1kgEKokgdDQYScdU_64r3wKdFjILZ1oPn`)；文件SHA256实际下载计算 `6913aea6f8f98b0b24e1f5058ba919d2e21cb3b0fd7d07d51ec541fe0ab9f1f9` 与指针相符，快照时间 `2026-10-30T11:06:21Z`。
- 数据质量 `passed_with_gaps`：Treasury/EIA/TWSE/ECB/SCFI已接受，TSMC与7个SEC来源仍缺失或无效。**只认定发布权限门禁与快照读回通过，不认定海外数据全覆盖或回滚演练通过**。

### 2026-10-30 19:xx 自动投研短名单修复上线
- PR #134 已squash合并main：`3d085f2647900e6d846854f57512a9765941428b`。保留纸面研究限制，修复候选股票数小于上限导致Drive发布错误中断，新增5个断言回归测试。PR CI 40992107948 离线127项success。
- **尚缺**合并后main实际`verified-drive`作业及Drive发布回读；不可将离线测试通过等同完整生产修复。下轮核验新main `ai-research-data-only.yml` 运行证据。

### 2026-10-31 10:25 CST — 发布滞后与恢复前安全门禁
- 重新从Google Drive独立读取Task01 `current.json`，仍为`snapshot_as_of=2026-10-24`；下载1,371,895字节的`production_manifest.json`并重算SHA256，与current记录的`118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`完全相符，旧指针完整但未更新。
- Gold最新manifest `as_of_date=2026-10-30`，`primary_keys_unique=true`、`ohlc_valid=true`；Task01与Gold日期不同，不能以Gold成功代替Task01生产发布。
- PR #123只读审计的归档预检原允许tar符号链接/硬链接，已在草稿分支修复为仅文件/目录：commit `3698446797890be850c6a8258b3028126bf5837e`；恶意symlink/hardlink执行回归测试commit `aabfe222fc64416d068c3f1650f0759b75808ab8`。新head CI待确认，**未合并**。
- 源Task01 run `40972437556` 当前artifact列表仅有validation-report，先前记录的同run checkpoint `12960014331`未返回；Task01有“未发布时只保留最新checkpoint”的清理逻辑，需先查可用checkpoint与同run stage是否匹配。严禁跨run拼接、无审计续传、擅自启用350分钟自动恢复。
- 决策顺序：新head CI → 同run checkpoint及stage来源 → 只读SHA审计 → 受控生产发布及current/manifest回读 → 回滚验收 → 全仓调度验收。上述条件未满足，不关Issue #128、不停专项GPT任务。
