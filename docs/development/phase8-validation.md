# 阶段 8 本地发行验收

日期：2026-10-04。版本：`1.0.0rc1`。状态：本地实施与验收通过；稳定发行门禁未全部通过。

本地计划与验收范围见[阶段 8 计划](phase8-plan.md)。安装架构见 [ADR-018](../adr/018-installation-and-release.md)，使用见[安装指南](../user/installing.md)，独立环境见[清洁 Windows 验收](clean-windows.md)。

## 代码结构

```text
packaging/inno/                   固定安装标识、向导与原始语言资源
scripts/setup_inno.ps1            固定下载、摘要/发布者验证、便携编译器
scripts/build_installer.ps1       完整 bundle 核对与编译
scripts/verify_installer.ps1      无 Python 的真实安装/恢复/卸载入口
scripts/release_tools.py          tag、PE 版本、源码与制品清单
src/openledger/infrastructure/platform/installation.py
.github/workflows/{ci,release}.yml
```

## 实际结果

本机 Windows 11 专业工作站版 10.0.26200 x64，普通用户令牌。解释器 Python 3.12.5，PySide6/Qt 6.11.2，SQLite 3.45.3，uv 0.10.4，PyInstaller 6.22.3。数据库 Schema 仍为 1、日志模式 DELETE；没有改写既有资金结构。

| 验证 | 实际结果 |
| --- | --- |
| 全部 pytest | 988 通过：450 单元、350 集成、188 UI；0 失败/错误/跳过，174.91 秒 |
| 代码质量 | Ruff lint 通过；163 个 Python 文件格式通过；严格 mypy 检查 120 个文件通过 |
| 原生启动 | 源码、冻结目录、实际便携 ZIP 解压程序全部通过；Qt `windows`、原生 HWND、资源/Schema/翻译/离线引擎齐全；启动写入交易 0、远端请求 0 |
| 首次安装 | 新隔离用户数据目录、中文及空格路径，真实静默安装/修复/恢复/卸载全部通过 |
| 升级安装 | 原阶段 7 程序与丰富合成账目为基线，真实旧安装→当前升级→修复→备份/恢复→卸载全部通过；11 个检查组 |
| 安装边界 | 非本应用的非空目录、降级、活跃进程下安装/卸载、安装中启动新程序均拒绝；不强制结束其他进程 |
| 资金保留 | 全部 16 个应用表的行内容与 Schema 指纹一致；源数据库、偏好、备份和未知程序文件保持，测试卸载项移除 |
| bundle | 核对实际 869 个载荷文件的大小和 SHA256；便携 ZIP CRC 通过 |
| 翻译与通知 | 498 个英语翻译完成、0 未完成；TS/QM 与冻结/便携字节一致；56 份 Qt 原文、11 个运行依赖材料及新增 Inno 原文核对 |
| 工作流与脚本 | actionlint 1.7.12 检查两个工作流通过；新 PowerShell 脚本语法检查通过 |
| 清洁系统门禁 | `-RequireCleanSystem` 在本机检测到 Python，按设计拒绝；没有将此拒绝记为独立 Windows 通过 |

升级基线是用阶段 7 原始便携 ZIP 中未修改的 `0.3.0.dev0` exe 与当前安装脚本编译的本地验收夹具，补充安装标识和通知；它不是历史发布过的安装程序。合成源库 SHA256 为 `3cb781fe1e76bdb3a6e36fb27f8d874b7ef0a05cd5ced48de44ffc56f1381fbc`，4 个资金交易、总资产 842.00 元，包括已确认 AI 来源与原文。验收不调用真实 AI、不读取日常用户凭据。

恢复的 SQLite 文件摘要与备份 manifest 比较；源库与恢复库的资金、资料和 Schema 用规范化逻辑指纹比较，避免把 SQLite backup 的文件头变化误判为数据变化。首次与升级报告均为 `ordinary_user=true`、`system_path_only=true`、`clean_windows_certified=false`。

安装编译器固定 Inno Setup 6.7.3。官方下载包 SHA256 为 `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`，实际签名有效、发布者 Pyrsys B.V.；只解出仓库内便携工具。编译前核对 20 个固定工具二进制与 bundle，不配置全局编译器安装。应用 exe 与安装程序本身仍未签名。

## 缩放与视觉检查

在实测原生 DPR 1.5 的本机，分别使用 Qt 缩放乘数产生有效 DPR 1.0、1.5、2.0；每个数值由窗口实际读回并断言。三组主窗口和快速窗口原生截图共 6 张，浅色/深色均查看，快速窗口确认按钮完全在窗口内，长表单可滚动。另查看真实安装后默认中文主窗口截图。

这没有修改 Windows 显示设置，也不能代表不同物理显示器或真实输入法认证。英语界面在有效 DPR 1.0 的固定侧栏副标题存在轻微截断，核心表单与保存按钮正常；保留为候选版界面细节。完整 Windows 显示/IME/托盘/热键手动验收仍待执行。

## 制品与证据

交付完整源码 ZIP、Windows x64 便携 ZIP、当前用户安装 exe、逐个 SHA256 与总清单；它们全部读取同一 `1.0.0rc1` 源码版本，PE 数字版本为 `1.0.0.30001`。源码归档包含当前未暂存的已授权新增文件；不包含 Git 元数据、构建缓存、数据库、财务备份或密钥。

本地原始证据在 `build/validation/phase8/`，包括 `pytest.xml`、质量日志、源码/冻结/便携启动报告、`installer-final/installer-check.json`、`fresh-installer/installer-check.json` 和 DPR 报告。用户交付记录提供三类制品的具体大小、摘要、来源与验证证据 ZIP。测试账目和财务备份仅留在本地隔离验证目录，不进入公开源码或证据包。

所有既有阶段的验收结果和制品保持历史快照，不用本阶段结果覆盖阶段 7。

## 稳定发行门禁

- 无 Python 的独立 Windows 验收：未执行，本机 Windows Sandbox 不可用。
- 真实系统输入法、全部托盘/热键交互与完整 DPI 手测：未认证。
- 远端 GitHub CI、真实 Release/Issue/提交：未执行，仓库没有 remote 与 Git 身份。
- 代码签名：未配置，本地候选制品未签名。
- 最终分发授权与来源审阅：需维护者按实际制品确认；既有通知与新增 Inno 原文随包。

本地验证通过后仍交付候选版，不自动声明 `1.0.0` 稳定发布。源码、便携和安装程序的 SHA256 与证据在阶段交付记录中列出。
