# 离线多角色投研 V2：无LLM、无模型API、基于已存数据

## 目标与角色定义

复用 Google Drive 行情、财务与持仓状态数据、GitHub Actions 计算以及当前聊天窗口的人工联网检索。
不接入 LangGraph、大模型推理服务、收费金融 API 或券商交易接口。
“Agent”在自动端是独立、确定性、可复核的规则角色，不宣称具备大模型思考能力。

技术 Agent：使用 Gold 复权 K 线计算 MA5/MA20/MA60、20日收益、前20日高点突破、ATR14 与前20日量比。
财务 Agent：按 end_date 和 ann_date 选出研究日以前已披露指标，评估 ROE、净利率、资产负债率、流动比率及数据时效。
消息 Agent：只接纳来自聊天窗口整理并归档的、带披露与采集日期的真实链接。缺失不能解释为无风险。
多方 Agent：汇总可溯源的正向指标，不预测盈利概率。
空方 Agent：汇总反证、负向指标和缺失项。
风控 Agent：披露间隔、财务时效、波动性、成交量异常及未知风险。
研究经理 Agent：标记证据完成度，仅给出研究观察状态，不产生交易指令。

## 已核实输入路径（rclone 的 gdrive: 根是用户现有 project 文件夹）

Gold：gdrive:down_data/data/gold/main_board/daily_prices/year=YYYY/month=MM/data.parquet
复权：gdrive:down_data/data/gold/main_board/adjustment_factors/year=YYYY/month=MM/data.parquet
行情验收：gdrive:down_data/data/gold/main_board/manifest.json
原始A池：gdrive:stock_state_strategy/output/state_definition_YYYYMMDD/a_strategy_pool.csv
财务：gdrive:stock/financial_data/fina_indicator_q.csv
可选新闻证据：gdrive:stock_state_strategy/research_evidence/YYYYMMDD.json
输出：gdrive:stock_state_strategy/output/ai_multiagent_offline_YYYYMMDD/

行情使用六个月已存档 K 线并排除研究日之后的记录。财务必须有公告日，
晚于研究日的公告不可进入结果。旧 income.csv 等无逐行 ann_date 的文件不得在
历史时点直接参与判断。若有更新且经过验收的财务 Clean 数据，后续才可扩展适配。

## 运行方式

.github/workflows/ai-research-data-only.yml 支持手工发起，也会在旧的
astock-quant - Drive-first strategy 夜间 schedule 成功之后执行。
原 Gold 刷新、四阶段状态及冻结 LOCK_MARKUP_SINGLE_PAPER_V1 均保持不变。
新的计算在运行时使用既有 rank_a_watchlist.py 重建当日辅助排名；
只允许观察、结构化风险说明与纸面评测。
结果存储 report.json/report.md/report.html 并执行 rclone check。
不使用第三方行情更新接口或任何大模型服务。

GitHub Actions 通过 Google Drive 凭据下载与上传既存文件，当然仍有正常网络传输。
“不调用API”是指不调用额外的行情、财务、新闻数据 API 或模型推理 API，
不是彻底断网后仍能从 Drive 读取数据。

## 本对话窗口的联网检索接力

脚本无法主动唤醒当前 ChatGPT 对话，也无法自动读取它的 web 检索结果。
使用者可在当前对话搜索公告、官方披露与媒体新闻；按来源和日期核验后，
显式把事实整理为下列 JSON 列表，并经授权放到 Drive 的研究证据目录。

示例格式（内容只是占位示例，绝不是真实公告）：

    [
      {
        "symbol": "600351.SH",
        "published_at": "2026-10-09",
        "captured_at": "2026-10-09",
        "source_url": "https://example.com/original",
        "headline": "原始公告标题",
        "evidence_type": "announcement"
      }
    ]

如果 2026-10-12 才查到 2026-10-09 的旧公告，则如实记录
captured_at=2026-10-12。它能用于当前阅读，但不能伪装为 10月9日
已经采集的历史消息信号。历史 PIT 证据要求两个日期都不晚于 as_of。

## 验收与约束

- 原策略状态保持 PAPER_ONLY_NO_ENTRY_AUTHORIZATION。
- 缺少 Gold 或 Gold 校验失败、缺少候选 A 池、远程上传未验收均需失败。
- 真实技术指标由 Gold 日 K 线计算，不复制原研究分冒充技术打分。
- 财务字段须按公告日截断，来源与滞后期明确展示。
- 未抓取到新闻不代表无利空，不伪造任何新闻。
- 结果既不改变 V1 参数，也不接入券商、建仓或执行策略。
