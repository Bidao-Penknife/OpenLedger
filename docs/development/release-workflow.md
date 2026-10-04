# 发行候选与草稿 Release

当前仓库没有 remote 或 Git 身份。本工作流已写入源码，尚未在 GitHub 执行；本地制品不表示已发布。

## 本地构建与核对

```powershell
uv sync --locked --group dev --group build
uv run --locked --no-sync python scripts/release_tools.py check-tag --tag v1.0.0rc1
./scripts/build_windows.ps1
./scripts/setup_inno.ps1
./scripts/build_installer.ps1
./scripts/verify_installer.ps1 -Installer ./dist/installer/OpenLedger-1.0.0rc1-windows-x64-setup.exe
uv run --locked --no-sync python scripts/release_tools.py collect --tag v1.0.0rc1
```

Windows 构建自动生成 PE 版本。单独使用 PyInstaller spec 前先执行 `scripts/release_tools.py prepare`，否则缺少 `build/release/windows-version.txt`。`setup_inno.ps1` 从官方固定下载验证 SHA256 与 Pyrsys B.V. 签名，以 `/PORTABLE=1` 解出仓库内工具，不配置系统关联、快捷方式或卸载项。编译时逐字节核对 20 个固定工具二进制和全部 bundle 文件，不依据缺失的 PE `FileVersion` 字段推断编译器版本。

源码清单包含已暂存和当前未暂存/新增文件；收集器拒绝私密格式、目录逃逸、符号链接和 Windows 文件名冲突。便携与安装资产使用源码版本的准确文件名，不选择目录中任意“最新”制品。`dist/release` 保存三类资产、独立 `.sha256`、SHA256SUMS.txt 和 release-manifest.json。归档可检查但不承诺压缩包字节级可复现。

## GitHub 工作流

维护者建立真实仓库后，先配置 `release` environment 的 required reviewers、允许发布的版本 tag 和仓库保护规则。仅写 `environment: release` 不会自动建立审核保护；未配置审核者时不能声称有人审查过制品。

准备经过审查的版本提交与 tag，例如 `v1.0.0rc1`。手动运行 “Build and prepare draft release”，输入准确 tag。只读 Windows job 检出 `refs/tags/<tag>`，校验源码版本、依赖、质量、全部测试、实际启动、便携包和安装生命周期，再上传制品与证据。Action 引用固定 commit SHA；工具固定 uv 0.10.4 和 Inno Setup 6.7.3。

写权限只在后续 draft job，并受 release environment 控制。该 job 下载同次构建资产，重新验证 SHA256SUMS，使用 `--verify-tag --draft` 创建草稿；PEP 440 候选/开发版加 `--prerelease`。发布 job 不检出或运行项目脚本，不覆盖已有 Release，不下载真实 AI 凭据，不自动标记正式发布。

审核具体制品、独立 Windows 结果、签名/第三方来源材料与发行说明后，维护者再发布草稿。未经明确指令，本地代理不推送 tag 或创建线上 Release。正式 `1.0.0` 需要完整门禁通过并重新构建，其 tag 与所有资产版本必须一致。

CI 安装测试使用 `-AllowElevatedRunner` 时会记录真实令牌；它不提供普通用户或干净系统认证。普通权限结果与独立 VM 记录另行保存。证据上传排除数据库、财务备份、运行时偏好和 AI 配置，只有检查报告、图像和合成交换/报告文件。

依据：[GitHub 工作流权限](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#permissions)、[环境保护](https://docs.github.com/en/actions/deployment/targeting-different-environments/managing-environments-for-deployment)、[GitHub CLI release create](https://cli.github.com/manual/gh_release_create)、[Inno 官方下载](https://jrsoftware.org/isdl.php)。
