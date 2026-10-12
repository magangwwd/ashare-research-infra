# A 股离线多角色研究系统（无 LLM / 不调用模型 API）

本目录只读已有 Google Drive Gold K线、财务指标及 A 级研究池。
GitHub Actions 使用现有 Drive 同步凭据读取与回写结果；不新增行情、
财务或新闻抓取 API，不接入任何语言模型或券商交易接口。

正式冻结策略 LOCK_MARKUP_SINGLE_PAPER_V1 保持原状：
PAPER_ONLY_NO_ENTRY_AUTHORIZATION。

## 推荐运行入口

- 核心程序：ai_research/offline_agents.py
- 定时/手动工作流：.github/workflows/ai-research-data-only.yml
- 离线角色、时点与路径说明：ai_research/OFFLINE_MULTIROLE_GUIDE.md
- 本窗口手工研究提示词：ai_research/PROMPT_PLAYBOOK.md
- 旧版 run_daily.py 仅保留零 API 的历史报告兼容功能，不能启用 LLM。

新的 Actions 流程仅在旧的 stock strategy 夜间 Gold/状态作业
成功完成后触发（或手动发起）。每次先通过测试，之后读取 Gold manifest、
研究日 A 池、K 线和日期可追溯的财务指标，生成 report.json、report.md、
report.html，再写回 gdrive:stock_state_strategy/output/ai_multiagent_offline_YYYYMMDD
并运行 rclone check。缺少可信数据时拒绝产生成功报告，不偷偷替换为虚构指标。

## 本地示例（文件先经现有 Drive 方法恢复）

python -m unittest discover -s ai_research/tests -v

python -m pip install "pandas>=2.2,<3.0" "pyarrow>=18,<25"

python -m ai_research.offline_agents \
  --watchlist /path/to/watchlist_top20.csv \
  --summary /path/to/ranking_summary.json \
  --gold-root /path/to/gold/main_board \
  --financial-csv /path/to/fina_indicator_q.csv \
  --as-of 2026-10-09 \
  --top 5 \
  --output-dir /path/to/results

2026-10-09 是历史验证日，不代表今日实时数据。
历史网页证据只能使用对应交易日以前已经采集的事实；
当前聊天联网检索记录不可冒充历史当时已知信息。
