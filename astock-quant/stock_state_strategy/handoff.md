# stock_state_strategy 最终交接说明

2026-09-12归档更新：本文中的 `output/post_delete_forward_20260818` 和 `output/post_migration_forward_20260818` 已移至工作区 `archive/2026-09-12/migration_checks/`。这些是迁移复核证据；活动历史快照 `output/forward_20260818` 和内部历史依赖保持原位。当前维护范围见README。

更新时间：2026-08-24（Asia/Shanghai）

## 当前结论

`demo01` 与 `demo02` 的内容迁移、配置切换、删除前后复核已经全部完成。

- 合并项目：`G:\我的云端硬盘\project\stock_state_strategy`
- 原 `demo01` 目录：已从原路径移除并送入回收站。
- 原 `demo02` 目录：已从原路径移除并送入回收站。
- 合并项目已在两个原目录不存在的情况下独立通过测试和端到端预测。
- 策略仍为纸面评测：`PAPER_ONLY_NO_ENTRY_AUTHORIZATION`，不授权实盘下单。

## 迁移结果

### 源码与文档

- 两个项目的 37 个顶层 Python 源码及测试文件已合并到项目根目录，无模块重名冲突。
- 原项目顶层文件快照：
  - `archive/source_snapshots/demo01`：20 个文件，477,921 字节。
  - `archive/source_snapshots/demo02`：33 个文件，330,343 字节。
- 删除前逐文件 SHA256 对照结果：两个目录的顶层文件均与对应快照完全一致，差异数为 0。
- 历史研究文档已归档到 `docs/`，映射见 `MERGE_MANIFEST.json`。

### 数据与历史输出

- `artifacts/legacy/demo01_output`：76 个文件，165,103,742 字节。
- `artifacts/legacy/demo02_data`：148 个文件，198,544,272 字节。
- `artifacts/legacy/demo02_output`：76 个文件，270,822,335 字节。

活动配置已全部改为上述内部路径；统一主板行情继续使用项目级共享数据：`../down_data/data/gold/main_board`。

### 必须保留的策略文件

当前策略与归档原文件逐字节一致：

```text
文件：LOCK_TO_MARKUP_PAPER_STRATEGY.md
归档：archive/source_snapshots/demo02/LOCK_TO_MARKUP_PAPER_STRATEGY.md
SHA256：7ff70fff76c3f2bf0bbc61288baf94b41ac9c402b7c622efd90a12b82b7129af
```

## 最终验证记录

### 删除前

- 活动 Python、JSON、README、合并清单对 `demo01`/`demo02` 的运行时引用：0。
- JSON 配置和清单：全部可解析。
- 自动化测试：40 项全部通过。
- 截至 2026-08-18 的端到端 V1 冒烟：成功。

### 删除后

- `G:\我的云端硬盘\project\demo01`：不存在。
- `G:\我的云端硬盘\project\demo02`：不存在。
- 自动化测试：40 项全部通过，无失败。
- 端到端 V1 冒烟：成功，结果与删除前一致。

端到端基准结果：

| 项目 | 结果 |
|---|---:|
| 上市股票池 | 3,193 |
| A级池 | 94 |
| B级池 | 291 |
| 删除池 | 2,808 |
| V1候选数 | 1 |
| 候选代码 | `600400.SH` |

删除后输出位于：

`output/post_delete_forward_20260818/lock_to_markup_v1`

其中包括 `summary.json`、`report.md`、`v1_signal_candidates.csv`、状态明细和A级状态列表。

## 后续使用入口

- 总说明：`README.md`
- 合并与验证清单：`MERGE_MANIFEST.json`
- 正式策略：`LOCK_TO_MARKUP_PAPER_STRATEGY.md`
- 策略机器配置：`lock_to_markup_paper_strategy.json`
- 统一运行入口：`run_pipeline.py`
- 入场与冻结排序配置：`config.json`
- 持仓状态配置：`configs/ownership_state.json`
- 数据重建配置：`configs/ownership_rebuild.json`
- 2026-08-18前向配置：`configs/forward_20260818.json`
- 历史研究文档：`docs/`

常用验证命令：

```powershell
Set-Location -LiteralPath 'G:\我的云端硬盘\project\stock_state_strategy'
python run_pipeline.py tests
```

重新生成当前 V1 基准：

```powershell
python current_lock_markup_snapshot.py `
  --as-of 2026-08-18 `
  --state-snapshot artifacts\legacy\demo02_output\forward_20260818\state_definition_pool\current_snapshot.parquet `
  --transition-snapshot artifacts\legacy\demo02_output\forward_20260818\state_transition\current_state_transitions.parquet `
  --output-dir output\post_delete_forward_20260818\lock_to_markup_v1
```

## 恢复说明

- 原项目的有效顶层文件可从 `archive/source_snapshots/` 恢复。
- 原数据及历史输出可从 `artifacts/legacy/` 恢复。
- 原目录已送入 Windows 回收站，在回收站清空前还可整体恢复。
- Python 与 pytest 缓存不属于需要保留的业务资产。

迁移已经闭环，后续工作应仅在 `stock_state_strategy` 内继续，不应重新创建对 `demo01` 或 `demo02` 的运行依赖。

## 2026-08-24新增：香山股份持仓监控

`live_position_monitor/` 已实现香山股份 `002870.SZ`、买入价49元的东方财富/腾讯/新浪多源实时行情与退出邮件告警。初始化冻结ATR止损比例为6.1120%，止损价约46.005元；程序不连接券商。买入日期因用户未明确提供，暂按2026-08-24配置，正式运行前需核对。SMTP收件人与授权码尚未提供，须按该目录README设置环境变量并通过测试邮件后，才能实际发信。
