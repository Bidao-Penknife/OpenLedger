# 开发环境与结构

当前基线为 Windows x64、Python 3.12 与 uv 0.10.4。源码采用 `src` 布局，需要先安装到项目虚拟环境，避免依赖当前工作目录解析包。

当前代码版本为 `1.0.0rc1`。阶段 8 增加当前用户安装器、版本资源、真实安装/升级/卸载验收及发行工作流；状态、实测环境与未执行门禁见[阶段 8 记录](phase8-validation.md)。阶段 7 的 942 项及更早记录只对应各自历史快照。

## 安装与运行

在仓库根目录执行：

```powershell
uv sync --locked --group dev --group build
uv run --locked python main.py
uv run --locked python -m openledger
```

第三行是第二种入口，无需同时启动两个进程。`main.py` 仅调用启动组装层，模块入口应调用同一实现。激活 `.venv` 后，也可直接使用 `python main.py`。

`dev` 组包含代码检查和测试工具，`build` 组包含 PyInstaller。锁文件使用 `uv.lock`；依赖声明和解析结果分别由 `pyproject.toml` 与锁文件保存。阶段 2 草案中的 `requirements.lock` 不作为当前维护入口。具体版本、SQLite 能力策略及变更理由见 [ADR](../adr/)。

日常运行和 CI 使用 `--locked`，不要用隐式更新绕过过期锁文件错误。需要调整依赖时，修改声明后执行 `uv lock`，审阅 diff，再重新同步与验证。单个依赖升级应明确写出包名和兼容性影响，避免无理由的整组升级。

## 目录职责

```text
OpenLedger/
├── src/openledger/
│   ├── domain/                 # 纯 Python 领域模型和不变量
│   ├── application/            # 用例、DTO 和协议端口
│   ├── infrastructure/         # 持久化、日志、路径及系统适配器
│   ├── presentation/           # Qt 窗口、表格模型及交互
│   ├── plugins/                # API v1 显式内置插件注册与选择
│   └── bootstrap.py            # 生命周期与依赖组装
├── tests/                      # 金额/解析单元、真实库集成、Qt 交互与进程测试
├── scripts/                    # 进程验证与 Windows 构建辅助
├── packaging/pyinstaller/      # 冻结配置与许可证收集入口
├── docs/architecture/phase2/   # 需求与设计包
├── docs/adr/                   # 已确认的设计决策
├── docs/user/                  # 使用文档
├── docs/development/           # 维护与发布文档
├── .github/                    # CI 与问题/PR 模板
├── main.py                     # 薄源码入口
├── pyproject.toml
└── uv.lock
```

目录代表职责边界，不表示其中所有业务功能已经实现。不要为填满目录而创建空泛抽象，接口随具体用例实施。

## 核心边界

领域层不导入 Qt、SQLite 或 HTTP 客户端。应用层定义 DTO、协议端口、纯解析和 FIFO writer；基础设施中的 LedgerService 协调领域校验与单次 SQLite 事务，LedgerQueries 组装只读快照。展示层确认草稿并组装命令，不直接写账户余额或拼装 SQL。当前桌面表单读取具体 LedgerService，CommandBridge 的提交边界依赖 LedgerPort；不声称完整查询/provider 插件端口均已实施。

Qt 控件在主线程使用。资金写入由 LedgerWriter 在专属 worker 中执行，数据库连接在执行线程创建并关闭；CommandBridge 通过 queued Qt signal 交付提交结果。自然语言草稿不持有 writer，必须经用户确认才生成命令。草稿标识与修订号防止旧结果应用，用户字段优先。具体实施差异见 [ADR-014](../adr/014-financial-command-boundary.md) 和 [ADR-015](../adr/015-local-drafts-and-desktop.md)；阶段 2 契约保留历史设计。

SQLite 版本号取决于所使用的 Python 发行环境，不能只根据 Python 主版本推断。能力探测使用独立内存连接；正常应用启动创建或验证资金数据库，初始化资料而不添加账户与资金交易。WAL 运行策略与数据库接入在阶段 4 完成，实际发行版需要按冻结程序里的 SQLite 核对。已经应用的迁移文件不能就地修改。

## 独立使用本地解析

ParseRequest 注入业务日期、时区和最小资料候选，ParseResult 返回字段来源、证据、替代值及问题。可在没有 Qt 或数据库连接的情况下调用：

```python
from datetime import date
from uuid import uuid4

from openledger.application.dto.parsing import ParseRequest
from openledger.application.parsing import LocalParser

request = ParseRequest(
    draft_id=str(uuid4()),
    revision=1,
    text="昨天咖啡25元",
    reference_date=date(2026, 10, 2),
    time_zone="Asia/Shanghai",
)
result = LocalParser().parse(request)
assert result.drafts[0].amount_minor.value == 2500
assert result.drafts[0].occurred_on.value == date(2026, 10, 1)
assert result.drafts[0].account_id.value is None
```

该示例没有账户或分类候选，因此只返回待补齐草稿，不能直接当作记账 payload。实际 UI 会提供活跃资料、默认项和明确渠道映射，显示来源后再确认。具体时刻的未来校验仍由资金服务在提交时完成。

主题与时区通过独立 SettingsStore 保存到数据目录的 `settings.json`，不包含资金或密钥，不进入资金备份；修改时区后需要使未保存草稿失效。初始 `Asia/Shanghai` 在界面显示并可修改，不能依据中文界面推断用户区域。

## 统计与文件交换边界

`application/dto/analytics.py` 定义不可变的筛选和报告快照，`infrastructure/analytics.py` 从一次只读事务组装总计、月份、分类、排行、前期比较、名称与数据版本。`presentation/charts.py` 及 `presentation/report_export.py` 消费同一 DTO，不在重绘或导出时重新计算财务聚合、重新查询数据库。只将归一化比例转换为绘图坐标；金额仍以整数分和精确字符串传递。

`application/dto/exchange.py` 保存源表、显式映射、逐行预览、重复身份和意图摘要。`infrastructure/exchange.py` 读取与校验有界 CSV/XLSX，确认前重新检查文件摘要；真正的写入由 `import.commit.v1` / `import.revert.v1` 经 LedgerWriter 和 LedgerService 提交。预览本身没有写权限，批次任一行违反约束整批回滚。普通交换不能恢复全部账本实体、审计与附件，完整恢复仍用 `.olbackup`。

文件与统计查询使用 `presentation/tasks.py` 的后台任务桥接，Qt 控件只在主线程访问。页面代次编号丢弃旧结果；取消确认准备时必须同时失效尚未送达 GUI 的 payload，已确认的资金事务完整完成。文件发布关闭工作簿/绘制设备后同步临时文件，再原子替换目标。

XLSX 使用锁定的 openpyxl、et-xmlfile 和 defusedxml，拒绝公式并关闭外部链接加载。PDF 使用 QtGui 的 QPdfWriter；不需要 QtPdf、Matplotlib 或 ReportLab。中文字体使用系统安装字体，Qt 将需要的字形嵌入报告，仓库和便携包不复制系统字体原文件。实施口径和格式决定见 [ADR-016](../adr/016-analytics-and-file-exchange.md)，使用步骤见[统计与数据交换指南](../user/analysis-and-exchange.md)。

## 日常检查

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

格式修改使用 `uv run --locked ruff format .`，完成后审阅 diff。更详细的测试范围见 [testing.md](testing.md)；冻结构建见 [releasing.md](releasing.md)。

构建输出、虚拟环境、个人数据和凭据不进入版本控制。实际测试与阶段验收记录需要标明环境和执行结果，不能以配置文件代替已运行的检查。

## 阶段 7 服务与翻译

同一数据目录的实例锁在资金初始化之前取得；托盘与全局快捷键只在正常交互启动时启用。AI Provider 仅接收用户明确选定草稿和最小资料；所有资金提交仍复用同一个 CommandBridge。插件与同步预留接口见[扩展开发](extensions.md)。包携带 `py.typed` 标记，供使用公共类型契约的维护者检查导入。

英语翻译目录随源码保留 `.ts` 与已编译 `.qm`，使用静态 AST 提取补足动态列名，避免执行界面代码：

```powershell
uv run --locked python scripts/update_translations.py
# 在 Qt Linguist 或文本编辑器审核新增 unfinished 译文
uv run --locked python scripts/update_translations.py --check --compile
```

`--check` 只读检查覆盖、重复、非空及格式占位符；`--compile` 调用环境内 lrelease。CI 和 Windows 构建执行检查与编译。语言选择下次启动生效，不重建当前表单；账本资料和中文解析原文不翻译。
