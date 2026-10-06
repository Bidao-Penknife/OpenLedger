# OpenLedger

面向 Windows 的本地个人财务管理与智能记账桌面应用，使用 Python、PySide6 和 SQLite。项目优先保证资金记录的正确性、数据可恢复性和长期可维护性。

项目提供 Android `0.3.0-beta1-preview`：Kotlin 原生界面和同一套 Python 3.12 / SQLite 核心。新增支付通知待确认、离线中文收据 OCR、中文语音录入及文字回退、多币种账户与实际换汇；保留交易维护、完整备份、统计、CSV/Excel、PDF/PNG、附件和可选 AI。

Windows 配套版本为 `1.1.0rc1`，共享 Schema v2、币种精度、汇率、换汇和完整备份。Android 与 Windows 仍本地运行，云同步和插件市场未实现。开始使用请看[完整图文手册](docs/user/complete-manual.md)，手机设置中也可离线查看。验证记录见[阶段 12](docs/development/phase12-validation.md)。

[下载新版 APK、配套 Windows、使用手册和样例](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/android-v0.3.0-beta1)。公开 APK 与旧 alpha/beta 使用同一预览签名，可覆盖升级；保持预发布状态，真机和正式签名工作见 [#6](https://github.com/Bidao-Penknife/OpenLedger/issues/6)。

## 项目状态

阶段 1–7 已完成各自本地验收，历史结果保留在对应记录。发行候选的安装与升级见[安装指南](docs/user/installing.md)，首次使用见[启动指南](docs/user/getting-started.md)，日常录入见[记账指南](docs/user/bookkeeping.md)，报表见[统计与数据交换](docs/user/analysis-and-exchange.md)，可选联网功能见[系统与 AI 指南](docs/user/system-and-ai.md)。开发接口见[资金核心](docs/development/financial-core.md)与[扩展指南](docs/development/extensions.md)。

| 范围 | 当前状态 |
| --- | --- |
| 中文桌面界面、本地规则解析和手工收支 | 已接入可编辑草稿与明确保存 |
| 交易记录 | 账本/账户/类型/日期筛选、文本搜索、分页、编辑、删除及恢复 |
| 账户、账本、分类、标签与支付渠道 | 创建、编辑、归档/恢复；默认账本/账户和显式渠道映射 |
| 转账、退款、期初和余额校准 | 专用表单；资金操作经过统一事务与幂等回执 |
| 首页汇总与统计分析 | 总资产、本月收支；日期与资料筛选、月度趋势、分类占比、消费排行和前期比较 |
| CSV/XLSX 导入 | 列映射、只读预览、精确/疑似重复提示、明确勾选、原子整批提交和受约束的批次撤销 |
| CSV/XLSX 导出 | 一次数据库快照、币种最小单位整数字符串、安全可逆文本；不替代完整备份 |
| PDF/PNG 报告 | 使用当前统计 DTO；中文、多页 PDF、完整图片及超限提示、原子文件发布 |
| 浅色/深色/跟随系统与记账时区 | 主题即时切换；语言选择下次启动生效；设置与资金记录分离 |
| SQLite 数据库、迁移与审计 | 资金核心；金额以对应币种最小单位整数保存 |
| 一致备份与新目录恢复 | 服务接口与维护命令 |
| 类型检查、格式检查、测试和 CI 配置 | 工程骨架；运行结果以阶段验收记录为准 |
| Windows exe | PyInstaller `onedir` 发行候选；未签名 |
| Windows 安装程序 | Inno Setup 当前用户安装，固定 AppId；修复安装、升级与卸载保留账目；候选版未签名 |
| Android APK | 支付通知待确认、离线中文 OCR、语音及文字回退、多币种；保留原有功能；预览签名；Android 7.0 起，ARM64 / x86_64 |
| 多币种 | 固定 ISO 精度、原币账户、实际双端换汇、手动日期汇率、显示币种和缺失报价提示 |
| 单实例、托盘和全局快捷键 | 同一数据目录激活已有窗口；默认 Ctrl+Alt+L；关闭隐藏可配置 |
| 独立快速记账窗口 | Enter 解析、Ctrl+Enter 确认；失败保留，复用同一个资金 writer |
| 可选 AI | 用户自行配置兼容 API/模型/Windows 凭据；显式解析，只生成待确认建议 |
| 更新检查 | 配置真实 GitHub owner/repo 后手动检查稳定 Release；只提醒与打开页面 |
| 插件和同步接口 | API v1 显式内置注册与不可变 DTO；未实现市场或云传输 |

源码托管于 [Bidao-Penknife/OpenLedger](https://github.com/Bidao-Penknife/OpenLedger)，实际构建状态见 [GitHub Actions](https://github.com/Bidao-Penknife/OpenLedger/actions)。Windows `1.0.0rc1` 与 Android `0.1.0-alpha1-preview` 已于 2026-10-06 发布为[公开预发布 Release](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/v1.0.0rc1)，保持候选/预览状态。

[下载 Windows 安装程序、便携版与 APK](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/v1.0.0rc1)。Release 还保存对应提交的完整源码、Git 历史、验证报告、历史文档归档和 SHA256。公开历史归档排除了含账本备份的离线验收包；账本数据库、备份、凭据与签名私钥不随公开制品上传。APK 已实现范围、缺失功能及后续顺序见[Android 功能清单与路线](docs/development/android-roadmap.md)。

[历史 Android 0.2.0-beta1 APK](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/android-v0.2.0-beta1)。使用与旧公开 alpha 相同的预览签名和包名；覆盖升级保留账目。旧 Release 保持原制品，正式移动签名和 ARM64 真机验收继续作为稳定版里程碑。

本次交付按维护者要求采用当前 Windows 电脑验收，不以独立无 Python 系统为前提。环境覆盖范围与真实输入法等人工检查仍如实记录；候选版没有被声明为稳定 `1.0.0`。[清洁系统验收](docs/development/clean-windows.md)保留为更广泛发行的可选验证流程。

## 从源码运行

开发基线为 Windows x64 和 Python 3.12。项目使用 `uv` 管理环境和锁文件，当前工具基线为 `uv 0.10.4`。先安装 Python 与 uv，再在项目根目录执行：

```powershell
uv sync --locked --group dev --group build
uv run --locked python main.py
```

也可以使用模块入口：

```powershell
uv run --locked python -m openledger
```

安装完成后，激活虚拟环境也可直接运行 `python main.py`：

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

`--locked` 要求依赖声明与 `uv.lock` 一致，防止运行时静默更新依赖。uv 的安装方式见 [官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)。

## 数据位置

默认用户数据目录为 `%LOCALAPPDATA%\OpenLedger`。启动会创建或验证 `database/openledger.sqlite3`，建立“我的账本”、基础分类和支付渠道；不会生成示例交易或猜测账户余额。首次记账前，在“账户与管理”创建账户，明确填写起算日期和期初余额，包括余额为零的情况。可通过绝对路径覆盖：

```powershell
uv run --locked python main.py --data-dir D:\OpenLedgerData
```

不要将个人数据目录放入源码仓库。默认记账时区为 `Asia/Shanghai`，修改后未保存的自然语言草稿需重新解析，历史交易保留原时区。桌面偏好写入 `settings.json`，非敏感 AI 配置写入 `ai-settings.json`，更新仓库写入 `update-settings.json`，API 密钥只保存在 Windows 凭据管理器。这些设置与密钥不属于资金备份，恢复新目录后需要重新设置。

同一数据目录只运行一个桌面实例，重复启动激活已有窗口。用 `python main.py --quick` 打开快速窗口。默认主窗口关闭隐藏到可用托盘，托盘菜单、侧栏「退出」或 Ctrl+Q 明确退出；设置可修改关闭行为。托盘不可用时正常退出。启动诊断不启用真实热键或托盘，也不访问远端服务。

备份和恢复无需启动 Qt，恢复只写入新的数据目录：

```powershell
uv run --locked python main.py --data-dir D:\OpenLedgerData --backup D:\MyBackups\ledger.olbackup
uv run --locked python main.py --restore D:\MyBackups\ledger.olbackup --restore-to D:\OpenLedgerRestored
```

备份文件必须不存在，目标父目录必须已存在。恢复后关闭应用，再用 `--data-dir D:\OpenLedgerRestored` 启动；原账本保留。完整格式与校验说明见[备份恢复指南](docs/development/backup-recovery.md)。

## 开发检查

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

检查入口及贡献约定见 [开发指南](docs/development/getting-started.md) 和 [CONTRIBUTING.md](CONTRIBUTING.md)。测试结果应以本次实际运行的验收记录和 CI 日志为依据。

## Windows 构建

```powershell
./scripts/build_windows.ps1
./scripts/setup_inno.ps1
./scripts/build_installer.ps1
```

Windows 脚本同步锁定的构建环境，生成 PE 版本资源、执行 PyInstaller、收集许可证并生成 ZIP 与 SHA256。后两步在仓库内准备固定版本的便携 Inno 编译器，核对完整 bundle 后编译安装程序。单独调用 PyInstaller spec 前需要先运行 `uv run --locked --group build python scripts/release_tools.py prepare`。

采用 `onedir` 目录分发，以便验证 Qt 动态库、平台插件和许可证材料。分发时需要保留整个输出目录，不能只复制其中的 exe。普通权限的首次安装、升级、修复、备份恢复及卸载已完成本地验证；本次按维护者要求使用当前电脑验收，独立 Windows 可补充更广泛的发行覆盖。具体输出路径和检查步骤见[发布指南](docs/development/releasing.md)与[草稿发行工作流](docs/development/release-workflow.md)。

## 设计与路线图

源码按照以下职责划分，核心业务不依赖 Qt：

```text
src/openledger/
├── domain/          # 金额、交易与领域规则
├── application/     # 用例、DTO 与端口
├── infrastructure/  # SQLite、文件、平台与外部适配器
├── mobile/          # Android API v1 JSON 适配，共享资金服务
├── presentation/    # PySide6 窗口、模型与视图
└── bootstrap.py     # 程序入口与依赖组装
```

阶段 2 设计包位于 [docs/architecture/phase2](docs/architecture/phase2/)。设计原则包括：整数“分”保存金额，余额由有效账户流水计算，资金变更通过统一事务提交，自然语言与 AI 仅生成待确认草稿，AI 默认关闭。

阶段交付状态：

1. 阶段 5 已完成：本地规则、可编辑草稿和桌面记账。
2. 阶段 6 已完成：分析报表、CSV/XLSX 交换、批次撤销、PDF 和图片报告。
3. 阶段 7 已完成：桌面快捷入口、可选 AI、插件端口、系统主题和更新提醒。
4. 阶段 8 本地实施与验收已通过：Windows 安装程序、版本与校验清单、988 项测试及草稿发行工作流；稳定发行仍待独立 Windows 和维护者审阅。

完整任务拆分见 [开发路线图](docs/development/roadmap.md)。云同步和插件市场不属于首版交付；当前只预留接口。

阶段 6 的分析使用同一只读快照中的不可变报告 DTO：退款按到账日期统计，净支出允许为负，分类占比按毛支出计算。文件任务与资金 writer 分离，取消尚未提交的确认准备不会写入账目，已确认的资金事务完整完成。实现决策见 [ADR-016](docs/adr/016-analytics-and-file-exchange.md)。XLSX 使用 openpyxl 和 defusedxml；图表与 PDF/PNG 使用 Qt 原生绘图，无须额外报告运行库。

## 参与与许可

欢迎通过问题报告、文档修改、测试和小范围代码变更参与。提交前阅读 [贡献指南](CONTRIBUTING.md)；安全问题见 [SECURITY.md](SECURITY.md)。

项目原创源码采用 [MIT License](LICENSE)。PySide6、Qt 和其他依赖保留各自许可证，MIT 不改变其授权条件。请在重新分发前阅读 [第三方依赖说明](THIRD_PARTY_NOTICES.md) 及构建产物中的许可证材料。
