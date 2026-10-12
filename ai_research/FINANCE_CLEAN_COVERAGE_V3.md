# Finance Clean Bridge V3：已验收但非全覆盖，无模型API

## 数据事实

原系统读取 gdrive:stock/financial_data/fina_indicator_q.csv，指标时点较旧。
新源 Ashare_Quant/current.json 指向版本：
2026-10-05T215756.864024_0000-b88e66ac，snapshot_as_of=2026-10-06。
原数据 acceptance.json passed=true，但历史全量标记 false。

GitHub Actions 的 gdrive: 遥端不能读取 Ashare_Quant 所在的共享根目录，
因此经 Drive connector 复制经授权的版本快照到：
gdrive:stock_state_strategy/finance_clean_stage/

该目录保留独立接受文件及各次财务原始 Parquet。
这是一份手工复制、冻结版本的输入，不会自动镜像上游新版本。

## 实测半年度覆盖率

五只股票：600351.SH，002107.SZ，600641.SH，002568.SZ，002296.SZ。

2026-06-30：
income 42行，候选五只命中0；
balancesheet 42行，命中0；
cashflow 41行，命中0；
fina_indicator 42行，命中0。

2025-06-30：
income 37行，候选五只命中0；
balancesheet 16行，候选五只命中0；
cashflow 36行，候选五只命中0；
fina_indicator 分区不存在。

因此不能据此把前五只股票的基本面更新至半年报，
不能把已验收理解为全市场完整覆盖。

## 安全接入方式

ai_research/financial_clean_bridge.py 验证版本、验收文件、时点、披露日期，
对各证券检查实际覆盖。只有找到有效记录才采用 Clean 指标。
若没有，明确输出 LEGACY_FALLBACK_CLEAN_GAP 并使用旧带公告日CSV；
如使用 --require-all-clean，则覆盖不足直接拒绝发布。

报告保存 financial_clean_coverage.json，含实际财务覆盖、来源版本、
时效和缺失分区；新输出到 ai_multiagent_offline_v3_YYYYMMDD，
不覆盖已有 V2 报告与人工七角色复核文件。
保持 PAPER_ONLY_NO_ENTRY_AUTHORIZATION，无 LLM/API/券商交易。

## 未解决问题

上游 Clean 仅部分采样，需要完整财务补采才会覆盖前五只股票；
复制到 finance_clean_stage 后不会自动更新，源快照超过14天标风险。
2026半年报数字虽已用公开公告人工复核并单独留存，
但不能冒充此 Clean 中已存在的机器数据，也不能将10月12日才采集到的
新闻称为10月9日已采集的历史信号。
