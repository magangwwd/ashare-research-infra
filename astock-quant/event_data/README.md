# Event Data

`event_data/` 存放公告、新闻和研报元数据。它们不写入 `daily_data/*.csv`，只在建模时按 `Date,ts_code` 聚合后拼接。

当前文件：

- `announcements.csv`：巨潮公告元数据。
- `research_reports.csv`：东财个股研报元数据。
- `stock_news.csv`：东财个股新闻元数据。
- `daily_event_features.csv`：日频滚动事件特征。

常用命令：

```bash
python -m event_data.collect_events --sample-size 20 --days 30
python -m event_data.build_features
```

事件特征默认使用 1 个自然日可见性滞后，尽量避免同日盘后公告或新闻被信号日提前使用。
