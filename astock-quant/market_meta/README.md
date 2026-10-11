# Market Meta

`market_meta/` 存放不适合直接写入 `daily_data/*.csv` 的日频市场元数据。

当前口径：

- `daily_basic.csv`：Tushare `daily_basic` 原始落盘，包含估值、市值、股本和 Tushare 换手率。
- `daily_market_features.csv`：模型可直接按 `Date,ts_code` join 的衍生特征。
- `turnover_audit.csv`：`daily_data` 的 `TurnoverRate` 与 Tushare `turnover_rate` 的抽样校验。

常用命令：

```bash
python -m market_meta.collect_daily_basic --latest
python -m market_meta.collect_daily_basic --start 2026-04-01 --end 2026-04-30
python -m market_meta.build_features
```

`daily_data` 仍是主行情表；这些数据只在模型训练时按日期拼接。
