# trade_cal

来源：Tushare；Clean Schema 1.0.0。

主键：exchange + calendar_date。分区：`year=YYYY`，ZSTD Parquet。

更新：每日，行情按类型近期回补；基础信息刷新。重复保留最后一条，日期转 Arrow Date，未知源字段仅进入 Raw。

注意：只读验收后的 `data/clean/trade_cal`；历史正在分批推进时须检查 Manifest 的实际范围。金额/股本/成交量等单位遵循 Tushare 接口原口径，Task 01 不计算因子或复权价格。

| source_field | clean_field | dtype | description |
| --- | --- | --- | --- |
| exchange | exchange | string | Tushare 原字段 exchange; 口径及单位遵循接口文档 |
| cal_date | calendar_date | date | 自然日期 |
| is_open | is_open | int64 | 是否交易日 |
| pretrade_date | previous_trade_date | date | 上一交易日期 |
| schema_version | schema_version | string | Clean Schema 版本 |
