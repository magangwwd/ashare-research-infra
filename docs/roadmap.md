# Task 01 路线图与运行流程

本文整理已经核验的工作和接续顺序。第 15 次数据任务已完成第二次 checkpoint 续传，第 18 次 Drive 发布已完成两次真实版本发布；当前正式 Drive 快照日期为 **2026-10-04**。这是阶段验收记录，不代表全量历史或 Task 01 最终验收完成。

当前已经走通：**Tushare → Raw → Clean → Parquet → Validate → Artifact → Drive staging/远程校验 → 正式版本切换**。手动发布已完成两次真实验收；常规数据 job 的自动 Drive 发布代码已接通，待下一次正常运行取得真实证据。

## 已完成内容

| 阶段 | 已交付内容 | 验收状态与限制 |
| --- | --- | --- |
| 仓库与配置 | 最小模块、README、YAML、环境 Secrets、依赖、测试 | 已完成；未建立因子/模型/回测目录 |
| 数据源接入 | Tushare client、重试、Capability Audit、接口分级 | 五个 Critical 可用；扩展接口按权限处理，失败记录 Gap |
| 基础资料 | stock_basic、trade_cal、Security Master、稳定 security_id、明确板块元数据 | 已验证；严格每日研究股票池留 Task 02 |
| 存储与恢复 | Raw/Clean、固定 Schema、Date/dtype、去重、ZSTD Parquet、Hive 分区、compact、Manifest | 已实现；扫描实际文件、断点续传和失败保护已有实测 |
| 核心下载与验收 | 历史分批、最近窗口回补、缺失股票补抓、主键/非空/覆盖率验收 | 已存数据通过；历史全量仍未补齐 |
| 扩展数据 | 财务、行业、指数、市场状态、Optional 白名单的获取和落库 | 部分接口和数据已可用；完整历史、主行业体系仍待验收 |
| Actions | 单一 Workflow、20:00 北京时间 cron、手动触发、恢复 checkpoint、Smoke、验收和 Artifact | 定时维护和真实交易日回放通过；连续交易日运行待观察，GitHub 可能延迟调度 |
| Drive | OAuth、独立 staging、远程 SHA256、发布 Manifest、最后切换 current.json | 两次真实发布与新旧版本切换已通过；自动发布代码已接通但待正常数据任务实测，远程恢复和版本保留仍待完成 |

本地 77 项普通测试通过，另有真实数据回放和恢复演练证据。真实首次 Drive 发布已成功；上传中断、漏文件、checksum 错误、远程读取及指针切换失败保护由离线故障测试验证。

## 当前数据进度

| 项目 | 已核验结果 |
| --- | --- |
| 快照日期 | 2026-10-04；最近交易日仍为 2026-09-30 |
| 最近交易日核心覆盖率 | daily、adj_factor、daily_basic 均为 100%，理论池 5,559 只 |
| 已存核心历史交易日 | 三类均为 65 日；包含早期边界资料，不能理解为连续最近 65 日 |
| 尚缺目标交易日 | daily 8,320；adj_factor 8,671；daily_basic 8,320 |
| Drive 正式快照 | 当前正式版本 482 个数据/元信息文件，共 109,617,924 字节；历史进度仍未完成 |
| 财务/行业等完整历史 | 尚未验收；extensions_history_complete 为 null |
| Drive 空间 | 上传前账号总配额 400 GiB、可用约 393.9 GiB；与其他 Google 服务共享 |

daily/basic 另有观察边界前 351 个交易日单独记录为提供者覆盖不足，不混入上述待下载日数。起止日期、请求回执或游标不能证明历史区间完整。

证据：[数据任务 #15（含第二次续传）](https://github.com/magangwwd/ashare-research-infra/actions/runs/37133945515)、[Drive 发布 #18（含第二次正式发布）](https://github.com/magangwwd/ashare-research-infra/actions/runs/37160747646)、[详细验收记录](acceptance-record.md)。

## 当前运行流程

```mermaid
flowchart TD
    A["Actions 定时或手动触发"] --> B["测试 · 恢复 checkpoint · 真实 Smoke"]
    B --> C["Capability Audit · 读取交易日历"]
    C --> D["交易日更新 / 非交易日维护"]
    D --> E["Critical 最新数据 → Required → Optional → 历史缺口"]
    E --> F["Raw → 固定 Schema Clean → 分区 Parquet"]
    F --> G{"Critical 总验收"}
    G -->|失败| H["FAILED · 旧正式版本不变 · Gap / 恢复状态"]
    G -->|通过| I["本地正式版本 · Artifact · 验收证据"]
    I --> J["自动 Drive staging · 重新验收"]
    J --> K{"远程文件清单 / 大小 / SHA256"}
    K -->|失败| L["旧正式指针不变"]
    K -->|通过| M["发布 Manifest · 移入 versions · 切换 current.json"]
```

Critical 下载或质量验收失败必须让数据任务失败。Required/Optional 暂时失败记录 Gap，不阻断合格的核心数据。尚未下载的历史范围不阻止已存合格快照发布，但历史未完成标记必须保留。

手动 Drive 发布入口继续存在，可读取 checkpoint 中最后一个正式版本并独立重发。从 commit `a208de341b587bab03b194389c752e423763d62e` 起，常规 `task01` job 在本地验收和验收 Artifact 成功后自动执行同一套 Drive staging、远程校验与指针提交逻辑；发布失败会使任务标红，但 recovery checkpoint 仍通过 `always()` 保存。该自动链已落代码，仍需一次正常数据任务实测后才能记为运行验收完成。

## 存放位置与读取方式

| 位置 | 用途 | 生命周期 |
| --- | --- | --- |
| GitHub 仓库 | 代码、配置、测试、说明 | 不存数据和业务凭据 |
| Actions Runner | 本次下载、清洗、验收、上传的临时工作目录 | GitHub 托管 Runner 在任务结束后销毁 |
| task01-validated-data Artifact | 经验收的正式数据包 | 保留 30 天，占 GitHub Artifact 存储 |
| task01-checkpoint Artifact | 上一正式版本及未完成状态，下次运行恢复 | 保留 90 天，占 GitHub Artifact 存储 |
| Drive versions | 经远程核对的不可变快照 | 当前不会自动删除旧版本 |
| Drive current.json | 唯一正式读取入口，指向已验收版本和发布清单 | 最后切换；不以文件夹创建时间判断正式版本 |

上传 Drive 不会自动清理 GitHub Artifact；现阶段继续保留检查点，避免恢复链中断。

在 [Drive 根目录](https://drive.google.com/drive/folders/1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue) 查看 current.json。当前 [正式快照目录](https://drive.google.com/drive/folders/1O6t2EE1V1rmRCQYgzdbpiD-TosgHfsN0) 已切换为第二次版本；首次版本仍保留在 versions 中。消费者应验证指针和 Manifest，不读取 staging。

## 后续路线图

历史补齐和每日更新继续并行运行；以下是 Drive 接续开发和最终验收的顺序，尚未开始的新功能不能记为完成。

1. **第二次正式发布：已完成。** 第二次真实发布已验证 staging 期间旧指针不变、远程校验后才切换；新版本可读、previous_version 正确，首次版本目录和 Manifest 仍可恢复，staging 最终为空。
2. **每日验收后自动上传：代码已接通，待真实运行验收。** 常规 `task01` job 已衔接本地验收与 Drive 发布；远程验证成功才提交指针，上传失败会标红任务而 `always()` checkpoint 仍保存。下一次正常数据任务需确认该链无人干预成功。
3. **建立 Drive 恢复链。** Runner 能从经验证的远程版本恢复数据和必要下载状态；实测 GitHub checkpoint 不可用时仍可继续，不重新下载完整历史。当前 Drive 快照上传不等于已具备此恢复能力。
4. **制定并实现保留策略。** 明确 Drive 版本数、行情 Raw 保留、财务 Raw 长期保留和 Artifact 配额；恢复链验证成功后再清理冗余产物。当前发布每次上传完整快照，远程增量/续传尚未实现，随历史增长再优化。
5. **持续补齐与审计历史。** Critical 当天优先，Required/Optional 按权限更新，再修复历史缺口；核对实际 Parquet、财务修订、行业/指数覆盖，确定主行业体系。无法获得的范围保留明确原因，不用完成标签掩盖缺口。
6. **执行 Task 01 最终验收。** 联合检查历史范围、Critical 质量、连续交易日和维护运行、历史修订、断点恢复、Drive 发布及失败保护，形成完整验收记录。
7. **进入 Task 02。** 从经验证的 Clean 开始，再建立 curated/factors，处理严格股票池、每日状态、动态复权和因子研究。Task 01 不提前实现这些功能。

操作入口：Actions → Task 01 data infrastructure → Run workflow；默认两项都不勾选执行数据更新，仅勾选 publish_drive_snapshot 发布已验收快照，仅勾选 verify_drive_auth 检查授权。具体规则见 [Drive 发布说明](drive-phase2.md)。
