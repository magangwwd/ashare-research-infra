# AGENTS.md

本文件是 Codex 和 Claude Code 共用的主说明。项目审核时间：2026-05-04（最后更新：2026-05-05）。工作前先读本文件，再按需参考 `lightgbm_return_top3/README.md` 和 `kronos_return_top3/README.md`。

`CLAUDE.md` 只作为 Claude Code 的兼容入口，要求 Claude Code 自动转读本文件；后续维护以 `AGENTS.md` 为准，避免两份长文档漂移。

## 项目概览

这是一个 A 股量化选股研究项目，主流程是：

```text
screen_stocks.py
  -> filtered_stocks.csv
download_daily.py / update_daily.py
  -> daily_data/{ts_code}.csv
financial_rating
  -> financial_data/scores.csv
lightgbm_return_top3.model_backtest
  -> lightgbm_return_top3/output/predictions_latest.csv
kronos_return_top3
  -> kronos_return_top3/output/*_predictions_latest.csv
generate_quality_h20_report.py
  -> financial_data/reports/report_YYYY-MM-DD.md
combined_top3
  -> combined_top3/output/*
```

所有策略输出都是研究辅助结果，不是实盘成交确认或投资建议。

## 环境与依赖

- 当前本地 Python 为 `3.13.7`。
- 仓库没有统一的 `pyproject.toml` 或根目录 `requirements.txt`。
- 根流程常用依赖：`pandas`、`numpy`、`yfinance`、`tushare`、`akshare`、`lightgbm`。
- 旧强化学习试验目录 `rl_baofeng_sb3/`、`rl_baofeng_600989/` 已删除，当前主流程不再依赖 Stable-Baselines3、gymnasium 或 torch。
- Kronos 依赖在 `kronos_return_top3/requirements.txt`，安装命令：

```bash
python -m pip install -r kronos_return_top3/requirements.txt
```

- Tushare Pro token 预期在 `~/.tushare`，代码通过 `ts.pro_api()` 读取。
- Kronos 模型默认从 HuggingFace `NeoQuasar/` 加载。CPU 可跑，但 full-universe/base 会很慢。

## 仓库结构

- `screen_stocks.py`：用 Tushare 筛主板股票池，输出 `filtered_stocks.csv`。
- `download_daily.py`：用 yfinance 全量下载股票日线到 `daily_data/`。
- `update_daily.py`：增量更新 `daily_data/`，使用 `daily_data/.last_dates.json` 缓存。
- `TechnicalPatternScanner/`：公共数据加载和技术指标工具。
- `financial_rating/`：财务数据采集、评分和基础报告。
- `market_meta/`：Tushare `daily_basic` 市值/估值/股本数据采集、换手率校验和模型特征输出。
- `event_data/`：公告、新闻、研报元数据采集和日频滚动事件特征输出。
- `lightgbm_return_top3/`：LightGBM 20 日收益预测、回测和日报。
- `kronos_return_top3/`：Kronos 时序预测、回测、级联和微调数据集准备。
- `combined_top3/`：LightGBM 粗筛 + Kronos-mini 精排的联立选股。
- `rl_baofeng_sb3/`、`rl_baofeng_600989/`：旧宝丰能源强化学习试验目录，2026-05-05 已删除；不要作为当前主流程依赖。
- `generate_latest_report.py`：一键日更流水线（update_daily → financial_rating → LightGBM → 归档报告），默认同样受 `update_daily.py` 工作日 09:15–15:05 盘中 guard 约束；盘中只查看已有数据时用 `--skip-daily`，确认只做历史回填时才用 `--force-daily` 显式透传 `update_daily.py --force`。
- `eval_kronos_ic.py`：Kronos mini OOS IC 评估脚本，缓存于 `kronos_return_top3/output/ic_eval/`，每期约 20–30 分钟（CPU）。
- `financial_data/`、`daily_data/`、各 `output/`：数据和生成产物，通常不要手工编辑。

## 数据约定

- 股票代码使用 Tushare 格式：`000001.SZ`、`600000.SH`。
- yfinance 下载时：深市 `.SZ` 保持不变，沪市 `.SH` 转为 `.SS`，落盘再转回 Tushare 格式。
- 日线 CSV 标准列为 `Date,Close,High,Low,Open,Volume,TurnoverRate`，`Date` 为 `YYYY-MM-DD`；`TurnoverRate` 为百分比数值，例如 `0.47` 表示 0.47%。
- 读写中文 CSV 优先使用 `encoding="utf-8-sig"`；普通 JSON/Markdown 用 UTF-8。
- PowerShell 查看中文文档时用 `Get-Content -Encoding UTF8 <path>`，避免终端乱码误判。

## 常用命令

数据准备：

```bash
python screen_stocks.py
python download_daily.py
python download_daily.py --test
python update_daily.py
python update_daily.py --test
python update_daily.py --start 2026-03-01
python update_daily.py --end 2026-04-03
python backfill_turnover_rate.py --test
python backfill_turnover_rate.py --code 000001.SZ
python -m market_meta.collect_daily_basic --latest
python -m market_meta.build_features
python -m event_data.collect_events --sample-size 20 --days 30
python -m event_data.build_features
```

财务评级：

```bash
python -m financial_rating
python -m financial_rating --score-only --skip-report
python -m financial_rating --collect-only
python -m financial_rating --skip-report
python -m financial_rating --test
```

报告生成：

```bash
python generate_latest_report.py                          # 一键日更：prices → scores → LightGBM → report，默认受盘中 guard 约束
python generate_latest_report.py --skip-daily             # 盘中只用已有日线继续刷新后续评分/报告
python generate_quality_h20_report.py                     # 仅生成归档报告（依赖已更新的各输入文件）
python generate_quality_h20_report.py --kronos-weight 0.15  # 覆盖 Kronos 权重（LightGBM 自动调整为 0.35）
python generate_quality_h20_report.py --allow-lgbm-only   # Kronos 未刷新时生成 LightGBM-only 临时报告
python review_quality_report.py financial_data/reports/report_2026-06-12_2.md --as-of 2026-07-02 --top-n 3  # 自动复盘归档报告
python predict_single_stock.py 603061.SH                  # 单股走势预测，归档到 financial_data/reports/single_stock/
python predict_single_stock.py 金海通 --hold-days 20       # 支持股票名称，默认单股更新 + Kronos-mini H20
python predict_single_stock.py 603061.SH --with-lightgbm   # 全市场日线覆盖率达标时，额外运行正式 LightGBM 截面日报
```

`generate_quality_h20_report.py` 可信度权重（截至 2026-05-04 OOS 校准）：LightGBM 35% + Kronos 15% + 财务分 25% + 过去表现 20% + 一致性 5%。权重在文件顶部常量 `_W_LGBM`、`_W_KRONOS` 定义，可通过 `--kronos-weight` CLI 临时覆盖。同一天重复生成会自动写 `_2`、`_3`，不会覆盖旧 `financial_data/report.md`。

默认报告仍要求 LightGBM 与 Kronos 的 `signal_date` 一致。`--allow-lgbm-only` 只作为临时降级路径：Kronos 过期时强制 Kronos 权重为 0%、LightGBM 权重临时为 50%，并在报告中明确标注“不等同于正式双模型报告”。

单股报告默认只处理目标股票：增量更新 `daily_data/{ts_code}.csv`，用 Tushare `daily_basic` 补最近换手率/估值，运行 Kronos-mini 单股预测，并生成 Markdown 归档。输出是研究辅助，不替代全市场 LightGBM 截面排名。需要 LightGBM 正式信号时显式加 `--with-lightgbm`；脚本会先检查全市场日线覆盖率（默认阈值 95%），覆盖率不足时不会运行 LightGBM，只在报告中标注不可用原因。

LightGBM：

```bash
python -m lightgbm_return_top3.model_backtest
python -m lightgbm_return_top3.model_backtest --test
python -m lightgbm_return_top3.model_backtest --sample-size 100
python -m lightgbm_return_top3.daily_report
python -m lightgbm_return_top3.daily_report --trade-date 2026-04-24
```

Kronos：

```bash
python -m kronos_return_top3 --use-mini --sample-size 20 --hold-days 10 --max-periods 1 --output-prefix smoke_mini
python -m kronos_return_top3 --predict-only --use-mini --hold-days 20 --full-universe --batch-size 32 --output-prefix mini_full_h20_lb512 --quiet
python -m kronos_return_top3 --predict-only --use-mini --hold-days 20 --full-universe --batch-size 32 --output-prefix mini_full_h20_lb512 --signal-date 2026-03-04 --quiet  # 历史 signal_date（IC 评估用）
python -m kronos_return_top3.backtest --predict-only --full-universe --hold-days 1 --lookback 512 --max-context 2048 --use-mini --top-k 3 --batch-size 32 --ranking-mode fast_risk --fast-risk-lookback 60 --fast-risk-candidate-pool 50 --output-prefix mini_fast_risk_next1_top3
python -m kronos_return_top3.prepare_finetune_data --lookback-window 512 --predict-window 10
```

Kronos IC 评估：

```bash
python eval_kronos_ic.py                  # 默认 6 期，间隔 20 交易日，每期约 20–30 分钟
python eval_kronos_ic.py --periods 2      # 快速验证（约 40–60 分钟）
python eval_kronos_ic.py --force-rerun    # 忽略缓存重新推理
```

联立选股：

```bash
python -m combined_top3
python -m combined_top3 --test --hold-days 5
python -m combined_top3.backtest --max-periods 2 --output-prefix combined_backtest_smoke
```

## 日常数据更新与报告生成规程

轻量日更在每个 A 股交易日收盘后执行，避免盘中写入未收盘的日线数据。最新版报告只看 `financial_data/reports/` 下的归档文件，旧 `financial_data/report.md` 不再作为最新版输出目标。

日常五步：

```bash
python update_daily.py
python backfill_turnover_rate.py
python -m financial_rating --score-only --skip-report
python -m lightgbm_return_top3.daily_report
python generate_quality_h20_report.py
```

| 步骤 | 更新内容 | 主要输出 |
|------|----------|----------|
| `update_daily.py` | 增量更新日线行情 | `daily_data/*.csv`、`daily_data/.last_dates.json` |
| `backfill_turnover_rate.py` | 为缺失日期补齐换手率字段 | `daily_data/*.csv` 的 `TurnoverRate` |
| `market_meta.collect_daily_basic` / `market_meta.build_features` | 更新市值、估值、股本和换手率校验 | `market_meta/daily_basic.csv`、`market_meta/daily_market_features.csv`、`market_meta/turnover_audit.csv` |
| `event_data.collect_events` / `event_data.build_features` | 更新公告、新闻、研报元数据和滚动事件特征 | `event_data/*.csv` |
| `financial_rating --score-only --skip-report` | 用已有财务数据和最新日线重算评分/行业景气折扣 | `financial_data/scores.csv` |
| `lightgbm_return_top3.daily_report` | 刷新 LightGBM 最新 20 日预测 | `lightgbm_return_top3/output/predictions_latest.csv` |
| `generate_quality_h20_report.py` | 刷新 H20 质量池并生成归档报告 | `financial_data/kronos_h20_quality_universe.csv`、`financial_data/reports/report_YYYY-MM-DD.md` |

Kronos H20 不是每天必须跑。检查 `kronos_return_top3/output/mini_full_h20_lb512_predictions_latest.csv` 的 `signal_date`，超过 2 周或需要完整刷新模型观点时执行：

```bash
python -m kronos_return_top3 --predict-only --use-mini --hold-days 20 --full-universe --batch-size 32 --output-prefix mini_full_h20_lb512 --quiet
python generate_quality_h20_report.py
```

月初刷新 LightGBM 模型和验证指标：

```bash
python -m lightgbm_return_top3.model_backtest
python -m lightgbm_return_top3.daily_report
python generate_quality_h20_report.py
```

每季度重新校准 Kronos 权重：

```bash
python eval_kronos_ic.py --periods 6
# 根据输出 mean_IC 更新 generate_quality_h20_report.py 顶部的 _W_LGBM / _W_KRONOS 默认值
python generate_quality_h20_report.py
```

财报季后完整更新财务采集：

```bash
python -m financial_rating --skip-report
python generate_quality_h20_report.py
```

股票池需要重筛时：

```bash
python screen_stocks.py
python update_daily.py
python -m financial_rating --skip-report
python -m lightgbm_return_top3.daily_report
python generate_quality_h20_report.py
```

每次生成报告前后都用 `git status --short` 检查变更范围，重点确认 `daily_data/.last_dates.json`、`financial_data/scores.csv`、`lightgbm_return_top3/output/predictions_latest.csv`、`kronos_return_top3/output/mini_full_h20_lb512_predictions_latest.csv` 和 `financial_data/reports/` 的状态。

## 验证建议

- 这个仓库没有 pytest 测试套件；以 CLI 冒烟测试和小样本回测为主。
- 改 CLI 参数或入口时，至少跑对应 `--help`。
- 改 `financial_rating/` 评分逻辑时，优先检查 `python -m financial_rating --help`；真实评分会覆盖 `financial_data/scores.csv`，日常用 `--skip-report` 避免覆盖旧 `financial_data/report.md`。
- 改 LightGBM 特征或回测时，优先用 `python -m lightgbm_return_top3.model_backtest --test`。
- 改 Kronos 逻辑时，优先用 `--use-mini --sample-size 8 --max-periods 1` 这类小样本命令。
- 改报告脚本时，可以运行 `python generate_quality_h20_report.py` 做安全验证；它只新增归档报告并刷新质量候选池，不覆盖旧 `financial_data/report.md`。
- 很多验证命令会写 CSV/Markdown 输出。运行前后用 `git status --short` 确认自己制造了哪些变更。

## 高风险操作

- 不要在 A 股交易时间内运行 `update_daily.py`，当天未收盘数据可能写入不完整。脚本已在工作日 09:15–15:05 增加 hard guard；`--force` 只用于确认不会写入未收盘行情的历史回填。
- 不要直接编辑 `financial_data/scores.csv`，评分会覆盖它。
- 不要无意批量格式化或重写 `daily_data/*.csv`，这些文件数量巨大且当前工作区经常有数据变更。
- 不要清理或回滚用户已有变更；本仓库常见大量 `daily_data/`、`financial_data/`、`output/` 变更。
- 不要把 `__pycache__/`、`.vscode/`、`.obsidian/`、临时输出等顺手纳入功能改动。
- 不要恢复或重跑已删除的 `rl_baofeng_sb3/`、`rl_baofeng_600989/` 旧强化学习试验，除非用户明确要求。
- Full-universe Kronos 和完整 `financial_rating` 都可能运行很久，并且会访问外部 API。

## 需要定期维护的硬编码

- `screen_stocks.py`：`pro.pledge_stat(end_date=...)` 需要更新为最近可用交易日。
- `screen_stocks.py`：`output_path = 'g:/我的云端硬盘/stock/filtered_stocks.csv'` 是机器相关绝对路径。
- `TechnicalPatternScanner/config.py`：`BACKTEST_END`、`OOS_START` 需要随样本推进。
- `lightgbm_return_top3/model_backtest.py`：`TRAIN_END`、`VALIDATION_START`、`VALIDATION_END`、`OOS_START` 独立维护。
- `generate_quality_h20_report.py`：会重建 `financial_data/kronos_h20_quality_universe.csv`，并依赖 `kronos_return_top3/output/mini_full_h20_lb512_predictions_latest.csv`。可信度权重（`_W_LGBM=0.35`、`_W_KRONOS=0.15`）定义在文件顶部，可通过 `--kronos-weight` 临时覆盖；每季度用 `eval_kronos_ic.py` 重新校准后更新默认值。

## 编码风格

- 保持现有模块风格：旧根脚本多用 `os.path`，新策略模块多用 `pathlib.Path` 和类型标注。
- 新增 CLI 参数使用 `argparse`，默认值要与 README/本文件同步。
- Pandas 处理优先向量化；只有 Tushare/akshare 逐股接口适合受控并发。
- 项目内新增代码、配置、文档和报告内容统一使用相对仓库根或模块目录的路径；内部可用 `Path(__file__)` 定位，但不要把机器相关绝对路径写入产物。
- 写 CSV 时明确编码；会被 Excel 或中文工具打开的结果优先 `utf-8-sig`。
- 策略逻辑里区分信号日可见数据和执行日可交易数据，避免未来函数。
- 代码注释要解释业务口径或风险，不要复述语法。

## 输出与提交范围

- 功能代码改动应尽量集中在相关模块，避免顺带重写大批生成数据。
- 如果必须更新数据产物，在最终说明里列清楚命令、输出文件和覆盖原因。
- 提交前检查：

```bash
git status --short
```

只汇报或提交与任务有关的文件。当前仓库可能已有大量未提交变更，默认视为用户工作成果。
