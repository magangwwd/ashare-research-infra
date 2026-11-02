# A股主板数据查询记忆 / VERIFIED SOURCE REGISTER (2026-10-20)

**首要任务：筛选可信、可实现、经样本外验证的A股主板3–5交易日短线策略。只允许研究/模拟；严禁Baostock作为15分钟数据源。**

## 唯一可信的数据查询顺序

1. Google Drive `project/down_data` 根目录：[folder](https://drive.google.com/drive/folders/19UP_n5McsBOC5-0NCcEHqCHMIItrHjIL)。先读 `DATABASE_STATUS.md`（[文件](https://drive.google.com/file/d/1OQYMcCg4P4e911Ou6VuyVQxlQ81fTApo/view)），再以**实际文件**、manifest、SHA、PIT与日期验证替代旧文档缓存。
2. **Tushare Gold 主板日线：** `down_data/data/gold/main_board`（[实际文件夹](https://drive.google.com/drive/folders/1vCz7H5aeePjFapg7xLPu44x-PUSQOX_8)）。有 `manifest.json`、`instruments.parquet`、`daily_prices/year=YYYY/month=MM/data.parquet`、`daily_metrics`、`adjustment_factors`、`daily_limits`、`suspension_events`、`name_history.parquet`。2026-10-20读取实际manifest：`source_vendor=TUSHARE`, `as_of_date=2026-10-19`, `validation.primary_keys_unique=true`, `validation.ohlc_valid=true`。**manifest新鲜不代表所有分区均已核实，回测须按具体年月与数据范围验证。**
3. **Tushare 15分钟原始分区预期：** `down_data/data/raw/tushare/main_board_15m/year=YYYY/<symbol>.parquet`，目录[1xlbrG-0r_poPAC0SqsH5yECpFO2yfPSe](https://drive.google.com/drive/folders/1xlbrG-0r_poPAC0SqsH5yECpFO2yfPSe)；2026-10-20直查内容**为空**。
4. **Tushare 15分钟Gold预期：** `down_data/data/gold/main_board/kline_15m/year=YYYY/month=MM/data.parquet`，目录[1eYUuBPDrdmT7tFtnbIKoQWtArSYW8ELi](https://drive.google.com/drive/folders/1eYUuBPDrdmT7tFtnbIKoQWtArSYW8ELi)；2026-10-20直查**为空**。
5. **15分钟更新记录预期：** `down_data/data/manifests/tushare/main_board_15m/last_run_summary.json`，目录[1clJB3mnTh8xLlZdCW4TZew7T-RuHEE1k](https://drive.google.com/drive/folders/1clJB3mnTh8xLlZdCW4TZew7T-RuHEE1k)；2026-10-20直查**为空**。不能声称已成功下载。
6. `down_data/backfill_main_board_15m.py`：[Tushare下载器原文件](https://drive.google.com/file/d/1ifV4s6cEEucrKVrLHoug5_egSF_0nnd8/view)。使用Tushare Pro，读取历史主板股票池，按股票年份分块，出现接近8000行的请求必须分割，永不以空表覆盖成功分区。使用手册：[A股主板15分钟K线_下载与更新](https://drive.google.com/file/d/1bEx21fl_d8fHfjY7xWW1Wn9t2zkDizhg/view)。是否具备**Tushare历史15m独立权限、TUSHARE_TOKEN、GitHub runner的合法授权路径**，须实际试跑确认；文档本身不是下载成功证据。
7. 特别禁止混淆：另一目录[独立15分钟实验](https://drive.google.com/drive/folders/1U3XYjnVkg6bDB4GIjBShxbvhnCjrArHj)共有2023–2026月度Parquet分区；其真实`provider_manifest.json`解码显示`version=BAOSTOCK_600351_15M_PROBE_V1`, `symbol=600351.SH`, `rows=11728`, `partitions=37`，**不是Tushare、也不是主板全股票分钟数据**。禁止混用或声称Tushare覆盖。
8. Tushare Gold历史研究既有证据：V3 2024-01-01—2026-10-16，源manifest SHA256 `56003f3eab3ea4ad405ac692e11cb7ff42bb0b700d177903311b8829cd24ded6`，已归档V3 [comparison.json](https://drive.google.com/file/d/1-vQUcBr6jUB0k-fknH7dGa3mv5s4ETNy/view)。固定历史样本与后续数据要严格区分，输入SHA变化须可追溯。

## 研究与回测要求（不能省略）

- 样本：真实历史上市/退市主板股票，PIT名称、停牌、涨跌停；复权/股息送转要单独核验。已观察的131笔V2-LOOSE交易与唯一Baostock样本股票不重叠，**而Tushare历史15m样本尚未取得**。
- 先只提出*入场前已知*的少量信号变动（如突破前20日高点幅度/放量强度/次日跳空），冻结实验规则与参数；使用与V1相同的现金、单票、手续费和A股T+1执行器重新完整模拟；不可事后剔除亏损单来“改善”收益。
- 任何日线触价止盈、止损须显式标记为`DAILY_OHLC_ASSUMPTION`，不得标记真实成交。15m只可用于检验是否触价与OHLC先后限制，未验证挂单成交量/队列则仍不算真实成交。
- 以5万元本金、每笔约4万元（3–5万元）、最多持有1只、通常持有3–5个交易日、最大回撤<=25%、压力测试和滚动前瞻样本外为研究门槛。无正收益+风险门槛双达标不进入实盘，未经独立成交验证也不进入实盘。
- **自动运行边界**：ChatGPT每日08:00主动检查推进，可失败后报告；GitHub没有被这里默认为已启用持续分钟线下载。不得恢复旧Baostock工作流、不得篡改冻结V1/V2/V3或自动合并Draft PR#76。

## 日常核查 / 简单命令（只读）

在已授权的Google Drive镜像/有Tushare权限的计算环境：
```bash
rclone lsf gdrive:down_data/data/gold/main_board/daily_prices/year=2026 --dirs-only
rclone lsf gdrive:down_data/data/gold/main_board/kline_15m --recursive --files-only
rclone lsf gdrive:down_data/data/raw/tushare/main_board_15m --recursive --files-only
rclone lsf gdrive:down_data/data/manifests/tushare/main_board_15m --recursive --files-only
```
若目录为空，记录为`NO_VERIFIED_TUSHARE_MINUTE_DATA`，不是策略回测已完成，也不能用Baostock临时替代。若有数据，才进入sha/160分钟? 每交易日16根15分钟K线/与未复权日收盘价交叉核对。
