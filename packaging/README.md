# Windows 构建

当前版本为 `1.0.0rc1`，Windows x64 便携及安装候选结果见[阶段 8 记录](../docs/development/phase8-validation.md)。冻结方式为 PyInstaller `onedir`，入口为 `main.py`；GUI 没有控制台窗口。应用资源通过 `openledger.resources` 收集，包括 QSS、SQL 和审核后的 QM；IANA 时区数据随 `tzdata` 打包，Qt DLL 保留为独立文件。

## 本地构建与验证

要求 Windows x64、Python 3.12 x64 和 uv 0.10.4。先在仓库根目录执行质量检查，再构建：

```powershell
uv sync --locked --group dev --group build
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/smoke_app.py --output-dir build/validation/source
./scripts/build_windows.ps1
./scripts/smoke_windows.ps1 -Executable ./dist/windows/OpenLedger/OpenLedger.exe -OutputDirectory ./build/validation/frozen
uv run --locked --no-sync python scripts/verify_windows_archive.py --archive dist/windows/OpenLedger-1.0.0rc1-windows-x64.zip --output-dir build/validation/portable
```

`build_windows.ps1` 使用脚本所在仓库作为根目录，所有输出位于仓库内 `build/` 和 `dist/`。它先同步锁定的开发和构建依赖，再检查解释器版本及架构，执行冻结、收集项目和第三方许可、生成 ZIP 与 SHA256；任一步失败即终止。构建脚本自身不运行 GUI 验证，需继续执行上述 smoke 命令。

`smoke_windows.ps1` 参数为 `-Executable`、可选 `-OutputDirectory` 和 `-Timeout`（默认 30 秒）。共享验证脚本从含中文及空格的其他工作目录启动目标程序，传入独立数据目录，使用原生 Windows Qt 插件进入事件循环、检查窗口句柄、写出 JSON 和截图、自动退出。启动只初始化资料，检查没有账户或资金交易。脚本成功退出才代表报告通过且程序正常关闭。

`verify_windows_archive.py` 检查实际 ZIP 的 CRC、路径、必要资源和许可证，以及迁移文件与源码的字节一致性；解压到独立中文/空格目录后运行同一启动验证并保存证据。这些检查与使用真实临时账目的桌面录入测试分别记录，不以空库启动替代业务验收。

产物目录：

```text
dist/windows/
├── OpenLedger/
│   ├── OpenLedger.exe
│   ├── _internal/                  # Python、Qt、应用资源与时区数据
│   ├── LICENSE
│   ├── THIRD_PARTY_NOTICES.md
│   └── licenses/                   # 构建时收集的第三方许可及清单
├── OpenLedger-1.0.0rc1-windows-x64.zip
└── OpenLedger-1.0.0rc1-windows-x64.zip.sha256
```

ZIP 名称读取应用版本，例中的 `1.0.0rc1` 为当前开发版本。运行时需保留整个 `OpenLedger` 目录，双击其中的 `OpenLedger.exe`。目录可放置在中文路径或包含空格的路径。

## CI 边界

`.github/workflows/ci.yml` 配置在 `push` 到 `main`、PR 和手动触发时使用 Windows 2025 x64 执行相同流程：质量检查、全部 pytest、源码启动、冻结、exe 启动与便携 ZIP 验证。新增测试自动发现。Action 使用完整 commit SHA，uv 固定为 0.10.4；仓库权限仅 `contents: read`，不配置任何私密凭据。验证证据和成功的构建 ZIP 保留 14 天，工作流不发布 Release。当前没有真实远端 GitHub CI 执行结果。

当前制品未签名。原生窗口测试不等于独立 Windows 或真实系统输入法验收；合成事件只覆盖组合输入保护。安装器已经接入，稳定发行与签名/授权审阅仍按实际记录推进。项目 MIT 只覆盖自研代码，第三方组件按各自许可分发。

安装器构建使用 `scripts/setup_inno.ps1` 和 `scripts/build_installer.ps1`，详见[发行工作流](../docs/development/release-workflow.md)。真正的安装、修复、升级、拒绝及卸载由 `scripts/verify_installer.ps1` 验证；[独立 Windows 入口](../docs/development/clean-windows.md)不用 Python。

## 官方参考

- [PyInstaller Spec 文件](https://pyinstaller.org/en/stable/spec-files.html)：冻结配置及可覆盖的命令参数。
- [PyInstaller Hook 工具](https://pyinstaller.org/en/stable/hooks.html)：收集包内数据文件。
- [uv 在 GitHub Actions 中的用法](https://docs.astral.sh/uv/guides/integration/github/)：锁定工具与依赖，核实当前官方 Action 的版本和 SHA。
- [GitHub Artifact 上传 Action](https://github.com/actions/upload-artifact)：构建产物上传与保存参数。

阶段 7 的构建入口执行 `scripts/update_translations.py --check --compile`，拒绝缺项或占位符错误。QM 翻译与离线系统引擎验证进入同一 smoke 入口；AI 密钥、用户配置或用户账目不进入制品。packaging 作为版本比较运行依赖保留精确许可证材料。托盘、快捷键只在正常交互启动时启用，smoke 使用假服务并核对不写资金。
