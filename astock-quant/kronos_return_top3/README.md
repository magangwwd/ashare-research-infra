# Kronos Return Top3

本目录把 [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) 的官方开源代码本地化到仓库内，并补了两条链路：

- `daily_data/*.csv` 的日频 Top3 回测
- `daily_data/*.csv` 到本地多资产微调数据集 manifest 的生成

## 当前实现

- 当前实盘主线优先使用 `Mini + fast_risk`：只跑一次 Mini，再用近期胜率、收益、波动和回撤在 Mini 候选池内快速重排。
- `Mini-only` 和 `--regime-gate` 先保留为研究/辅助口径；门控默认只在更极端弱市才 `no_trade`，中等偏上用 `fast_risk`，高适配才用 `mini_only`。
- `Kronos-small` / `Kronos-base` 级联、否决、综合评分暂只作为研究指标，不参与实盘选股。
- 通用回测 CLI 仍保留 `NeoQuasar/Kronos-base` + `NeoQuasar/Kronos-Tokenizer-base` 作为未指定模型时的默认值；实盘/主线验证请显式传入 `--use-mini`。
- 输入直接来自仓库 `daily_data/*.csv`
- 自动补充 `amount = close * volume`
- 预测后对 OHLC 做 A 股 `±10%` 涨跌停裁剪
- 每 `hold_days` 个交易日非重叠调仓一次，Top3 等权
- 若个股在执行窗口缺少开盘/收盘数据，则该仓位留现金，不向后补位
- 微调数据集以 `train/val/test manifest` 形式引用原始 `daily_data` 文件，不额外复制大批 CSV

## 当前实盘口径

自适应实盘入口可作为辅助观察口径。默认阈值为 `skip < 45` 停止预测，`45 <= score < 80` 使用 `fast_risk`，`score >= 80` 使用 `mini_only`；硬风控触发时会在加载 Kronos 前直接退出并写 `no_trade` 报告：

```bash
python -m kronos_return_top3.backtest --predict-only --full-universe --hold-days 1 --lookback 512 --max-context 2048 --use-mini --top-k 3 --batch-size 32 --regime-gate --regime-skip-threshold 45 --regime-mini-threshold 80 --output-prefix mini_adaptive_next1_top3
```

门控结果会额外写入 `output/<prefix>_regime_gate_latest.csv`。若要关闭硬风控，只用于观察分数，可加 `--disable-regime-hard-veto`。

Mini-only Top3 是当前基准执行策略：

```bash
python -m kronos_return_top3.backtest --predict-only --full-universe --hold-days 1 --lookback 512 --max-context 2048 --use-mini --top-k 3 --batch-size 32 --output-prefix mini_only_next1_top3
```

Mini + 快速风险评分使用同一轮 Mini 预测，不启用多路径采样，默认只重排 Mini 前 50：

```bash
python -m kronos_return_top3.backtest --predict-only --full-universe --hold-days 1 --lookback 512 --max-context 2048 --use-mini --top-k 3 --batch-size 32 --ranking-mode fast_risk --fast-risk-lookback 60 --fast-risk-candidate-pool 50 --output-prefix mini_fast_risk_next1_top3
```

10 日持有回测沿用同一口径：

```bash
python -m kronos_return_top3.backtest --full-universe --hold-days 10 --lookback 512 --max-context 2048 --use-mini --top-k 3 --batch-size 32 --output-prefix mini_only_h10
```

10 日持有的快速风险评分回测：

```bash
python -m kronos_return_top3.backtest --full-universe --hold-days 10 --lookback 512 --max-context 2048 --use-mini --top-k 3 --batch-size 32 --ranking-mode fast_risk --fast-risk-lookback 60 --fast-risk-candidate-pool 50 --output-prefix mini_fast_risk_h10
```

阶段性研究结论：Small/Base 级联重排、弱否决和多路径 Mini 综合评分暂只作为研究指标；实盘优先比较 Mini-only 与单次推理的 fast_risk 快速重排。

运行时默认显示股票池加载、预测批次和回测窗口进度条，包含 ETA；如需安静日志可加 `--quiet`。

## 安装

```bash
python -m pip install -r kronos_return_top3/requirements.txt
```

## 回测

样本回测：

```bash
python -m kronos_return_top3.backtest --sample-size 20 --hold-days 10 --lookback 512 --output-prefix sample20_base
```

更快的小样本验证：

```bash
python -m kronos_return_top3.backtest --sample-size 8 --hold-days 5 --lookback 128 --use-mini --output-prefix smoke_mini
```

全样本口径入口：

```bash
python -m kronos_return_top3.backtest --full-universe --hold-days 10 --lookback 512 --output-prefix full_base
```

CPU-only 环境建议先做有限期 smoke test：

```bash
python -m kronos_return_top3.backtest --full-universe --hold-days 10 --lookback 512 --max-periods 1 --output-prefix full_base_p1
```

## 研究入口：级联预测

三阶段级联入口仅作为研究入口，用于降低 `Kronos-base` 的推理成本；当前不作为实盘选股默认规则：

1. `NeoQuasar/Kronos-mini` + `NeoQuasar/Kronos-Tokenizer-2k` 跑全量。
2. Mini 阶段严格筛选：仅保留预测收益排名前 50 且预测收益不低于 5% 的股票；如果不足 50 只，不向后补位。
3. `NeoQuasar/Kronos-small` + `NeoQuasar/Kronos-Tokenizer-base` 对 Mini 候选重排，默认取前 10 传入 Base。
4. `NeoQuasar/Kronos-base` + `NeoQuasar/Kronos-Tokenizer-base` 对 Small 候选重排，输出最终 Top3。

下一个交易日预测：

```bash
python -m kronos_return_top3.cascade_predict --horizon 1 --output-prefix cascade_next1
```

10 日预测：

```bash
python -m kronos_return_top3.cascade_predict --horizon 10 --small-top-n 20 --output-prefix cascade_h10
```

复用已有 Mini 全量预测并做某一期历史窗口回测：

```bash
python -m kronos_return_top3.cascade_predict --backtest --horizon 10 --signal-date 2026-04-10 --small-top-n 20 --mini-predictions-path kronos_return_top3/output/mini_full_latest_h10_lb512_predictions_latest.csv --output-prefix cascade_h10_20260410_reusemini
```

保守融合验证入口默认只比较 Mini-only 和 `rank3_only_veto`。`rank3_only_veto` 会永久保护 Mini Top1/Top2，只在 Mini Top3 同时满足 `Small < -5%` 且 `Base < -5%` 时替换第 3 名，替补按 Mini 排名顺延。预测结果会写入 cache，适合断点续跑 10~30 个历史窗口：

```bash
python -m kronos_return_top3.fusion_validate --horizon 10 --max-windows 10 --window-step 10 --output-prefix fusion_h10_w10
```

更分散的 5 窗口随机验证，窗口之间至少间隔 30 个交易日，且用固定随机种子保证可复现：

```bash
python -m kronos_return_top3.fusion_validate --horizon 10 --max-windows 5 --random-windows --window-step 30 --sample-seed 42 --output-prefix fusion_h10_random_w5_gap30
```

复用已有 2026-04-10 Mini/Small 预测做单期 smoke：

```bash
python -m kronos_return_top3.fusion_validate --signal-dates 2026-04-10 --horizon 10 --output-prefix fusion_h10_20260410_smoke --seed-mini-predictions-path kronos_return_top3/output/mini_full_latest_h10_lb512_predictions_latest.csv --seed-small-predictions-path kronos_return_top3/output/cascade_h10_20260410_reusemini_small_predictions.csv
```

如需把旧的宽松双否决或三模型预测收益加权平均作为实验组，可显式追加 `--include-dual-veto` 或 `--include-weighted-fusion`。

Mini-only 稳健版验证入口只使用 `Kronos-mini`，比较原始 Mini Top3 和“当前预测收益 + 历史胜率 + 历史收益”的综合评分 Top3。若目标是尽量保持收益、同时降低回撤，可让当前预测收益占更高权重：

```bash
python -m kronos_return_top3.mini_score_validate --horizon 10 --max-windows 20 --random-windows --window-step 10 --start-date 2025-01-01 --sample-seed 42 --pred-weight 0.65 --win-weight 0.20 --return-weight 0.15 --output-prefix mini_score_h10_w20_p65
```

注意：当前仓库 `daily_data` 的全量统一交易日不足 2048，级联脚本默认 Mini `max_context=2048`，但实际 `lookback=512`，以保证全量股票覆盖。若未来数据长度足够，可显式传入 `--mini-lookback 2048`。

## 微调数据集

生成多资产微调 manifest：

```bash
python -m kronos_return_top3.prepare_finetune_data --lookback-window 512 --predict-window 10
```

生成后会得到：

- `finetune_data/asset_catalog.csv`
- `finetune_data/train_manifest.csv`
- `finetune_data/val_manifest.csv`
- `finetune_data/test_manifest.csv`
- `finetune_data/dataset_summary.json`
- `finetune_data/kronos_base_daily_data.yaml`

`kronos_return_top3.finetune_dataset.MultiAssetKlineDataset` 可直接读取这些 manifest，保证训练窗口不会跨股票边界。

## 输出

回测会按 `--output-prefix` 生成：

- `output/<prefix>_summary.csv`
- `output/<prefix>_periods.csv`
- `output/<prefix>_trades.csv`
- `output/<prefix>_predictions_latest.csv`
- `output/kronos_top3_YYYY-MM-DD_<prefix>.md`

级联预测会按 `--output-prefix` 生成：

- `output/<prefix>_summary.csv`
- `output/<prefix>_mini_predictions.csv`
- `output/<prefix>_mini_candidates.csv`
- `output/<prefix>_small_predictions.csv`
- `output/<prefix>_small_shortlist.csv`
- `output/<prefix>_base_predictions.csv`
- `output/<prefix>_final_top3.csv`
- `output/kronos_cascade_YYYY-MM-DD_<prefix>.md`

保守融合验证会按 `--output-prefix` 生成：

- `output/<prefix>_summary.csv`
- `output/<prefix>_periods.csv`
- `output/<prefix>_trades.csv`
- `output/<prefix>_candidates.csv`
- `output/<prefix>_cache/*.csv`
- `output/kronos_fusion_validation_YYYY-MM-DD_<prefix>.md`

Mini-only 稳健版验证会按 `--output-prefix` 生成：

- `output/<prefix>_summary.csv`
- `output/<prefix>_periods.csv`
- `output/<prefix>_trades.csv`
- `output/<prefix>_candidates.csv`
- `output/<prefix>_cache/*.csv`
- `output/kronos_mini_score_validation_YYYY-MM-DD_<prefix>.md`

## 说明

- `vendor_model/` 下的 Kronos 模型代码来自上游 MIT License 项目，许可证副本见 `LICENSE.upstream`
- 本地环境如果没有 GPU，`Kronos-base` 的全样本回测会非常慢，建议优先使用 `--max-periods` 或 `--use-mini` 验证链路
- 当前仓库里已实现“微调数据集准备”和“微调后模型可回测”的入口；实际微调训练本轮未执行
