# A 股 AI 辅助投研 V0.1（独立影子模块）

本目录学习 TradingAgents 的角色分工思想，但不复制其决策/交易执行代码。
正式冻结策略 LOCK_MARKUP_SINGLE_PAPER_V1 保持不变。

## 状态和边界

- 策略编号：AI_RESEARCH_SHADOW_V1；永远 PAPER_ONLY_NO_ENTRY_AUTHORIZATION。
- 输入：stock_state_strategy 原研究优先级输出 watchlist_top20.csv + ranking_summary.json。
- 默认：RULE_ONLY_NO_API，仅使用现有状态数据的规则化研究报告。**默认不是 LLM 结果**。
- 只有显式 --enable-llm 且配置 API 变量时才触发远程 LLM，请关注供应商计费。
- 最多分析5只，输出 report.json、report.md、report.html；不会写入 GitHub 数据或连接券商。
- 财务与新闻数据缺失时明确写 NO_POINT_IN_TIME_DATA，绝不猜测信息。
- 技术 Agent 只展示原状态事实；没有OHLC序列，不冒充独立K线形态验证。
- LLM 文本被标记为 UNVERIFIED_NARRATIVE_REVIEW_REQUIRED，不用于覆盖原 V1 信号。
- 所有线上输入必须来自可核验交易日的 Drive 候选池；测试数据仅在临时目录生成。

## 本地运行（需事先从 Drive 恢复相同研究日期的两个文件）

    python -m unittest discover -s ai_research/tests -v

    python -m ai_research.run_daily \
      --watchlist /tmp/watchlist_top20.csv \
      --summary /tmp/ranking_summary.json \
      --as-of 2026-10-09 \
      --top 5 \
      --output-dir /tmp/ai_research_20261009

以上 2026-10-09 是历史示例，不是“今日行情”。

## 需要 API 时（明确由用户选择）

通过私密环境变量配置：
- AI_RESEARCH_API_KEY：服务商 API 密钥
- AI_RESEARCH_BASE_URL：HTTPS OpenAI-compatible chat API 根路径，例如 https://example.invalid/v1
- AI_RESEARCH_MODEL：模型名

然后在上述命令结尾加入 --enable-llm。每只股票进行1次同步请求，
设置输出 tokens 上限 350；没有预算上限和价格估算机制，必须自行评估
服务商的调用费用。请求失败时整个任务失败，不发布部分结果。
禁止把密钥、测试结果 CSV、模型报告等提交进仓库。

## 可选的独立 PIT 证据文件

可增加 --evidence-json，内容格式如下（示意数据，非真实消息）：

    {
      "600351.SH": {
        "fundamental": [
          {"published_at":"2026-09-30","source":"示例财务披露","text":"待核实的财务摘要"}
        ],
        "news": [
          {"published_at":"2026-10-01","source":"示例公告","text":"待核实的公告摘要"}
        ]
      }
    }

系统只纳入 published_at 日期不晚于研究 as_of 的带来源记录。
本版本采用日级截断，只适用于研究日结束后的快照，不支持盘中事件级回放。
数据必须由其他已验证流程采集；本模块不会联网抓取或自动填补缺失。
用户输入证据也需要人工核对原始公告。下一阶段应增加 capture_at、
来源URL、原文hash、公告精确时间、财务口径与复核状态。

## GitHub Actions

.github/workflows/ai-research-shadow-manual.yml **仅手动**：
输入交易日、分析股票数量以及是否启用 LLM；从 gdrive:
的现有研究优先级文件恢复输入，测试通过才运行，完成后把结果上传到：

    gdrive:stock_state_strategy/output/ai_research_YYYYMMDD/

上传后执行 rclone check；不保存到 GitHub Actions artifact，也不写 Git。
需要仓库已有 GDRIVE_RCLONE_CONFIG_B64；仅 LLM 模式额外使用
AI_RESEARCH_API_KEY、AI_RESEARCH_BASE_URL 和 AI_RESEARCH_MODEL Secrets。

本次没有增加自动定时 AI 任务。先用历史快照完成真实 Drive 读取、
报告生成及发布验证，再决定是否在原 23:50 Gold/状态任务成功以后串行触发。

## 验收

1. 离线测试通过且没有网络请求。
2. 读取快照的日期、候选池来源和纸面交易标记都严格匹配。
3. 缺失财报和新闻明确标记，不伪造看多/看空消息。
4. 研究结果不更改既有 V1 规则、数据、状态池、订单及风控。
5. 真实运行成功后，另行记录 GitHub Actions run ID、Drive 文件和日期。
