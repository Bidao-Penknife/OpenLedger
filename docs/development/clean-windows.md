# 独立 Windows 验收

本机没有可用的 Windows Sandbox，当前候选版的无 Python 清洁系统验收尚未执行。这个入口只需 PowerShell 5.1 和发行制品，不需要 uv、Python 或构建工具。

在无 Python 的 Windows x64 虚拟机或专用测试机，用普通用户登录，将安装 exe、便携 ZIP、校验值和源码中的 `scripts/verify_installer.ps1` 放到独立测试目录。脚本采用 UTF-8 BOM，以兼容 Windows PowerShell 5.1 的中文路径。不要在已有 OpenLedger 安装的日常用户账户运行；脚本检测固定 AppId 后会拒绝接管已有安装。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify_installer.ps1 `
  -Installer .\OpenLedger-1.0.0rc1-windows-x64-setup.exe `
  -OutputDirectory C:\OpenLedgerValidation `
  -RequireCleanSystem
```

脚本核对 Python 注册项和命令路径，创建独立中文/空格程序及数据目录，执行真实安装、同版本修复、活跃应用拒绝、原生窗口和离线引擎启动、备份/恢复及损坏/覆盖拒绝，最后卸载并核对资金、偏好、备份和未知文件保持。应用只使用 Windows 系统 PATH，默认数据目录通过子进程的独立 LOCALAPPDATA 注入。安装过程产生自己的测试卸载项，完成后移除；实际用户账目和凭据不作为测试输入。

`-PreviousInstaller` 可指定已审查的旧版本安装包，追加真实升级和降级拒绝。开发者的 `-SeedDirectory` 仅允许仓库 `build/validation` 内的合成账目；不要复制真实账目到 CI。脚本退出失败时尝试卸载本轮安装并保留证据，若仍有注册项应按记录中的确切程序路径卸载，不能删除其他安装。

GitHub Windows runner 可能具有提升的令牌；CI 显式使用 `-AllowElevatedRunner` 运行生命周期检查，其报告不会被记作普通权限或清洁系统认证，不能与 `-RequireCleanSystem` 同用。安装权限另由本地普通令牌测试和独立 Windows 验证确认。

自动脚本通过后还需在同一 VM 手动验证：100%/150%/200% 显示、微软拼音候选与组合输入、真实托盘和全局热键冲突、断网下本地记账、CSV/XLSX 导入预览、中文 PDF/PNG、重启后重新打开与升级/卸载时退出行为。记录 OS build、用户令牌、制品 SHA256、截图和每项结果；自动报告不替代这些交互认证。

稳定 `1.0.0` 发布前提交实际 `installer-check.json` 和手动记录。没有环境或结果时保持“未执行”，不可填写通过。
