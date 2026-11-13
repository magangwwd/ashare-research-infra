# Task01 → Drive 发布超时恢复：Skill v1.2（候选，待验收）

## 证据与故障分类（2026-11-13）
- Run [43303877396](https://github.com/magangwwd/ashare-research-infra/actions/runs/43303877396)，job 131481953603：北京时间约 04:48 开始、10:18 在 Publish accepted snapshot to Drive 被取消；工作流 task01 timeout-minutes=330。
- 日志：创建暂存区，8323 文件 / 3486835462 字节；已记录 staged 6400/8323，尚未执行全量远端 SHA 验证；GitHub checkpoint artifact 14003857374 成功保存。
- 这是运行时限命中与完整快照发布耗时过长的直接证据；不能据此断言 Google Drive API 故障、外部并发取消或文件损坏。
- data_pipeline/drive_publish.py 默认新建 staging，逐文件上传、再逐文件远端下载做 SHA256；仅显式 --resume-stage-id 才会验证并复用同一暂存区。切勿把 checkpoint 存在误称为同源暂存已验证。

## 防止重复工作：必须按顺序执行
1. 读取最新 main、producer run/jobs/logs、当前 Drive pointer 和 production_manifest；SHA、版本、日期双重读回，确认无并发生产写入。按 source run + stage id + checkpoint SHA 计算唯一恢复指纹；同指纹已有审计/恢复任务则停止重复派发。
2. 只读检查指定 staging 与 checkpoint 属于同一源运行，checkpoint 未过期；逐文件校验路径、大小、SHA，记录 verified/missing/bytes；旧 production pointer 不得改变。
3. 只有审计 passed、回滚可用、容量足够、无并发且恢复守卫允许，才可调用受控 resume workflow；只补传经验证缺失的文件。审计 success 不等于发布 success。
4. 恢复完成必须验收所有文件 SHA、manifest、current pointer、旧版本回滚读回。任何失败保持旧生产指针，分类记录并禁止同条件盲目重试。
5. 为长期修复单独开发增量/内容寻址复用与上传阶段独立预算：不能仅按文件名、大小或当前 Gold 名录复用；必须验证内容 SHA、PIT 归属、数据版本和不可变性。先模拟取消和断点续传测试，再精确 head CI，再评估上线。

## 不变的安全边界
- 不删除旧 Raw/Clean 或历史非主板数据，不改变 Gold 独立下载、冻结 V1、08:00 晨检。
- 至少五个有效交易日影子比对、候选排名一致、回滚演练、双端 SHA/指针读回完成前不得去重切换。
- 禁止泄露 Secrets、绕过 CI、无证据宣称正式发布或恢复成功。

## 验收清单
- [ ] 对最新故障指纹的审计证据和同源 checkpoint 有效性完整核验
- [ ] 同一暂存区模拟中断后，SHA 验证已上传文件且仅补传缺失项
- [ ] 同指纹重复派发被拒绝，生产并发锁保持有效
- [ ] 受控恢复的精确 head CI 与真实运行成功
- [ ] 正式 pointer、manifest、全量数据 SHA 和旧版回滚读回成功
- [ ] 连续多轮稳定运行；Skill 同步 Drive 并保存可追溯链接

> 本文是候选操作指南，不代表代码修复、生产恢复或云盘同步已经完成。

## 2026-11-13 13:04 北京时间复核收据
- 旧审计 #41287586055：job 在 Restore verified checkpoint without network writes 失败，不能复用或再次派发。
- 同源只读审计 #42367840442：真实日志通过；7507 文件中 SHA 核验 6553、缺 954（374906452 字节），pointer_unchanged=true、production_switch_allowed=false。**该审计属于 11月7日旧源，checkpoint 已过期，不得用来恢复 11月13日的新源。**
- 新源 #43303877396：11月13日约10:18北京时间命中 330 分钟限制，staged 6400/8323；checkpoint artifact #14003857374 有效期至 11月16日10:20北京时间，尚未做新源 stage 的同源全量 SHA 审计。
- Drive 正式 Task01 current.json 读取结果：as_of=2026-11-11、8321 文件、critical_history_complete=false；Gold 正式 manifest as_of_date=2026-11-12。正式 production_manifest 的内容 SHA 与所有文件 SHA 本轮未完成二次核验，不可宣称完整发布或去重切换。
- 现有草稿 PR #123 已包含超时后独立恢复的设计，但其 235–265 分钟判定来自旧 240 分钟上限；现行 Task01 上限 330 分钟，必须更新触发条件并重新进行安全测试，**不可直接合并或启用**。
