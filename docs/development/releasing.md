# Windows 构建与发布

当前版本为 `1.0.0rc1`，提供 Windows x64 onedir 便携包和当前用户安装 exe。阶段 8 实测结果见[本地发行验收](phase8-validation.md)，更早记录保留历史快照。制品未签名，无 Python 的独立 Windows 验收另见[清洁系统入口](clean-windows.md)，未执行项不能用于稳定发布声明。

## 环境与检查

在 Windows x64、Python 3.12 x64 和 uv 0.10.4 环境中，从仓库根目录执行：

```powershell
uv sync --locked --group dev --group build
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/smoke_app.py --output-dir build/validation/source
```

当前尚未建立远端仓库，不能将本地检查说成 GitHub Actions 运行结果。锁定 Python 主次版本与依赖有助于控制环境，但不承诺 ZIP 字节级可复现；记录实际解释器、Qt、SQLite、PyInstaller 和 uv 版本。

## 构建 Windows 候选制品

```powershell
./scripts/build_windows.ps1
```

脚本同步 `uv.lock` 中的 dev/build 环境，核对解释器和架构，冻结程序，收集项目与依赖许可证，最后生成 ZIP 和 SHA256。中途失败会终止；构建脚本不自动运行 GUI 验证。

需要单独检查 PyInstaller 时使用：

```powershell
uv run --locked --group build python scripts/release_tools.py prepare
uv run --locked --group build pyinstaller packaging/pyinstaller/OpenLedger.spec --noconfirm
```

该命令只有冻结步骤，不能代替完整构建脚本的许可证收集和 ZIP 输出。`uv run` 会同步所需依赖组，因此必须明确选择 `build`，避免构建工具被环境同步移除。

完整脚本输出为：

```text
dist/windows/
├── OpenLedger/
│   ├── OpenLedger.exe
│   ├── _internal/
│   ├── LICENSE
│   ├── THIRD_PARTY_NOTICES.md
│   └── licenses/
├── OpenLedger-1.0.0rc1-windows-x64.zip
└── OpenLedger-1.0.0rc1-windows-x64.zip.sha256
```

ZIP 文件名读取源码版本，上例为当前版本。exe 需要相邻的 `_internal`、资源和许可证文件；分发、移动或解压时保留整个目录。用户数据默认写入 `%LOCALAPPDATA%\OpenLedger`，不能写入安装目录或构建目录。

## 验证冻结程序

```powershell
./scripts/smoke_windows.ps1 -Executable ./dist/windows/OpenLedger/OpenLedger.exe -OutputDirectory ./build/validation/frozen
```

也可直接使用共享 Python 验证入口：

```powershell
uv run --locked python scripts/smoke_app.py --executable dist/windows/OpenLedger/OpenLedger.exe --output-dir build/validation/frozen
```

验证在独立中文/空格目录启动，使用实际 Windows Qt 平台插件，进入窗口事件循环，记录原生窗口句柄、资源与运行环境，保存截图后自动关闭。成功还需要报告字段、退出码和截图齐全，不能仅以子进程被创建判断通过。独立数据目录必须只含初始资料，不自动生成账户或资金交易。Windows 辅助脚本支持可选 `-Timeout`，默认 30 秒。

还需验证实际分发的 ZIP，而不只验证构建目录里的 exe：

```powershell
uv run --locked --no-sync python scripts/verify_windows_archive.py --archive dist/windows/OpenLedger-1.0.0rc1-windows-x64.zip --output-dir build/validation/portable
```

该步骤检查 ZIP 路径与 CRC、资源/许可证及打包的迁移文件字节，在独立中文/空格目录解压后启动其程序并保存证据。源码、冻结目录和解压包的结果分别记录。

这种验证证明当前机器能够运行冻结程序，不能替代不安装 Python 的独立 Windows 验收。Qt 交互测试里的合成 QInputMethodEvent 只验证组合输入保护逻辑，也不能代替真实系统输入法全流程。阶段 8 已验证普通权限安装、升级/卸载、恢复和 Qt 有效缩放，独立环境与真实系统交互仍按[验收入口](clean-windows.md)补齐。

阶段 6 增加统计及文件依赖，需要在冻结目录和实际便携解压目录使用隔离的合成账目验证：同一统计 DTO 的页面/报告口径、CSV/XLSX 导入整批提交与撤销、金额和关联字段导出、中文 PDF 多页及 PNG 完整性、取消和失败后原数据保留。仅执行初始空库启动不足以验证这些功能。报告 PDF 用独立渲染工具逐页检查，代码与字体存在也不能代替视觉验收。

## 许可证与制品记录

随包保留项目 LICENSE、THIRD_PARTY_NOTICES 和构建收集的依赖许可证材料。核对实际打包的 Python、PySide6、Shiboken、Qt DLL/插件、tzdata、openpyxl、et-xmlfile、defusedxml、packaging 及间接组件，确认材料与精确版本对应。openpyxl 与 et-xmlfile 的材料使用 `LICENCE` 拼写，et-xmlfile 同时包含 Python 代码许可，收集时不得漏掉；defusedxml 使用 PSF 许可原文。许可证收集成功不等于最终授权审阅完成，具体要求见 [第三方说明](../../THIRD_PARTY_NOTICES.md)。

报告使用 QtGui 的 QPdfWriter，不启用 QtPdf/QML，也不引入 Matplotlib 或 ReportLab。中文字体从用户系统选择并由 Qt 嵌入生成的 PDF；应用制品不携带系统字体原文件。统计与交换边界见 [ADR-016](../adr/016-analytics-and-file-exchange.md)。

交付时记录源码状态、版本、锁文件、运行库版本、检查结果、制品文件名与 SHA256。SHA256 用于核对下载完整性，本阶段没有代码签名或已建立的发布身份。

## GitHub Actions 与后续正式发布

当前 CI 配置了 Windows 质量检查、全部 pytest、源码启动、冻结程序/便携 ZIP、实际安装生命周期验证和制品归档，不创建 Release。单独的手动发行工作流核对准确 tag，并在受保护环境审核后创建草稿；配置、权限和安装构建命令见[发行工作流](release-workflow.md)。新增测试由 pytest 自动发现，Action 引用固定 commit SHA；当前没有远端 CI 已运行结果。

正式发布前需确定真实 owner/repo、版本标签与权限边界，完成独立 Windows 验收和最终发行审阅。CHANGELOG、候选截图、第三方通知及校验和随本次交付；维护者核对具体制品后再发布草稿。当前不填充虚构下载链接或“最新版”徽章。

构建脚本及 CI 参数的具体说明见 [packaging/README.md](../../packaging/README.md)。

阶段 7 构建先验证并编译已审核的英语 TS；QM 必须随 package resources、wheel/sdist 和便携目录保留。源码、冻结和解压 smoke 均验证 QM 与离线 AI/Release 引擎，真实服务访问不属于构建验证。启动诊断不注册用户热键或显示托盘。`verify_windows_archive.py` 还逐字节核对已审核 QM 与不可变 SQL。系统入口、可选联网及扩展边界见 [ADR-017](../adr/017-desktop-and-optional-services.md)。
