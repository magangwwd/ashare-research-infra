# A股主板 Task01 × Gold：Drive/GitHub/GPT 闭环示范｜第1轮

**状态：本轮证据检查和只读防重复决策工具已提交；尚未完成生产发布、五交易日影子验收或整体整合。**

## 三方职责

- GitHub：已有定时 Task01 和 Gold 工作流执行数据下载/验证；PR + CI 执行代码检查，保存 run/job/artifact。
- Google Drive：正式 Task01 \`current.json\` / \`production_manifest.json\`、Gold \`manifest.json\` 和原始/版本化数据是数据实情。当前指针只能用真实读回核实，不能凭 CI 推断。
- ChatGPT：读取实时证据、选一项安全可验证修复、提交隔离 PR、读取 CI/数据结果、更新下轮决策。

## 本轮现场证据（2026-11-01 北京时间）

| 证据 | 已核实事实 |
|---|---|
| main SHA | \`f4a9023619061328d034bb701c58baaa57a647c0\`，含 #151 恢复存档链接白名单安全修复 |
| Task01 [run #41248659151](https://github.com/magangwwd/ashare-research-infra/actions/runs/41248659151) | 最终 \`completed/cancelled\`；\`Production acceptance\` 成功，\`Publish accepted snapshot to Drive\` 被取消；**正式发布未验收** |
| 上述运行同源工件 | \`task01-validation-report\` #13066229507，SHA256 \`cd148f32c67c6b2b176cac4d80bd9ea272fa3ee2dc69e18d9abfa3cf2de67557\`；\`task01-checkpoint\` #13072181704，3,936,680,028 bytes，SHA256 \`b18cf6fe55874e5615e9d24129761cbea71f7ac5b98593d999079ca8708602bc\`；当次查询 \`expired=false\` |
| Task01 Drive \`Ashare_Quant/current.json\` | 文件 ID \`1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB\`；\`snapshot_as_of=2026-10-24\`、\`validated=true\`、\`critical_history_complete=false\`、5406 files |
| Task01 Drive \`production_manifest.json\` | 文件 ID \`1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1\`；原始字节 SHA256 \`118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8\` 与指针一致 |
| Gold Drive \`main_board/manifest.json\` | 文件 ID \`1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn\`，\`as_of_date=2026-10-30\`、\`primary_keys_unique=true\`、\`ohlc_valid=true\` |
| 历史只读 stage 审计 [#41160259395](https://github.com/magangwwd/ashare-research-infra/actions/runs/41160259395) | 4350/7104 个 stage 文件通过 SHA；另 2754 缺失。原来源 #41119601566 同源 checkpoint 已消失，**不得复用这一旧配对恢复** |
| 原有 Gold 数据差异草稿 [PR #149](https://github.com/magangwwd/ashare-research-infra/pull/149) | 代码/诊断在独立草稿；没有五交易日影子、PIT 历史板块、冻结 V1 一致性或回滚的完整生产验收 |

## GPT 本轮决策与执行

**优先级：P1**。现存 Task01 核心障碍是耗时上传未完成；以新正式数据覆盖 Gold 或重跑旧恢复均不安全。

本轮提交与生产完全隔离的 \`tools/mainboard_loop_gate.py\` 和 \`tests/test_mainboard_loop_gate.py\`：

1. 仅使用调用方同轮准备的运行/工件 JSON 和官方 Drive 三份文件的**本地字节**，无网络、无 Google/GitHub 凭据、无写入、无触发。
2. 对 Task01 manifest 原始 SHA256、指针字段、文件数和总字节、历史完整标志、日期先后、Gold 质量标志、run 与发布/验收步骤、同run工件存在性做 fail-closed 检查。
3. 运行中返回 \`WAIT_FOR_ACTIVE_RUN\`，被取消或发布失败返回 \`BLOCKED_PUBLISH\`，不完整/落后返回 \`BLOCKED_METADATA\`；全部元数据正确仅返回 \`SHADOW_VALIDATION_REQUIRED\`。
4. **任何情况输出 \`production_switch_allowed=false\` 和 \`automatic_retrigger_allowed=false\`**；不能当作直接的恢复发布审批。
5. 当前事件对应 \`BLOCKED_PUBLISH\`：只读观察到同run checkpoint，但**未核验唯一 stage ID / 完整 stage SHA**；因此不运行 \`task01-verified-stage-resume.yml\`。

**限制：**程序依据输入的 JSON 生成诊断收据，不能证明调用者取证真实性；必须由 GPT 先从 GitHub/Drive 实读并锁定来源。本轮未自动新增 schedule 或 webhook，不能声称 GPT 被 GitHub Actions 即时唤醒。

## 下一项唯一任务与验收标准

**验收 #41248659151 对应的 stage ID 和同 run checkpoint，准备新的只读 SHA 审计**：

- 先确认没有另一个 Task01 正在发布；
- 从 #41248659151 job 原始日志抽取其**唯一** staging 文件夹 ID，并与同 run checkpoint/验收报告一一对应；
- 重新读取 Drive 正式 pointer，比较 hash、日期和来源；
- 在所有安全条件满足时才考虑**一次**新的只读审计；若缺任一工件、SHA/来源不符则 \`BLOCKED\`，不触发；
- 只有后续全量 SHA 与所有文件字节数量、旧 pointer 稳定、同 run 工件已验收且无并发，才评估受控恢复；恢复仍需再验收真实 Drive 指针。

之后依次进行主板 PIT 核验、5交易日影子、冻结 V1 排名一致性、回滚。**继续保留两套现有生产下载、旧数据、冻结 V1 策略和北京时间08:00晨检。**


## 2026-11-01 14:20 北京时间补充：该 run 的唯一 staging 已定位

- 从 #41248659151 的 task01 job #124719079710 **实际日志第734行**读取：\`SUCCESS staging created folder_id=1Twq0EP00oq6xYjojhTmiTdnAkM1Y7cik files=8657 bytes=3096760067; current pointer unchanged\`。
- 最后一个可见分批上传确认：\`SUCCESS staged files=8600/8657; remote verification pending\`；**还有57项未得到日志确认**，不能将 8600 项视为全量 SHA 通过；随后 \`The operation was canceled\`。
- 同源 checkpoint 工件 #13072181704 存在但此处尚未实际下载/解包核验，不能据其存在就启动受控 resume。
- 下轮仅在无 Task01 生产并发、两份同源工件未过期且安全审计条件满足的情况下，按**精确源 run #41248659151 + 精确 stage ID 1Twq0EP00oq6xYjojhTmiTdnAkM1Y7cik**核验只读 SHA 审计输入；旧中转分支不再启用。不要把当前 8600/8657 当成可直接补57项的许可。
