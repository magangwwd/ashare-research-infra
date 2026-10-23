# Task 01 Drive 上传故障修复 Skill / SOP

适用：GitHub Actions Task 01 发布 Google Drive 快照时，出现网络中断、上传会话状态不确定、发布未完成。

## 固定步骤
1. 查 Actions 的 run/job/log，定位失败阶段和最后成功文件数；核对 `current.json`，不得把 CI 通过当作 Drive 发布成功。
2. 只修改 `data_pipeline/drive_publish.py` 相关的有限重试、resumable session 状态查询和安全日志。禁止日志输出 OAuth 凭据、完整 HTTP 响应体或上传 session URL。
3. 丢失 PUT 响应时，不盲目发送下一块：查询该 resumable session 已确认的偏移；只有与预期字节范围一致才继续。无法确认、偏移不一致、会话失效，一律停止且不切换 `current.json`。
4. 用 `tests/test_drive_publish.py` 模拟：正常分块、响应丢失且服务端已接收、偏移不一致、不可用会话、失败保留旧生产版本、文件 SHA256 不匹配。
5. 独立分支提交，草稿 PR，先运行 Pipeline CI；CI 失败按真实日志修正并复测。不得绕过安全检查；遇拒绝记录原文、时间、请求编号（如有）。
6. CI 通过后，再用已验收 checkpoint 单独验证发布；核对 Drive 文件数量、大小、SHA256、manifest 和 `current.json` 指针及旧版本可恢复性。避免重复全量上传和重复 staging。
7. 仅在真实发布、远端校验及生产指针验证均成功后，标记 VERIFIED_DONE；否则标记 REVIEW_REQUIRED/BLOCKED。

## 本次执行记录（2026-10-23）
- 初始生产故障：Run 39864704830，在约 800/5365 文件时上传中断，旧指针保留。
- 修复草稿 PR：#96，分支 `fix/task01-drive-network-retry-20261023`。
- 首轮 CI：Run 39930124294，116 passed / 1 failed / 2 skipped；失败点为确认最后一个 chunk 已接收后仍误判未完成。
- 后续修复提交：`5e029eba3e2f11fb29d20879fa7dbcdb340bc3b2`；待 CI 和真实云盘验证。
- 注意：同一分支含无害的写权限探测文档，合并前需审查变更范围。
