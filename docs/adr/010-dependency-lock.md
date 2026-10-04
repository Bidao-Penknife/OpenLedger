# ADR-010：使用 uv.lock 与隔离虚拟环境

状态：阶段 3 已采用。日期：2026-10-02。

## 决策

阶段 2 目录草案中的 `requirements.lock` 改为 uv 原生的 `uv.lock`，由工具生成并纳入版本控制。`pyproject.toml` 保存依赖范围和工具配置，锁文件保存精确解析版本及下载摘要；不手改锁文件。以 CPython 3.12 x64 为当前源码与 Windows 构建基线，使用项目 `.venv`，不向全局解释器安装应用依赖。

运行依赖目前只有 PySide6 与 tzdata。`dev` 组包含 Ruff、mypy、pytest 和 pytest-qt；`build` 组包含 PyInstaller。资金核心、图表、Excel 和可选 HTTP 依赖在对应阶段引入。版本唯一来源为 `src/openledger/_version.py`。

构建先 `uv sync --locked --group dev --group build`，执行打包时明确 `--group build`。冒烟脚本在同步完成后使用 `--no-sync`，避免运行命令改变已准备环境。CI 固定 uv 0.10.4；Python 小版本、操作系统和构建后端仍须记录实际值，锁文件不表示二进制逐字节可复现。

## 后果与验证

贡献者需要安装 uv 和符合范围的 Python。升级依赖必须更新锁文件，并重新验证质量检查、源码启动和冻结启动。开发测试依赖不应全部打入 exe；许可证收集保留实际分发组件及构建工具的相关通知。

当前实际依赖版本见阶段 3 验证记录与 `uv.lock`。使用该锁文件保证依赖选择一致，不扩大为所有 Windows 版本的兼容声明。

依据：[uv 项目与锁文件文档](https://docs.astral.sh/uv/guides/projects/)。
