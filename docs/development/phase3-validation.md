# 阶段 3：工程骨架验收记录

日期：2026-10-02。版本：0.0.1.dev0。范围：OLG-005–008。

## 已完成

- 独立 Git 仓库、main 分支、src 包布局、源码/模块入口、隔离虚拟环境与 uv.lock。
- 中文 PySide6 原生首页、运行环境页、退出按钮与 Ctrl+Q；文本使用 tr，样式作为包资源读取。
- 独立数据路径、目录创建、UTF-8 有界启动日志、SQLite STRICT/JSON 功能探测和 WAL 版本判断。尚未创建资金数据库。
- Ruff、严格 mypy、pytest/pytest-qt、Windows GitHub Actions 配置；固定 Action SHA、最小权限、不发布 Release。
- Windows PyInstaller onedir/windowed 构建、许可收集、便携 ZIP 和 SHA256。
- README、使用/开发文档、贡献/安全/变更/许可文件、Issue/PR 模板，以及阶段 1/2 设计归档。

## 实际验证

| 检查 | 实际结果 |
| --- | --- |
| Ruff lint | 通过 |
| Ruff formatter | 通过 |
| 严格 mypy | 26 个检查文件通过 |
| pytest | 33 项通过：25 单元、4 集成、4 UI |
| 依赖锁与 Git 空白检查 | uv lock --check、git diff --cached --check 均通过 |
| Qt 原文完整性 | 56 份下载材料的字节与摘要匹配；工作区、Git 暂存内容和 exe 许可目录一致 |
| 源码入口及模块入口 | --version 均通过；不创建数据 |
| 源码原生窗口 | Windows Qt 插件下启动、样式加载、截图、关闭及进程退出通过 |
| Windows exe | 冻结运行库下同样通过；无外部 Python/Qt 环境变量，PATH 仅有 Windows 系统目录 |
| ZIP 解压运行 | CRC、资源、许可、DLL 边界检查通过；新的中文/空格解压目录启动与退出通过 |

本机实际环境：Windows 11 x64（10.0.26200）、CPython 3.12.5、PySide6/Qt 6.11.2、SQLite 3.45.3、PyInstaller 6.22.3、uv 0.10.4。具体下载依赖见 uv.lock；SQLite 未通过 WAL 门槛，资金连接将在阶段 4 使用已验证的回滚日志路径。

便携 ZIP 为 `OpenLedger-0.0.1.dev0-windows-x64.zip`，39,830,126 字节，852 个 ZIP 条目。SHA-256：

```text
8855768d695bfeda998d02c8bd3703c083e4d787a0ec5cf5c0d1d9a5619332c3
```

最终 Qt DLL 为 Core、Gui、Network、Svg、Widgets；PDF、Virtual Keyboard、QML/Quick 依赖未进入制品。标准 Python wheel/源码分发也已构建，样式资源与启动入口包含性已检查。

## 验证的实用边界

只在当前 Windows 11 主机验证，未进行干净系统、Windows 10、ARM64、安装器、升级/卸载和签名验收。此开发版不能记账，未实现真实迁移、资金用例、解析、统计、导入导出、托盘、热键、AI 或更新。

GitHub Actions 已配置，当前没有 remote，因此远端 CI 尚未执行。没有已发布 Release 或真实 GitHub Issue。Git 未配置作者身份，本阶段不替维护者设置身份或制作署名提交。

许可目录包含实际依赖的上游文本与版本清单，补充 Qt LGPL/GPL、文档许可、52 份归属页及来源摘要；这些材料不被描述为完整制品 SBOM。

依赖锁、SQLite 与冻结边界的实施选择见 [ADR 索引](../adr/README.md)。下一阶段为 OLG-009–014：资金核心、数据库与迁移、原子收支/转账/退款、期初/校准、备份恢复及故障测试。
