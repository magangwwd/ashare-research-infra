# ETF GitHub Actions 启动与验收记录

原则：仅记录已证实的结果；提交成功不等于运行成功；运行成功不等于数据质量合格。不得存储凭据。时间均为北京时间（UTC+8）。

## 已验证运行
| 日期 | 操作路径 | GitHub run / job | 检出 SHA | 结果 | 证据与限制 |
|---|---|---|---|---|---|
| 2026-10-19 18:01 | workflow_dispatch；具体发起工具未知 | 39234376712 / 117999779860 | f6d1d977 | 成功 | 原始日志、Artifact；旧版无质量审计 |
| 2026-10-19 18:57 | GitHub 插件 rerun_workflow_job(117999779860) | 39234376712 / 118022035934 | 27d04977 | 成功 | 日志和 Drive 四个文件上传；旧版步骤无审计 |
| 2026-10-19 19:01 | GitHub 插件 rerun_workflow_job(118022035934) | 39234376712 / 118023342723 | c44c936e | 成功，质量有警告 | 下载脚本内调用 audit_data.py；日志提示缺失交易日、复权/分红未验证。Artifact 成功，Drive 日志仅确认 prices/equity_curve/trades/summary 四文件，不能宣称质量报告已上传 Drive |

## 新增验证运行（2026-10-19）


| 2026-10-19 19:23–19:25 | GitHub 插件 rerun_workflow_job(118023342723)；复用既有运行流程并检出最新分支代码 | 39234376712 / 118032110611 | 54f0148a | 运行成功；数据仍需复核，策略不合格 | Tushare fund_adj 与 SSE trade_cal 均成功；21,398 行、8 只 ETF；数据质量报告和 7 类文件已上传 Drive。4 个缺失交易日；CAGR 2.91%，最大回撤 -34.38%，未达到回撤≤20%目标。复权因子已逐行匹配，但其分红再投/总收益语义仍需独立验证。 |

## 未验证的触发尝试
| 日期 | 方法 | 提交 | 结果与教训 |
|---|---|---|---|
| 2026-10-19 | 为工作流增加 push + 指定路径触发 | b7a89bb | 工作流配置已提交；未取得运行编号 |
| 2026-10-19 | 新建 .run_data_audit | e90eb442 | 提交成功；查询接口仅返回 PR 事件相关运行，空结果不能证明 push 未执行 |
| 2026-10-19 | 修改 .run_data_audit 重试 | 27d04977 | 提交成功；仍无可验证的新增 run ID |

## 已验证的快速启动方式
1. 在分支 feature/etf-single-rotation-v1 提交并核对新代码 SHA。
2. 如果要复用已有工作流，使用 GitHub 插件 rerun_workflow_job 对最近已完成的 job 重跑。此方法沿用原工作流定义，但 checkout 明确指向 feature 分支，因此可取最新代码；务必核对日志中的 `git log -1 --format=%H`。
3. 获取最新 job ID；轮询 fetch_workflow_run_jobs，直至 completed；核对各步骤 conclusion 和 fetch_workflow_job_logs。
4. 查验审计输出、Artifact 文件清单及 Google Drive 上传日志。缺失日期、异常跳变、复权/分红状态需独立评估。
5. 每次追加本表：时间、操作方法、分支/提交、run ID、job ID、实际状态、日志/产物、失败原因、下次改进。

## 待改进
- 当前连接不提供 workflow_dispatch 直接创建新运行，也不提供完整运行列表；不应把 push 提交当作成功触发证据。
- 正式接入新工作流后，优先使用明确的 workflow_dispatch + 新 run ID；在此之前可用 rerun_workflow_job，但必须辨别旧工作流定义。
- 修复 Google Drive 上传脚本以包含 data_quality.csv 与 data_quality_summary.json；本次日志仅确认它们在运行器本地生成并包含于 results/ Artifact 路径。
- 数据质量报告显示部分 ETF 缺失交易日，且复权/分红状态为 NOT_VERIFIED；回测结果不能据此认定策略可用。
- 不启用定时触发，不进行真实交易。


## 2026-10-19 数据链路改进
- `download_prices.py`：下载 `fund_daily` 原始行情、按年分段获取 `fund_adj`（接口单次最多 2000 行），用复权因子对价格缩放，并下载 SSE 开市日历；若复权因子缺失或接口失败则拒绝回测。
- `audit_data.py`：以 SSE 交易日历检查每只 ETF 上市后的缺失日期，输出 `data_quality.csv` 与 `data_quality_summary.json`。
- `upload_drive.py`：将行情、交易日历、数据质量报告、净值曲线、交易记录和摘要共 7 类文件上传到指定 Drive 文件夹。
- `backtest.py`：止损退出当日不再重新买入，等待下一次月度再平衡；但最大回撤仍明显超过目标。
- 本次审计发现的缺失日期：159915.SZ 2021-02-08；510500.SH 2015-04-13、2015-04-14；512100.SH 2022-09-02；512690.SH 2021-05-14（共 5 个缺失 ETF-交易日记录，分布于 4 只 ETF）。在确认是否停牌/数据漏抓前，状态保持 REVIEW_REQUIRED。
- 当前复权数据只能标记为“复权因子已匹配”，不可等同于已独立证明完整总收益序列；需要核对基金分红事件及独立基准后，才可用于正式策略验收。
