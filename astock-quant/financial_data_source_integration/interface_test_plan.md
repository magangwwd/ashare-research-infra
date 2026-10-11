# 金融数据接口测试说明

归档日期：2026-05-05

目标：对 mootdx、腾讯财经、akshare、iwencai、Tushare、巨潮 cninfo 等数据源做小样本可用性测试，确认 5 个功能层面的免费可用接口边界，并为后续统一封装提供验收标准。

## 测试边界

本说明只定义接口测试和验收口径，不直接改造现有策略主流程。实测脚本后续应放在本目录下的 `scripts/` 或独立模块中，测试输出放在 `financial_data_source_integration/results/`，避免污染现有生成数据。

| 功能层面 | 主测源 | 对照源 | 目标数据 |
|----------|--------|--------|----------|
| 行情 | mootdx、腾讯财经 | akshare、Tushare | 日 K、分钟线、实时价、五档盘口 |
| 研报 | akshare 东方财富研报、iwencai | Tushare | 个股研报列表、评级、机构、PDF 链接、语义检索 |
| 新闻 | akshare | 其他源 | 个股新闻、东财快讯、新浪快讯、富途快讯、财联社电报 |
| 基础数据 | mootdx F10 | akshare、Tushare | 财务摘要、除权除息、公司资料、F10 文本 |
| 公告 | 巨潮 cninfo | akshare 间接公告接口 | 公告列表、公告分类、PDF 下载链接 |

## 环境准备

建议先在临时虚拟环境中测试，不复用策略训练环境。

```powershell
python -m venv .venv-data-source
.\.venv-data-source\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r financial_data_source_integration/requirements-smoke.txt
```

注意事项：

- 当前仓库本地 Python 记录为 `3.13.7`，部分第三方包可能还未完全适配，安装失败时先记录失败原因。
- `pywencai` 当前需要 cookie 参数，并依赖 Node.js 16+ 执行 JS 逻辑。
- Tushare 需要本机 `~/.tushare` token，且部分接口受积分限制，只做对照源测试。
- 行情实时接口要避开非交易时段误判，非交易时段只测试历史 K 线和公告/研报。

## 通用验收标准

每个接口至少记录：

| 字段 | 说明 |
|------|------|
| source | 数据源名称 |
| function | 接口或函数名 |
| sample | 样例标的或查询语句 |
| called_at | 调用时间 |
| elapsed_ms | 耗时 |
| rows | 返回行数 |
| columns | 返回列名 |
| status | pass、empty、blocked、error |
| error | 失败原因 |

通过标准：

- 返回结构可被 pandas DataFrame 或 JSON 稳定解析。
- 样例标的至少覆盖深市 `000001.SZ` 和沪市 `600000.SH`。
- K 线数据至少包含日期、开、高、低、收、成交量。
- 新闻、公告、研报至少包含标题、时间、来源或链接。
- 失败时能区分空数据、限流、权限、网络异常和字段变更。
- 高频调用源必须设置限速，默认不超过 1 次/秒；大批量任务另设断点缓存。

## 行情层测试

优先级：mootdx 实时和盘口，腾讯财经补 K 线，对照 akshare 和 Tushare。

### mootdx

测试点：

- `Quotes.factory(market="std")` 能建立连接。
- `bars(symbol="600000", frequency=9, offset=20)` 返回日 K。
- `minute(symbol="000001")` 在交易时段返回当日分钟线。
- `quotes(symbol=["000001", "600000"])` 返回批量实时行情。
- `xdxr(symbol="600000")` 和 `finance(symbol="600000")` 可作为基础数据补充。

样例：

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market="std", multithread=True, heartbeat=True)
bars = client.bars(symbol="600000", frequency=9, offset=20)
quotes = client.quotes(symbol=["000001", "600000"])
finance = client.finance(symbol="600000")

print(bars.tail())
print(quotes.head())
print(finance.head())
```

验收重点：

- `bars` 最近日期不能晚于当前已收盘交易日。
- `quotes` 在非交易时段可返回昨收/静态行情，但不得被误判为实时成交。
- mootdx 不作为北交所股票的默认源，北交所先走巨潮公告和后续候选源验证。

### 腾讯财经

测试点：

- 通过 akshare 的腾讯历史行情入口 `stock_zh_a_hist_tx` 验证日 K 补源能力。
- 如后续封装直连腾讯公开行情 URL，必须单独记录 URL、参数、响应字段和变更风险。

样例：

```python
import akshare as ak

df = ak.stock_zh_a_hist_tx(symbol="sz000001", start_date="20260401", end_date="20260505", adjust="")
print(df.tail())
```

验收重点：

- 腾讯 K 线与本仓库 `daily_data/000001.SZ.csv` 最近 5 个交易日的收盘价误差为 0 或可解释。
- 字段映射必须统一为 `Date,Close,High,Low,Open,Volume,TurnoverRate`，其中 `TurnoverRate` 为百分比数值。

## 研报层测试

优先级：东方财富研报元数据和 PDF 链接，iwencai 做自然语言补充。

### akshare 东方财富研报

测试点：

- `stock_research_report_em(symbol="000001")` 返回个股研报。
- 关键字段包含报告名称、机构、评级、日期、报告 PDF 链接。

样例：

```python
import akshare as ak

df = ak.stock_research_report_em(symbol="000001")
print(df[["股票代码", "报告名称", "机构", "日期", "报告PDF链接"]].head())
```

验收重点：

- PDF 链接为空时记录为 `partial`，不得直接丢弃整行研报。
- 研报发布时间属于可见数据，回测时按发布日期之后的信号日使用，避免未来函数。

### iwencai

测试点：

- `pywencai.get(query=..., cookie=...)` 可执行自然语言搜索。
- 查询样例包括“近一月机构评级上调的银行股”和“近三个月研报覆盖数最多的白酒股”。

样例：

```python
import os
import pywencai

cookie = os.environ["IWENCAI_COOKIE"]
df = pywencai.get(query="近一月机构评级上调的银行股", cookie=cookie)
print(df.head())
```

验收重点：

- cookie 缺失或失效要返回明确错误，不进入重试风暴。
- 返回列名可能随问句变化，封装层只能抽取标准列，不依赖固定中文列名全量存在。

## 新闻层测试

优先级：akshare 新闻接口。先做小样本单次调用，不批量轮询。

| 接口 | 目标 | 样例 |
|------|------|------|
| `stock_news_em` | 东方财富个股新闻 | `symbol="600000"` |
| `stock_info_global_em` | 东财全球财经快讯 | 无参数 |
| `stock_info_global_sina` | 新浪全球财经快讯 | 无参数 |
| `stock_info_global_futu` | 富途快讯 | 无参数 |
| `stock_info_global_cls` | 财联社电报 | `symbol="全部"` |

样例：

```python
import akshare as ak

news = ak.stock_news_em(symbol="600000")
em = ak.stock_info_global_em()
sina = ak.stock_info_global_sina()
futu = ak.stock_info_global_futu()
cls = ak.stock_info_global_cls(symbol="全部")

print(news.head())
print(cls.head())
```

验收重点：

- 新闻去重键使用 `source + title + published_at`，不能只按标题去重。
- 时间字段统一为本地 `Asia/Shanghai`。
- 新闻只作为辅助特征或日报摘要，不进入价格标签生成逻辑。

## 基础数据测试

优先级：mootdx F10 和财务快照，对照 akshare 和 Tushare。

测试点：

- `client.finance(symbol="600000")` 返回财务摘要字段。
- `client.xdxr(symbol="600000")` 返回除权除息数据。
- `mootdx.affair.Affair.files()` 可列出远程财务文件，下载测试仅限单个小样本文件。

样例：

```python
from mootdx.affair import Affair
from mootdx.quotes import Quotes

client = Quotes.factory(market="std")
finance = client.finance(symbol="600000")
xdxr = client.xdxr(symbol="600000")
files = Affair.files()

print(finance.head())
print(xdxr.tail())
print(files.head())
```

验收重点：

- 财务字段必须保留报告期或发布日期，不能只保留最新值。
- 与现有 `financial_rating/` 集成前，必须先设计字段字典，避免覆盖已有 `financial_data/scores.csv`。
- F10 文本类资料先作为“可读资料补充”，不直接进入模型特征。

## 公告层测试

优先级：巨潮 cninfo 原始公告。先只测列表查询和 PDF 链接拼接，不批量下载。

测试点：

- 通过巨潮公告查询页确认沪深北交所公告覆盖。
- 使用公开查询接口时记录请求参数、响应状态和公告 PDF 相对路径。
- 下载 PDF 前先做 HEAD 或小样本 GET，确认文件大小和 Content-Type。

样例参数口径：

```python
import requests

url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
payload = {
    "pageNum": 1,
    "pageSize": 30,
    "column": "szse",
    "tabName": "fulltext",
    "stock": "000001",
    "searchkey": "",
    "category": "",
    "seDate": "",
    "isHLtitle": "true",
}
headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
}

resp = requests.post(url, data=payload, headers=headers, timeout=20)
resp.raise_for_status()
data = resp.json()
print(data.keys())
```

验收重点：

- 巨潮是公告原始源，公告 PDF 链接要保留原始 `adjunctUrl`。
- 搜索接口可能受 Referer、Cookie 或反爬策略影响，失败时记录为 `blocked`。
- 公告分类和标题关键词只用于筛选，不作为公告事实本身。

## 对照源测试

### Tushare

测试目的：确认当前 token 和积分权限，不作为免费首选源。

```python
import tushare as ts

pro = ts.pro_api()
df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,area,industry,list_date")
print(df.head())
```

记录：

- token 是否存在。
- 接口是否命中积分限制。
- 可作为缺失基础数据的补充字段。

### akshare 行情对照

测试目的：确认 `stock_zh_a_hist` 可作为低频历史行情对照，不作为高频批量首选。

```python
import akshare as ak

df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20260401", end_date="20260505", adjust="")
print(df.tail())
```

## 结果记录模板

建议每次测试新增一个 Markdown 结果文件：

```text
financial_data_source_integration/results/
  2026-05-05_smoke.md
  2026-05-05_smoke_summary.csv
```

结果 Markdown 模板：

```markdown
# 数据源接口烟测结果

测试日期：
Python 版本：
包版本：
网络环境：

| source | function | sample | status | rows | elapsed_ms | notes |
|--------|----------|--------|--------|------|------------|-------|
| mootdx | bars | 600000 | pass | 20 | 350 | 日 K 正常 |

## 异常记录

- source:
- function:
- error:
- next_action:
```

## 分阶段测试计划

| 阶段 | 范围 | 产出 |
|------|------|------|
| P0 | 单标的烟测，每源 1 到 2 个接口 | 可用性矩阵和失败清单 |
| P1 | 20 只股票小样本，覆盖沪深主板、创业板、科创板 | 稳定性、耗时、字段变更报告 |
| P2 | 统一封装原型，只读缓存，不写主流程数据 | `AStockDataClient` 接口草案 |
| P3 | 与报告或研究脚本只读集成 | 新闻、研报、公告辅助数据样例 |

## 资料依据

- mootdx README 展示 `Quotes.factory`、`bars`、`minute`、`finance`、`Affair.files` 等用法：https://github.com/mootdx/mootdx
- AKShare 股票文档列出 `stock_zh_a_hist`、`stock_zh_a_hist_tx`、`stock_research_report_em`、`stock_news_em`、`stock_info_global_em`、`stock_info_global_sina`、`stock_info_global_futu`、`stock_info_global_cls` 等接口：https://akshare.akfamily.xyz/data/stock/stock.html
- pywencai 说明 `pywencai.get(query=..., cookie=...)` 以及 cookie/Node.js 依赖：https://pypi.org/project/pywencai/
- 巨潮资讯网为公告查询和公告 PDF 的原始入口：https://www.cninfo.com.cn/
