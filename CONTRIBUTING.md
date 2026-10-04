# 贡献指南

感谢你帮助维护 OpenLedger。当前版本是 `1.0.0rc1` Windows 发行候选；请先阅读 [README](README.md)、[路线图](docs/development/roadmap.md)和[阶段 8 记录](docs/development/phase8-validation.md)，确认本地验证与稳定发行门禁。

## 开始贡献

项目尚未配置 GitHub remote，`OLG-xxx` 是本地计划 ID，不是 GitHub Issue 编号。正式托管后，可以从 Issue 模板提交问题和建议。在此之前，通过维护者正在使用的协作渠道提供可复现说明，不需要假设已有远端 Issue 或 Pull Request。

对于资金模型、数据库迁移、插件权限、发行方式等跨模块变更，先说明问题、预期行为和方案。文档纠错、已有缺陷修复和现有测试改进可以直接提出小范围补丁。每个变更围绕一个可审阅的问题，避免把格式整理混入业务修改。

## 本地环境

使用 Windows x64、Python 3.12 和 uv，依赖版本由 `uv.lock` 固定：

```powershell
uv sync --locked --group dev --group build
uv run --locked python main.py
```

完整环境和目录说明见 [开发指南](docs/development/getting-started.md)。不要提交 `.venv`、build/dist、个人账本、真实账单、账户截图、API 密钥或含有个人信息的日志。

## 代码约定

- 使用类型提示，为公共接口和不明显的领域规则添加文档字符串。
- `domain` 不依赖 Qt、SQLite或网络；`application` 定义用例和端口；基础设施实现适配器；Qt 控件仅在主线程使用。
- 金额、日期、账户引用和交易状态参考[阶段 2 历史设计](docs/architecture/phase2/)，现行实施以[ADR](docs/adr/README.md)和[资金核心接口](docs/development/financial-core.md)为准。金额运算使用整数分或受控 Decimal，不能通过浮点数修补资金错误。
- 错误需要保留可恢复的用户状态。资金变更必须原子提交，重试与重复请求不得重复记账。
- 用户可见字符串通过 Qt 翻译接口组织，界面文案与实现状态一致。不要将“即将实现”的功能呈现为已可用。
- 只记录诊断所需的信息，不在日志中保存凭据、账单原文或完整财务数据。测试样例使用合成数据。

## 验证要求

提交前运行：

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

涉及程序入口、资源路径、依赖或 Qt 生命周期时，还需要按 [测试说明](docs/development/testing.md) 验证实际进程启动；涉及打包时，按 [发布指南](docs/development/releasing.md) 验证冻结程序。

测试应覆盖可观察行为和失败路径。资金用例的测试需要检查余额、流水、回执和回滚结果；仅比较私有方法名称或复制实现分支不能证明正确性。设计文档或轻量文字修正可使用文档检查，无需为其新增业务测试。

## 依赖与文档

修改依赖时，说明用途、运行期或开发期归属、许可证、版本约束和兼容性影响，同时更新 `pyproject.toml` 与 `uv.lock`。不要手工编辑锁文件中的解析结果；使用 uv 重新锁定，再运行相关检查。

行为变化应同步修改使用文档，必要时记录 ADR，并在 [CHANGELOG](CHANGELOG.md) 的 Unreleased 项下补充用户可见变化。ADR 用于跨模块、难以撤回的设计决策，不用于记录每一次重命名。

## 提交说明与评审

PR 描述需要写清：具体问题、改动后的行为、实际运行的验证、已知限制。可以引用本地计划 ID，但不要把它伪装为远端 `#编号`。没有运行的测试明确标注原因；CI 文件存在不等于 CI 已通过。

提交标题使用简洁的动词，例如“修复非绝对数据目录错误提示”。不要求特定提交前缀，也不要求将无关修改压成一个大提交。

贡献的原创代码沿用项目 MIT 许可。引入第三方代码、字体、图标或样例前核对授权并保留声明；MIT 许可不会覆盖第三方组件。安全问题按照 [SECURITY.md](SECURITY.md) 处理。
