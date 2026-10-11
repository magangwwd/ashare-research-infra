# 香山股份持仓实时监控

本目录跟踪香山股份 `002870.SZ`，当前配置买入价为49元。程序只读取行情、计算退出条件并发送邮件，不包含券商接口，不会自动下单。

## 已实现规则

- 冻结ATR止损：入场日前20日平均真实波幅占比 × 1.5，限制在4%—8%，再乘实际买入价得到止损价；首次初始化后固定保存，不随行情移动。
- 买入日触及止损：邮件提示A股T+1限制，下一可卖交易日开盘处理。
- 后续交易日跳空或盘中止损：按照策略优先发出止损邮件。
- 拉升转派发：读取最新 `current_state_transitions.csv`；出现 `markup_to_distribution` 后提示下一可卖交易日开盘退出。
- 时间退出：买入后第20个后续交易日收盘提示退出。
- 去重：同一事件发送成功后写入 `runtime/state.json`，不会每30秒重复发信。
- 多源核验：东方财富、腾讯和新浪报价中至少两个新鲜来源可用，且价差不超过0.3%才视为确认；单源触发仍发警告，但标记为“来源不足”。

正式V1没有固定价格止盈，也没有启用移动止盈。历史移动止盈V2未达到替换标准，所以盈利退出仍是“拉升转派发”或20日时间退出。

## 重要配置

`config.json` 已写入：

```text
股票：002870.SZ 香山股份
买入价：49.00元
暂定买入日：2026-08-24
```

用户只提供了买入价，没有提供实际成交日。因此买入日暂按需求日期2026-08-24设置。实际成交日若不同，必须先修改 `position.entry_date`，然后执行：

```powershell
python monitor.py --reset-state --initialize --dry-run
```

股数不是触发条件，当前保留为 `null`；如需在邮件中计算持仓盈亏金额，可填写实际股数。

## 首次初始化

在本目录运行：

```powershell
python monitor.py --check-config
python monitor.py --initialize --dry-run
```

初始化会读取入场日前复权日线计算ATR20，将止损比例和止损价写入 `runtime/state.json`。如果修改了股票、买入日或买入价，程序会拒绝沿用旧止损，必须显式使用 `--reset-state`。

## 邮件设置

程序会自动读取本目录的 `.env`，且不会把邮箱密码写入JSON或日志。打开 `.env`，填写：

```dotenv
LIVE_MONITOR_SMTP_USER=你的Gmail地址
LIVE_MONITOR_SMTP_PASSWORD=16位Google应用专用密码
LIVE_MONITOR_SMTP_FROM=你的Gmail地址
LIVE_MONITOR_SMTP_TO=收件邮箱地址
```

`.env` 已加入项目忽略规则。应用专用密码只在本机监控进程中读取，不写入审计日志、状态文件或邮件正文。

当前模板已预设Gmail：`smtp.gmail.com`、SSL端口465。Gmail需要先开启Google账号两步验证，再生成16位应用专用密码；不要填写Google账号的普通登录密码。

发送测试邮件：

```powershell
python monitor.py --test-email
```

若运行环境设置了 `HTTPS_PROXY` 或 `HTTP_PROXY`，SMTP 会自动通过 HTTP CONNECT
代理发送；也可在 `.env` 中用 `LIVE_MONITOR_SMTP_PROXY` 单独指定。代理地址及凭据
不会写入运行日志。

## 启动监控

单次实时检查：

```powershell
.\start_monitor.ps1 -Once
```

连续运行，盘中默认每30秒检查一次，非盘中每15分钟检查一次：

```powershell
.\start_monitor.ps1
```

只查看将要发送的邮件，不实际发送：

```powershell
.\start_monitor.ps1 -Once -DryRun
```

窗口关闭或计算机休眠后监控会停止。长期运行可将 `start_monitor.ps1` 放入 Windows 任务计划程序，并设置登录后启动、失败后重启。监控代理会自动读取私有 `.env` 并在触发后发送邮件。

## 安全模拟

模拟价格跌至45元，验证止损邮件内容，但不连接行情、不发邮件：

```powershell
python monitor.py --simulate-price 45 --dry-run
```

模拟拉升转派发：

```powershell
python monitor.py --simulate-transition markup_to_distribution --dry-run
```

## 状态数据更新要求

盘中价格止损由双源实时行情直接判断。拉升转派发属于日级收盘后信号，必须由主项目每日更新状态转换文件。程序依次查找：

1. `../output/latest/state_transition/current_state_transitions.csv`
2. `../output/forward_latest/state_transition/current_state_transitions.csv`
3. 2026-08-18历史只读快照

若只找到过期快照，程序会发出 `STATE_DATA_STALE` 运维告警，且不会把旧信号当成当前派发退出。应先更新 `down_data`、股东披露和日级状态，再发布到上述 `latest` 路径。

## 运行产物

- `runtime/state.json`：冻结止损、持仓指纹、已发送事件和最近一次实时状态。
- `runtime/latest_status.json`：最近一次真实行情检查的独立状态摘要。
- `runtime/monitor_audit.jsonl`：每次监控的行情源、时间戳、价差、判断、邮件结果及异常。

日志不会保存SMTP密码。免费行情没有SLA，任何邮件都必须由人复核实时价格、停牌、涨跌停及可卖状态。
