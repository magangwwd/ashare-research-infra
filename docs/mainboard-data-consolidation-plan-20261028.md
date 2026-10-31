# A股主板数据双链路整合实施方案（2026-10-28）

状态：PROPOSED / 未切换生产；禁止以计划代替验收。

## 权威数据与边界
- Task01 `Ashare_Quant/current.json -> versions/<version>/data/clean` 为目标统一原始/清洗数据权威层；现有 Gold `project/down_data/data/gold/main_board` 暂作冻结策略的兼容视图，不直接删除。
- Task01 管日线、15min、daily_basic、adj_factor、财务；Gold 兼容层必须补齐 instruments、daily_snapshot、历史月分区、复权及状态数据，不得简单复制 Task01 daily 冒充 Gold。
- Task01 独立 OAuth 凭据与 Gold rclone 凭据的 Drive 根不同；显式验证两侧文件夹访问，不复制凭据到仓库。

## 阶段与闸门
1. 基线：核对两套 workflow 最近7次运行、Drive current.json/Gold manifest、交易日 as_of、主板股票数、历史范围、字段及校验和；生成机器可读差异报告。
2. 适配器：独立模块从已验收 Task01 current pointer/manifest 只读恢复；转换为 Gold 结构，补齐复权、状态、历史分区；产出新目录 staging，不覆盖旧 Gold。
3. 影子验证：至少连续5个交易日比较证券集合、OHLCV单位、daily_basic、adj_factor、停牌/退市、历史数据与冻结 V1 的选股池/排名；全部差异有记录和处理；15min 不直接改变 V1 日频信号。
4. 原子发布：先验证新 Gold 所有分区、manifest、状态与策略回放，再切换 Gold 版本指针；失败保留旧 Gold 并告警。
5. 调度去重：只在步骤1-4验收后，将 23:50 Gold 的独立 Tushare 日线/估值下载替换为读取 Task01 已验收快照；保留策略状态生成及必要的股东数据下载。若 Task01 数据过期则停止，不默默回退重复下载。
6. 观察与回滚：连续5个交易日正常运行且回滚演练成功后，才考虑移除重复抓取入口；保留旧数据、脚本和恢复说明。

## 必须通过的测试
- 未验收/过期 Task01 指针拒绝；缺文件、SHA256 不符、缺证券、日期回退拒绝；单位、复权、停牌和退市历史一致；冻结策略候选集与排名无非预期变化；网络上传中断不切换指针；Gold 回滚后可正常选股。
- 成功证据：真实 GitHub Actions run ID、Drive 两端 manifest/版本ID、数据覆盖率、差异报告、冻结策略重放结果、回滚记录。不得仅凭 CI 绿色宣称整合完成。

## 生产安全
- 本 PR 只落地实施方案；不改 schedule、不停 Gold、不删数据、不改冻结 V1 规则。
- Task01 目前存在 Drive 发布故障修复验收依赖；在真实发布成功之前禁止切换。
