# A股主板 Task01 × Gold｜三平台闭环示范：第1轮真实收据（2026-11-01）

**状态：PARTIAL / WAIT_ACTIVE_TASK01。不是生产发布成功。**
通用 Skill 权威版本：Google Drive《Drive-GitHub-ChatGPT 持续项目闭环》v1.1.0（[主 Skill](https://docs.google.com/document/d/1FfIBLynr5dKQHS6HKBvg6cMbPsczWdWZeazOqBtoG6U/edit) / [RUNBOOK](https://docs.google.com/document/d/1mxmgVkQ0HF0-G2MvQp_wij1TWJyfb3jT1kOAL8DqbPA/edit)）。

## 实际闭环链条与证据

1. **自动运行（GitHub）**：截至 2026-11-01 03:52 UTC，生产 [Task01 run #41248659151](https://github.com/magangwwd/ashare-research-infra/actions/runs/41248659151) 的 `task01` job **in_progress**，验收、回放通过，`Publish accepted snapshot to Drive` 仍在执行。源 run HEAD `00493bc7220536a86e7eb610801873580c7b535f`；验收 artifact `13066229507` 已存在。**不得在同一时刻额外派发恢复上传或复跑旧审计**。
2. **数据存储（Drive 官方文件读回）**：Task01 正式 [current.json](https://drive.google.com/file/d/1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB/view) 为 `2026-10-24`、`file_count=5406`、`total_bytes=2331724247`、`critical_history_complete=false`。其 [production_manifest.json](https://drive.google.com/file/d/1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1/view) 实际 SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8` 与指针吻合，版本/文件数/字节吻合。Gold 官方 [manifest.json](https://drive.google.com/file/d/1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn/view) 截止 `2026-10-30`，两个质量标志为 true。读取时间仅为本轮快照，不保证日后不会变化。
3. **GPT 发现问题**：Task01 比 Gold 旧，历史完整性未通过，而且生产上传尚在运行；历史 [run #41141750990](https://github.com/magangwwd/ashare-research-infra/actions/runs/41141750990) 的 `resumable_upload` 收到 HTTP 409（约上传 2500/7108 文件时失败）。409 类型不能直接猜测为 410、OAuth 失效或配额；需在本次当前运行结束后依据同 run 日志定位。
4. **受控改进（本 PR）**：新增 `tools/mainboard_cycle_gate.py`，仅消费**已取得的官方 JSON 字节和 Actions run JSON**，离线返回结构化机器收据。检测 manifest SHA、Task01 pointer/manifest 一致性、历史质量、Gold 质量、日期，优先防止在 active Task01 时错误再次触发；`retrigger_allowed=false`、`production_switch_allowed=false` 固定为安全拒绝。它不访问网络、不持有令牌、不写云盘、不改冻结 V1。
5. **验收与继续**：对应 `tests/test_mainboard_cycle_gate.py` 六项离线单元测试；检查本 PR 的**最新** GitHub CI run 结果，不得以本文件替代 CI。当前实测收据预计为 `WAIT_ACTIVE_TASK01`，问题 `TASK01_BEHIND_GOLD`、`TASK01_HISTORY_INCOMPLETE`。生产 run 完成后重新取得本轮指针/manifest/run，再运行同一门禁；若旧指针未变化，检查发布日志和同 run checkpoint；若已更新，则核验实际新快照，并继续 5 交易日影子、冻结策略、回滚。**不自动执行 GitHub Actions dispatch**；现有 GPT 集成接续任务负责证据检查和下一轮决策。

## 离线使用

```bash
python tools/mainboard_cycle_gate.py \
  --task01-pointer /path/to/Task01/current.json \
  --task01-manifest /path/to/Task01/production_manifest.json \
  --gold-manifest /path/to/Gold/manifest.json \
  --task01-run /path/to/github-run.json
```

退出码 `2` 表示未达到下一阶段门禁，不等于程序失败或自动重试授权；`0` **只代表可进一步进行影子验证，不代表可以生产切换**。调用者必须先通过授权来源分别拉取 4 份证据；本工具没有后台持续运行能力。最新运行及指针请按真实 GitHub/Drive 记录重查。

## 未达到的验收关卡

- Task01 本轮发布终态与正式指针新日期读回；完整历史数据覆盖；当前源 run 的同源 checkpoint。
- Gold 主板 PIT 身份、复权/状态/估值/财务、连续 5 交易日影子、冻结 V1 一致性、生产回滚。
- 全部真实通过前不合并生产切换、不去重下载、不删除旧文件，不关闭北京时间08:00晨检或整合接续任务。

**下一唯一优先动作**：等待 `#41248659151` 结束，读取其 `Publish accepted snapshot to Drive` 终态及同 run artifacts，并重新读回正式 `current.json`；不重复派发恢复上传。
