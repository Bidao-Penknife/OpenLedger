# 架构决策记录

ADR 记录已经接受的跨模块选择及其原因。阶段 2 设计包保留规划时的上下文；从工程骨架开始，具体依赖锁、SQLite 运行策略、打包边界等实施决策在本目录记录。

每条 ADR 包含：状态、日期、问题、考虑的约束、决策、后果与验证。使用连续编号和有意义的文件名。旧决策被替代时保留原文，注明替代记录，避免失去演变依据。

当前依赖锁使用 `uv.lock`，替代设计草案中的 `requirements.lock`。Python 基线为 3.12；SQLite 的能力与 WAL 策略需要根据实际运行库确定。具体环境与最终门槛由相应 ADR 和阶段验收记录给出。

- [ADR-010：依赖锁与虚拟环境](010-dependency-lock.md)
- [ADR-011：SQLite 运行策略](011-sqlite-runtime-policy.md)
- [ADR-012：Windows 冻结边界](012-windows-bundle-boundaries.md)
- [ADR-013：显式 SQLite 事务与线性迁移](013-sqlite-migrations.md)
- [ADR-014：资金命令与默认偏好](014-financial-command-boundary.md)
- [ADR-015：本地草稿、桌面确认与设置边界](015-local-drafts-and-desktop.md)
- [ADR-016：统计快照、文件预览与原子报告](016-analytics-and-file-exchange.md)
- [ADR-017：桌面入口、可选服务与扩展契约](017-desktop-and-optional-services.md)
- [ADR-018：当前用户安装与发行候选](018-installation-and-release.md)
- [ADR-019：Android 原生界面与共享 Python 核心](019-android-shared-core.md)

阶段 6 使用 Qt 原生绘图与 PDF 写入、不可变统计快照和预览后原子批次；现行实现决策已记录，统一本地验证结果另见[阶段 6 验收记录](../development/phase6-validation.md)。决策已采纳不表示发行验收已经通过。
