# 当前披露数据修复发布与验收

用户已授权在审阅、CI和真实数据门禁通过后合并上线。此处上线仅为研究数据与状态管线，不是券商交易，也不是策略盈利验收。

1. 在 PR #133 最新 SHA 验证全量 CI 和 offline-preflight。正式 V1 两份冻结文件哈希保持 PROJECT_MANDATE.md 的原值。
2. 等待真实来源运行 40987495007 完成。必须检查采集失败、实际状态日期、输出检查和 Drive 版本目录；失败不得复用或标成成功。
3. 合并前实读最新完整差异、主分支和 PR 状态；使用 expected_head_sha 防止合并未审阅更新，不绕过分支保护。
4. 在 main 手动运行现有 Drive-first 工作流：task=ownership-refresh，as_of=auto，min_as_of=2026-10-29，promote_ownership=true，reuse_ownership_run=40987495007-1。仅在该来源已成功且 Gold 日期仍为2026-10-29时复用；若 Gold 已更新则先采集对应日期，不把旧源伪装成新日。
5. 复用分支验证前次分阶段成功回执、精确日期和固定版本前缀，再复制/check processed。新代码重新计算状态、输出股东质量分项，保存配置及源/产物 SHA256 清单。禁止重复API全量采集来替代读取成功源。
6. 新版本、兼容状态输出均 rclone check 成功后，最后写 current_ownership.json。单独回读指针、snapshot_manifest.json、state/report.md、ownership_quality_summary.json和运行回执，核对代码、日期、覆盖数及清单哈希。失败保留原证据并修复，不删除历史目录。
7. 正式夜间链为 Gold 更新后 ownership-refresh --promote-ownership；current-state 只消费精确同日的已验证版本。已有08:00晨检复核新指针、分阶段回执及队列，无新增GPT任务或调度频率。

已保留的实际失败：CI 40989552197 暴露 pandas 3 字符串类型差异；40989885422 进一步暴露 NaN/None 差异。生产实现统一文本及缺失值，严格一致性断言保持不变；随后 CI 40990491597 为161 passed、8 warnings。

工程发布完成后，SS-REPLAY-004 / SS-EXEC-005及后续收益与前向验收继续按队列推进。不能把数据覆盖改善、A池增加或测试通过作为盈利证明；不能补记历史信号为真实前向成绩。
