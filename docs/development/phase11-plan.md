# 阶段 11：连续补齐 Android 功能

开始日期：2026-10-06。维护者要求按既定路线连续实现，完成阶段验证后直接进入下一项。本期保留源码、开发环境和既有发布制品。

## 工作顺序

1. 完整备份、安全恢复、新旧数据目录切换，验证桌面备份格式互通。
2. 交易详情、编辑/删除/恢复、转账/退款/余额校准、账户/账本/分类/标签/支付渠道管理和组合筛选。
3. 统计趋势、分类占比、消费排行与比较；CSV/Excel 预览导入、批次撤销与导出；原生 PDF/PNG 报告。
4. 可选 AI、Android Keystore 凭据、主题/语言/时区、GitHub 更新提醒和手机快速入口。
5. 图片附件、扩展与同步接口、完整回归、APK 构建、模拟器功能与升级/重启验收、文档和 GitHub 交付。

当前云同步仍按原需求预留接口，第一版本地运行，不绑定云服务。支付通知读取、OCR 与语音录入不在既定路线中。ARM64 真机和最低系统运行只能按实际设备证据报告，不以模拟器代替。

## 设计约束

- 保持 SQLite Schema v1 的原始迁移文件；金额使用共享整数分模型，不在 Kotlin 重写账务算法。
- 所有资金写入由同一串行队列执行，确认命令保存请求 UUID，并绑定当前数据目录。
- 恢复先验证并写入新目录，再原子保存活动目录；任何失败保留原账目。恢复与目录切换不能跨越未完成的确认请求。
- 用户文件通过 Android 系统文件选择器读写；只将明确选择的文件复制到受限暂存目录，JSON 接口不接受任意绝对文件路径。
- AI 默认关闭，只在明确请求时联网，建议仍需要用户确认；密钥不写入账本、财务备份或日志。
- 统计和交换复用共享核心，PDF/PNG 使用 Android 原生绘图；APK 不包含 Qt 或 Windows 窗口依赖。
- 保留原预览包名和调试签名以验证覆盖升级；本期版本根据完成与验收情况标为 beta，不提前声明稳定真机认证。

## 验收

各步骤运行真实 SQLite 接口测试与必要设备测试。完成后执行 Python 全量回归、Ruff、类型检查、Kotlin 格式、Android Lint、构建、APK 权限/签名/ABI/迁移检查，并在独立合成模拟器验证保存、修改、资金操作、文件交换、备份恢复和重启。使用合成数据，不删除日常用户账本。

文件接口依据：[Android 系统文件选择器](https://developer.android.com/training/data-storage/shared/documents-files)。凭据依据：[Android Keystore](https://developer.android.com/privacy-and-security/keystore)。手机入口依据：[Quick Settings tiles](https://developer.android.com/develop/ui/views/quicksettings-tiles)。
