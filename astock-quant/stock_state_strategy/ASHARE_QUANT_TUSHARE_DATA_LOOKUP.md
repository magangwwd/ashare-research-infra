# A股主板行情查询记忆：Ashare_Quant 的 Tushare 日线和15分钟数据

> 实际核对：2026-10-20。此文用于后续V4短线策略回测时避免误认空目录。**仅研究；任何回测前都必须复核当期指针与数据哈希**。

## 云盘实际入口

- Ashare_Quant 根目录（与 `down_data` **不同**）：[打开文件夹](https://drive.google.com/drive/folders/1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue)
- 根指针 `Ashare_Quant/current.json`：[打开](https://drive.google.com/file/d/1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB/view)。
- 截至2026-10-20读取到 `version=2026-10-20T024536.666798_0000-39fecbf2`, `snapshot_as_of=2026-10-19`, `validated=true`, `file_count=2233`, `total_bytes=1823517399`。源GitHub Actions run `39356295124`，`manifest_sha256=b0b7b0222e6a2094a039ca3141bf027d0383320c28305141c495d7b45731cb4d`。
- 正式版本清单 `production_manifest.json`：[打开](https://drive.google.com/file/d/1bL0sDMVk8MDSN8QjE3pUTNpUX8Y-mDxn/view)。数据集文件以相对路径和Drive `file_id` 的对应表存在清单的 `files[]`，实际回测应使用**已验证的正式版本**，而不是 `backfill-working-v1/objects` 下不可辨认的哈希名文件。
- 工作区 `backfill-working-v1`：[打开](https://drive.google.com/drive/folders/1R3Y0GdYzthmsSHRg2ufRo-aLdwGfN-Bc)，只做未发布增量和恢复，不能等同正式数据集。

## 已核实数据类型和规模（正式清单）

| 数据表（Clean） | 生产文件数 | 约计文件大小 | 作用 |
|---|---:|---:|---|
| `data/clean/daily` | 40 | 153.7MB | 历史日线OHLCV，选股/回测 |
| `data/clean/daily_basic` | 40 | 304.4MB | 换手、估值等 |
| `data/clean/adj_factor` | 40 | 22.5MB | 复权因子 |
| `data/clean/stk_mins` | 640 | 334.1MB | **Tushare 15分钟行情** |
| `data/clean/stk_limit` | 4 | 3.2MB | 涨跌停 |
| `data/clean/suspend_d` | 40 | ~0.2MB | 停牌 |
| `data/clean/stock_basic`, `security_master`, `trade_cal` | 已存 | — | 历史主板股票池/PIT核对 |

生产 `metadata/acceptance.json`（[打开](https://drive.google.com/file/d/1VcSC5BejDqHb5IkrCy8o0oBmvD2D8_L4/view)）在2026-10-20 02:43 UTC显示通过当前核心数据检查：daily Clean 4,057,283 行、daily_basic 4,057,287 行、adj_factor 4,080,135 行。**但当前验收通过不能被曲解为从1990年开始全历史完整！**

`metadata/manifest/intraday.json`（[打开](https://drive.google.com/file/d/13lVq628ILI-qj4dq635Hw4yyzVZBSqAX/view)）记录 `freq=15min`、`daily_source=stk_mins_15min`，截至2026-10-19当日5,564只证券覆盖率1.0；Clean `stk_mins` 16,578,398 行，Raw 16,895,240 行；历史请求960/35927，完成率**2.672%**，`history_complete=false`。20只样本与日线核对20/20一致仅限被抽样交易日，**不代表所有历史股票/成交都已对账**。

通过生产清单按 `data/clean/stk_mins/` 汇总，当前640个分区文件**全部位于 `year=2026`**（以该版本为准）。因此不能声称2024/2025已提供15分钟回测所需历史覆盖。历史请求目标起点2020-01-01，非已完成起点。

`metadata/history_progress.md`（[打开](https://drive.google.com/file/d/1XBj8nF-qw99lPso_rtlp2h5LaMESvUCn/view)）警告核心历史仍缺大量日期、仅776个目标交易日可见，`critical_history_complete=false` 及 `full_requested_core_range_complete=false`。财务三表Clean已存部分记录，不是财务全历史完整样本。

## 强制回测前检查

1. 每次先读取根 `current.json`，通过 `manifest_file_id` 取对应**正式** `production_manifest.json`，核SHA、版本和 `critical_history_complete` 标记；读取 `metadata/manifest/intraday.json` 与 `metadata/history_progress.json`。
2. 只认来源为**Tushare** 的 `data/clean/stk_mins` 和 `data/clean/daily`，绝不使用Baostock作分钟行情；`down_data/data/gold/main_board/kline_15m` 目前另行查看为空，不能混同。
3. 清单一个真实分区样本 `data/clean/stk_mins/year=2026/month=01/bucket=01/part-000.parquet` 已证实可经Drive取回（文件头尾均为Parquet `PAR1`，文件大小560,224字节，实际SHA256为`1945663e1a2dc82a8eeb690fe9656c76515b12979b1a91356fdaf8668676db75`，与正式清单完全一致），但**尚未用Parquet解码器逐行核算数据**；同月`bucket=00`的旧manifest `file_id` 目前返404，应先验证每个实际需用文件的可读权限，404不可视为验证通过。
4. **A股主板**不是全部5564只证券；须用证券主表、历史上市日期、交易所/板块与退市状态进行历史时点筛选，不能把创业板、科创板等混入。
5. 重放V2-LOOSE共131笔实际模拟成交的股票和日期，逐单核 `ts_code`、`trade_time`、每日16根标准15min bar、分钟源日期、日线OHLC、前复权调整和 `T+1`；先算实际成交交易覆盖率，再有条件地评估滑点和止损，**分钟高低价仍不能证明逐笔一定成交**。
6. 基础数据齐备后，冻结信号时点已知的突破幅度/成交量等候选规则，重新跑**完整资金路径**而不是事后删掉亏损单；做下一阶段尚未研究的前瞻模拟，验收正收益与最大回撤≤25%，不做实盘。
7. 任一生产数据版本、sha256、日期或覆盖率改变必须重新验收，不许把历史成功当当前状态。

**研究环境约束：** 交易本金5万元、单笔预算约4万元、最多持有1只沪深主板股票，3–5交易日，T+1，纸面回测；历史回填未完、分钟卖出成交未核实、策略仍未达标，不得直接上线实盘。

## 相关可点击入口
- [Tushare 15分钟Schema](https://drive.google.com/file/d/1oNIyIUci6T6yX6TBWslqErqShx6pUFyq/view)
- [Tushare分钟数据设计](https://drive.google.com/file/d/190X9z-ZpetWpIiPZvV3ABXahYgpFjK4V/view)
- [历史缺口说明](https://drive.google.com/file/d/1XBj8nF-qw99lPso_rtlp2h5LaMESvUCn/view)
