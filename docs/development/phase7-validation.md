# 阶段 7 验收记录

验收日期：2026-10-03–2026-10-04。版本：`0.3.0.dev0`。状态：统一本地验收已通过，OLG-025–030 完成。

本记录只覆盖阶段 7 的本地系统集成与可选 AI，不代表已发布 GitHub Release、远端 CI、安装器或干净 Windows 验收。阶段 6 的 660 项测试和制品记录作为历史基线保留。

## 交付范围

| 计划 ID | 行为 | 主要验证 |
| --- | --- | --- |
| OLG-025 | 数据目录单实例、有限激活、托盘与可配置热键 | 实际子进程、live/crash lock、Win32 注册/事件/注销、冲突保留、关闭策略 |
| OLG-026 | 独立快速窗口、同一个资金 writer、明确确认和失败保留 | Enter 不写、Ctrl+Enter 单次保存、IME、主草稿保留、失败幂等、退出等待回执 |
| OLG-027 | 默认关闭的兼容 AI、Windows 凭据、最小请求与严格草稿 | 假 HTTP/凭据、响应大小/重定向/超时、金额/日期/ID、取消与过期结果、资金来源 |
| OLG-028 | 有效主题分离、系统变化、Qt 翻译目录 | 系统信号、人工覆盖、QSS/图表、英语下次启动、资源与 placeholder |
| OLG-029 | 配置真实 owner/repo 后手动检查稳定 Release | 版本、URL、无更新/404/限流/断网/取消、不自动下载或打开浏览器 |
| OLG-030 | API v1 插件显式注册、同步 DTO 和冲突端口 | 默认关闭、manifest 版本/重复、不可变 DTO、精确金额字符串和序号 |

架构与实施决定见 [ADR-017](../adr/017-desktop-and-optional-services.md)，使用见[系统与 AI](../user/system-and-ai.md)，接口见[扩展开发](extensions.md)。原始设计包和 `0001.sql` 不改写。

## 代码结构

```text
src/openledger/
├── application/dto/ai.py
├── application/ports/{ai,sync}.py
├── infrastructure/{ai,credentials,settings,updates}.py
├── infrastructure/platform/{single_instance,hotkeys}.py
├── plugins/{contracts,registry}.py
├── presentation/{appearance,languages,desktop,system_smoke}.py
├── presentation/views/{main_window,quick_entry,ai_settings,updates}.py
└── resources/translations/openledger_en_US.{ts,qm}
```

## 统一检查

| 检查 | 实际结果 |
| --- | --- |
| Ruff lint / formatter | 全部通过，150 个 Python 文件格式通过；凭据适配模块显式纳入源码发现 |
| strict mypy | 114 个源文件通过 |
| 全部 pytest | 942 通过，0 失败/错误/跳过；408 unit、346 integration、188 UI；CLI 总耗时 219.91 秒 |
| 新增范围 | 在阶段 6 的 660 项基线上新增 282 项；目录修订后另复跑 10 项翻译测试 |
| 原生平台 | 实际 RegisterHotKey 注册/WM_HOTKEY Qt 分发/注销；真实 Qt 子进程单实例、活跃锁及崩溃恢复通过 |
| 源码/冻结/实际 ZIP 解压启动 | 均使用 Windows 平台、真实 HWND、正常退出；独立空库无虚构资金记录 |
| 离线引擎 | QM、插件、快速草稿、AI 假建议和 GitHub 假 Release；不访问网络/真实凭据/实际托盘热键，不改变资金 |
| 翻译 | 498 条 finished、0 unfinished；占位符、动态列名、QComboBox 上下文与确定性 QM 通过；英语侧栏和按钮经截图修正 |
| 实际桌面 | AI 显式草稿与确认、快速共享 writer、主草稿保持、失败回滚及重试回执、六张原生浅深/中文/英语截图 |
| 冻结已有数据与恢复 | 新阶段 AI 来源与原文、旧统计/导入批次、CSV/XLSX、全部行数据/DDL/迁移/余额和回执保持；拒绝覆盖与损坏/篡改备份 |
| 资源与许可 | SQL 原始摘要不变，Qt 56 份原始通知逐字节核对，11 项依赖实际许可随包收集 |

新增测试由 pytest 自动发现，CI 配置增加翻译检查与编译，并沿用质量检查、源码/冻结/便携验证。未配置 GitHub remote，未执行远端 CI、提交、线上 Issue 或 Release。

## 制品与边界

本阶段提供 `OpenLedger-0.3.0.dev0-source.zip`、`OpenLedger-0.3.0.dev0-windows-x64.zip`、对应 `.sha256` 和验证证据。最终文件大小、SHA256 和启动报告集中记录在交付目录的 `OpenLedger-阶段7-验证结果.json` 与系统集成验证记录中。源码 ZIP 包含本阶段新增模块、测试、文档和翻译；wheel/sdist 另作资源完整性检查。

便携包完整解压后运行 `OpenLedger/OpenLedger.exe`，保留整个目录。源代码可执行 `uv sync --locked --group dev --group build` 后 `uv run --locked python main.py`，或激活虚拟环境运行 `python main.py`。首次使用先创建账户并明确期初余额与起算日期。

桌面验收从 1000.00 元合成期初开始，AI 只生成 128.00 元草稿时余额不变；确认、快速窗口及一次失败后幂等重试完成后资产为 842.00 元。4 条交易、4 条流水、16 条回执和 17 条审计保持；冻结恢复核对全部 16 表及 AI 来源/原文。旧合成库的 10815.00 元余额、10 条历史交易和已撤销导入批次也保持。所有账目均为合成数据，验证材料不包含数据库或备份档案。

只使用指定临时目录；没有读取真实用户账目/凭据，也没有访问真实 AI 服务或真实 GitHub Release。真实远端协议兼容和干净系统仍需后续验收。

未实现安装器、代码签名、自动下载、插件市场或云同步。测试使用本地假服务与原生 Windows API，不等同于某个远端供应商或真实系统输入法完整验收。语言选择下次启动生效，报告与领域说明仍保留中文。
