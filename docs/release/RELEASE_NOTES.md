# OpenLedger 1.0.0rc1

这是首个提供 Windows 安装程序的发行候选，尚未发布稳定版。日常记账、账户转账、退款、期初和余额校准、规则解析、统计图表、CSV/XLSX 交换及 PDF/PNG 报告均保留既有资金事务边界。

提供完整源码、Windows x64 便携 ZIP、当前用户安装 exe、SHA256 和制品清单。安装器无需管理员权限，升级前退出应用；卸载保留账目、备份、偏好和 Windows AI 凭据。AI 默认关闭，更新检查需用户配置仓库并手动触发。

使用安装包时按向导完成安装，或完整解压便携包后运行 `OpenLedger\OpenLedger.exe`。首次使用先创建账户并明确期初。默认数据在 `%LOCALAPPDATA%\OpenLedger`，安装目录是 `%LOCALAPPDATA%\Programs\OpenLedger`。

当前制品未签名；无 Python 的独立 Windows、真实系统输入法以及最终分发许可审阅须在稳定版发布前完成。英语界面部分报告及校验提示仍保留中文；附件录入、插件市场和云同步尚未实现。候选版不自动下载更新。

请核对 SHA256SUMS.txt，并参阅源码中的 README、CHANGELOG、使用指南和阶段 8 验收记录。维护者在发布草稿时应填入该 tag 的实际测试、干净系统结果及签名状态；不要把本地结果改写为已执行的远端 CI。
