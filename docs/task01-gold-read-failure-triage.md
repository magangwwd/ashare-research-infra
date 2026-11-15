# Task01 / Gold 正式清单读取故障与防重复操作（只读，2026-11-13）

## 已验证事实与边界
- Drive 正式 Task01 指针 ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB` 可通过连接器读取；2026-11-12 版本指向 manifest ID `1QnPb3o0tLtrz7slMYpAzLU7gGRkPbl4S`，数据截至 2026-11-11，`critical_history_complete=false`。
- 正式 manifest 大约 2.1 MB；连接器元数据和 fetch 可访问，不能仅因返回 `content=""` 断言权限不足。连接器可能返回 `b64_string`、`file_uri` 或自动物化文件，需按实际响应选取读取路径。不能将元数据可读等同于内容校验通过。
- 历史缺口、PIT 归属、影子比对、V1 重放与回滚均为独立验收事项。读取故障修复不代表生产整合完成。

## 最小只读排障顺序
1. 读取生产 current.json 两次，记录指针版本、manifest_file_id、manifest_sha256、source_run_id、snapshot_as_of 和读取时间；两次不一致则停止，避免竞态。
2. 按指针中的 **manifest_file_id** 查询 Drive 元数据，检查是否存在、大小、MIME 类型、修改时间与可访问状态；不要硬编码旧 manifest ID。
3. 获取文件内容：先使用连接器支持的文本、base64 或物化路径；`content=""` 时检查 `b64_string` 和 `file_uri`，不得直接判定为空文件或权限失败。大文件采用下载到隔离临时目录后流式 SHA256；禁止在日志中输出完整文件或凭证。
4. 计算下载文件的 SHA256，与 current.json 的 manifest_sha256 比较；解析 JSON，核对版本、来源 run、日期、文件数量、总字节数和关键完整性字段。再读一次 current.json，确保指针未改变。
5. 按错误类型分类：403/权限拒绝→权限；404→ID/对象；429/5xx/超时→短暂服务故障；内容为空但有物化路径→读取适配；SHA/字段不符→数据完整性。只有可证明的短暂故障才指数退避重试（最多三次）。
6. 记录 `run_id + pointer_version + manifest_id + manifest_sha256 + error_class` 指纹、首次失败步骤、修复结果和证据链接。相同指纹已成功则跳过；相同确定性错误不重复派发/上传；运行中的生产者不启动并行生产者。

## 通过条件与安全约束
- 单元测试覆盖空 content/有效 b64/物化路径、403、404、429、SHA 不匹配、指针变化和重复指纹；在隔离分支运行精确 head CI，通过前只读。
- 仅当真实 SHA、JSON、前后指针一致，才能称“清单读取与校验通过”；否则明确区分“元数据可访问”“已下载”“已校验”。
- 不修改或删除旧 Task01 Raw/Clean 非主板数据；不绕过安全检查；不触发 Gold 去重或生产切换；独立 Gold 下载、冻结 V1、原版本和 08:00 晨检保持不变。
- 后续将本流程与真实测试收据同步至云盘 Skill v1.1；未写入云盘前不得声称已同步。
