# 通用自主研发标准变更记录

## v1.0.0 / 2026-10-21
- 首次制定跨项目“自主接续”标准，结合ETF任务队列与BTC巡检/去重/故障处理。
- 记录ETF真实成功run 39597990839、BTC真实schedule run 39596575303、BTC前后两次去重验收run 39589940046。
- 增加Backlog模板与统一验收Receipt Schema；研究默认NOT_FOR_TRADING。
- 区分已运行的GPT定时审核，与**尚未部署**的Work Pull Request事件任务以及独立OpenAI API webhook Agent。
- 建立每周指标：接力延迟、有效任务完成率、数据来源验收、重复回测、费用、失败误报、安全违规。

## 下一实验（尚未启动）
- BTC建立依赖型Backlog，ETF和BTC各跑一条可追溯任务的receipt入库验证。
- 用户在Work配置GitHub PR事件触发后，实测一个脱敏研究PR的事件驱动审核；如该事件不可用，则仅在另获外部服务和API预算授权后设计OpenAI API Agent。
- 使用隔离分支，不改冻结量化策略、不推断历史模拟可用于实盘。
