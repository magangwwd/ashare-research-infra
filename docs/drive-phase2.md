# Google Drive Phase 2 发布合同（待 Phase 1 稳定后实现）

用户后续明确要求协助准备 GitHub Actions 独立授权；现已提供 [OAuth 授权及只读验证工具](drive-auth.md)。用户已完成授权，重新授权后的 run #13 已核对，Runner 刷新和目标目录权限通过，见 [验收记录](acceptance-record.md)。当前没有 Drive 数据上传或 Production 发布；Task 01.1–01.12 稳定后再实施完整 01.13。

目标目录：

```text
Ashare_Quant/
  data/raw/
  data/clean/
  metadata/manifest/
  metadata/schemas/
  metadata/capabilities/
  logs/
  outputs/
  docs/
```

正式发布顺序：Runner 生成本地版本 → 本地 Critical 验收 → 上传独立 staging/version → 检查远程文件数、字节数、SHA256 和 Parquet/Manifest 一致性 → 写新 production manifest → 切换唯一 Current Pointer → 清理 staging。

不能直接覆盖 Production 文件。任何上传、远程验证或指针写入失败必须保持旧 Current Pointer 不变。数据按版本不可变保存；当前指针最后切换；中途失败的数据只能留在 staging，不能被 Task 02 消费。

接入阶段再配置 Drive 凭据，不写入代码或 YAML。核心数据集随版本上传 README 和 Schema；来源、主键、字段、分区、频率、清洗规则和注意事项必须齐备。

必需故障测试：上传中断、漏文件、checksum 错误、remote validation 失败和指针切换失败。每种失败都检查旧 Production 数据、Manifest 和 Current Pointer 完全不变；成功后只能通过新 Pointer 读取已验收版本。
