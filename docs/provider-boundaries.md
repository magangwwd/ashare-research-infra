# 已核实的 Tushare 边界

2026-10-02 真实审计确认五个 Critical 接口可调用。接口权限随账号变化，以每次能力快照为准。

- `stock_basic` 返回退市标识 `T600018.SH` 的 market 为空；保留原记录和 UNKNOWN board，记元数据 Gap。当前上市证券的 market 仍必须存在，不依据代码数字猜测板块。
- 上海日历首批历史从 1990-12-19 开始，深圳本次首日为 1991-07-03，SZSE 的 1990 年查询为空；程序保存实际观察起点。起点以前的空范围不伪造；当前年为空、可用范围内部缺年或请求异常仍为 Critical 失败。历史理论池同时使用交易所当日实际开市日历。
- 1990-12-19 的 daily 逐股补抓后仍无法满足理论池覆盖率。当前账号基于覆盖率的边界探测得到 daily / daily_basic 首次合格日期 1992-05-08，adj_factor 为 1990-12-19，保存在 `metadata/capabilities/history_ranges.json`。二分探测采用边界之后覆盖率通常连续的假设，并非逐日穷举；每个实际下载历史日期仍独立验证，覆盖不足禁止发布。边界前数据保留 Raw/Gap，每月重新探测，不伪造 Clean，也不声称该日期是接口永久历史起点。
- ST 原资料的正确接口为 [`stock_st`](https://tushare.pro/document/2?doc_id=397)，3000 积分起、单次最多 1000 行，文档起点 2000-01-01。保存提供者原记录，不展开全股票每日 is_st。
- 股本盘前快照为 [`stk_premarket`](https://tushare.pro/document/2?doc_id=329)，独立开通权限、文档起点 2024-05-01。无权限时记 Required Gap，仍长期保存 daily_basic 原股本字段。不在 Task 01 计算股本变化因子。
- SW2021 和 SW2014 分类审计可用；CITICS 分类探测返回空，不能当作已获得主行业体系。sw_daily、ci_daily、ci_index_member、stk_surv 的本次权限不足，不影响 Critical。后续权限开通后重新审计。
- [`index_member_all`](https://tushare.pro/document/2?doc_id=335) 和 [`ci_index_member`](https://tushare.pro/document/2?doc_id=373) 默认 `is_new=Y`，因此必须分别请求 Y/N 才能保存当前和退出成员；历史证券请求不能限制在历史下载起始年已上市的证券。
- [`dividend`](https://tushare.pro/document/2?doc_id=103) 支持证券与公告日等单日参数，没有 start_date/end_date 范围参数。程序按证券一次请求其历史，再轮转刷新；财务 Raw/Clean 分区规则保持不变。财务指标 [`fina_indicator`](https://tushare.pro/document/2?doc_id=79) 的范围参数按报告期，不能将其请求日期范围当作公告日期承诺；Raw 保留实际公告日期并按公告年月落库。

以上范围来自真实响应或明确接口文档，不是声称历史全量已完成。
