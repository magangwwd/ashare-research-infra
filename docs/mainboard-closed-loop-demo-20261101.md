# A股主板 Task01 × Gold：GPT决策闭环第一轮（只读，不切生产）
**记录日期：2026-11-01；状态：WAIT_ACTIVE_TASK01 / NOT PRODUCTION ACCEPTED。**

本示范按 Drive × GitHub × ChatGPT 主 Skill 执行：实读 GitHub Jobs/Artifacts + 两端 Drive 正式 Manifest → 发现阻塞 → fail-closed 决策 → 本地测试 → GitHub 草稿 PR 和 CI → 下一轮在运行终态后复核。

## 本轮真实证据
- main 基线 SHA `f4a9023619061328d034bb701c58baaa57a647c0`。
- [Task01 #41248659151](https://github.com/magangwwd/ashare-research-infra/actions/runs/41248659151)：读取时 `in_progress`；job `124719079710` 的验收成功、发布步骤仍运行；验收 artifact ID `13066229507`，尚不能证明发布。
- [旧stage只读审计 #41160259395](https://github.com/magangwwd/ashare-research-infra/actions/runs/41160259395) 成功，但其源 run #41119601566 的同源 checkpoint 不在 GitHub 当前工件列表内，禁止复用旧审计续传。
- Drive `Ashare_Quant/current.json`：截止 `2026-10-24`，`critical_history_complete=false`，5406文件；实际 production manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，与 pointer 匹配。
- Drive Gold `main_board/manifest.json`：`as_of_date=2026-10-30`，`primary_keys_unique=true`、`ohlc_valid=true`；尚未完成真实影子验收。

## 最小可验证改动
- `tools/mainboard_cycle_gate.py`：读本地 JSON，不联网、无上传、无凭据。交叉检查日期、正式 SHA、版本及文件量、历史完整性和 GitHub run 状态；输出 JSON receipt。
- `tests/test_mainboard_cycle_gate.py`：验证活动上传禁止再触发、缺数据、SHA与数量篡改、失败 run、缺运行证据均 fail closed。
- 通过真实三份 Drive JSON 加 run #41248659151 已读取状态的本地试跑：`decision=WAIT_ACTIVE_TASK01`，`issues=[TASK01_BEHIND_GOLD,TASK01_HISTORY_INCOMPLETE]`，`retrigger_allowed=false`，`production_switch_allowed=false`。本地 `pytest` **8 passed**；GitHub CI 须另核实终态。

## 接续门禁
只有该 run 完成以后才重新读取 jobs、artifacts 和 Drive current；若成功仍须查正式 pointer/manifest 实际变更、SHA、文件数、旧版本保留，再开展主板 PIT、5交易日影子比对、冻结 V1 对照和回滚。**不启动重复发布、不停止 Gold、不删除旧数据、不修改冻结 V1、不创建新的定时任务。**
