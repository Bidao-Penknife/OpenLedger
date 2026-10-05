# 测试与验证

OpenLedger 使用 pytest、pytest-qt 与真实临时 SQLite。阶段 3–6 的测试继续作为回归基线；当前 `0.3.0.dev0` 新增 Windows 实例/热键、快速草稿、可选 AI、更新、主题、翻译与扩展契约测试，统一本地验收已通过（942 项测试）。实际数量、结果和制品见[阶段 7 验收记录](phase7-validation.md)，历史记录只对应各自快照。

## 基本检查

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

Ruff 检查风格与常见错误，mypy 检查类型边界，pytest 检查可观察行为。它们不能替代 Qt 实际启动、冻结资源验证或真实数据库事务测试。

## 启动与窗口验证

启动验证检查数据目录解析、资源位置、运行环境 DTO、SQLite 能力策略、Qt 窗口生命周期和进程退出。测试使用临时目录和合成账目，不读写默认用户数据。独立启动 smoke 还检查数据库完整性、默认账本，以及没有创建账户和资金交易；业务录入测试另行显式创建合成账户和余额。

界面测试可以使用 `QT_QPA_PLATFORM=offscreen` 进行控件断言；原生 Windows 平台插件的启动需要另做实际进程验证。offscreen 通过不能证明托盘、全局热键、高 DPI 或系统输入法已通过。中文输入法保护测试发送合成 `QInputMethodEvent`，验证组合输入期间不解析、不保存；它不构成微软拼音或其他真实系统输入法完整选词流程的认证。

进程验证入口为 `scripts/smoke_app.py`，Windows 辅助入口为 `scripts/smoke_windows.ps1`。脚本通过应用自身提供的诊断与自动关闭选项启动程序，确认退出码、启动结果及资源路径：

```powershell
uv run --locked python scripts/smoke_app.py --output-dir build/validation/source
./scripts/smoke_windows.ps1 -Executable ./dist/windows/OpenLedger/OpenLedger.exe -OutputDirectory ./build/validation/frozen
```

第二条命令需要先完成冻结构建。验证在独立的中文及含空格目录运行，Windows 下使用真实 `windows` 平台插件，检查原生窗口句柄创建与正常关闭，保存 JSON 和截图。应用的 `--smoke-test` 必须配合 `--smoke-report PATH`；这些选项供验证脚本使用，日常启动无需填写。输出记录在阶段验收材料中。它证明本机的 Qt 运行，不等于干净 Windows 虚拟机、高 DPI 全矩阵或全部输入法验收。

## 阶段 5 的业务与交互测试

本地规则测试使用冻结 ParseRequest 和注入的 reference_date，覆盖精确金额、中文/全角输入、原文 span、相对日期、精确时刻、DST fold/gap、多事件、金额/字段歧义和渠道与账户分离。LocalParser 本身不持有 LedgerService、writer、数据库连接或网络客户端；解析成功不能作为保存成功的证据。

桌面测试覆盖 Enter 仅解析、Ctrl+Enter 确认、IME 保护、重复提交、用户字段在重新解析时保留、原文/时区变化使草稿失效、多事件逐笔确认及已消费候选、缺账户、失败保留与重试、编辑版本冲突、删除恢复、归档引用和主题/时区设置。管理表单测试校验显式期初、默认替换、转账/退款/校准的专用 payload，根窗口测试进一步通过统一 bridge 提交到真实临时数据库，不以仅发出信号代替入账验证。

CommandBridge 测试检查 worker 提交、GUI 线程交付、重复阻止、相同意图失败重试复用 request_id、结果丢失后读取已提交回执，以及退出等待运行中的提交。设置测试验证有界读取、默认回退、原子替换、失败保留旧文件、临时文件清理，以及资金文件不受设置写入影响。

查询测试覆盖筛选、排序、分页、文字通配符转义、归档名称、转账两侧账户筛选、退款继承和实际到账月、删除恢复、同一读快照内的计数/行/change_seq，以及整数聚合。这些基础查询继续作为阶段 6 的统计回归基线。

针对单个模块可运行：

```powershell
uv run --locked pytest tests/unit/test_parsing.py
uv run --locked pytest tests/integration/test_queries.py
uv run --locked pytest tests/ui/test_bookkeeping.py tests/ui/test_management.py tests/ui/test_command_bridge.py
```

完整验收仍需要运行全部 pytest、质量检查、源码/冻结启动及实际便携 ZIP 解压验证。新增测试由常规 pytest 发现规则自动纳入现有 CI，不需要维护手工测试列表。

## 阶段 6 的统计、交换与报告测试

统计测试使用一次真实 SQLite 读快照，核对日期与各维度筛选、归档资料、退款发生日期和到账账户、继承分类/账本、空月补零、等长前期比较、零收入时不可定义的结余率和负净支出。转账、期初、校准及已删除记录不得混入收支；月份、分类、排行、名称与数据版本必须来自同一快照。累加保留 Python 整数，不依赖 SQLite `SUM` 的溢出行为或浮点金额。

交换测试覆盖 CSV 编码与引用、XLSX 工作表、ZIP 大小限制、公式拒绝、金额精度、字段映射、未知引用与名称冲突、外部交易号、精确及模糊重复、源文件/预览变化和退款依赖。确认后经统一资金命令整批提交，故障注入需要证明交易、流水、批次、审计及回执共同回滚。撤销检查成员版本、删除和批次外退款，导入身份在删除与撤销后仍保留；撤销成员不能绕过批次规则逐笔恢复。

文件与报告验证检查取消、旧结果、失败重试、临时文件清理、刷新同步和原子替换。特别覆盖确认 payload 已完成、Qt 交付尚未执行时的取消，确保没有提交资金命令；已经确认的资金提交不被文件取消中断。界面需要显示实际目标资料，不能只验证发出一个正确类型的信号。

图表测试检查浅深色、空数据、长中文名称、负净支出、精确值提示、分类键盘选择，以及按金额/笔数排行时实际条形几何和标注。PDF/PNG 使用当前报告 DTO，验证页码、筛选和版本、中文字体、长字段、多页、PNG 尺寸上限、句柄关闭，以及绘制/同步失败保留原文件。PDF 还需用独立渲染工具逐页检查布局；成功创建文件或能抽取文字不代表没有剪裁和重叠。报告样本使用合成数据，最终财务一致性证据应由真实临时数据库生成的 DTO 提供。

针对新增模块可运行：

```powershell
uv run --locked pytest tests/integration/test_analytics.py tests/integration/test_exchange.py tests/integration/test_import_batches.py
uv run --locked pytest tests/ui/test_analysis_page.py tests/ui/test_exchange_page.py tests/ui/test_task_bridge.py tests/ui/test_charts_reports.py
```

本阶段还需在实际冻结程序及便携解压目录验证统计、导入导出和报告依赖，重新核对 openpyxl、et-xmlfile、defusedxml 许可材料。全部检查通过后才更新阶段状态；单个测试模块通过不能代替统一验收。

## 资金核心与后续测试

阶段 4 覆盖真实文件迁移、写锁与读快照、各写入步骤回滚、幂等重放、退款依赖、期初切点、余额校准、软删除恢复、归档引用、完整备份与恢复失败。使用 92,234 笔合成收入验证 signed int64 边界后，再尝试增加 1 分，确认总资产越界时整次命令回滚。跨行完整性检查额外拒绝 SQL 约束不能发现的单侧转账和超额退款。

| 范围 | 当前边界与后续断言 |
| --- | --- |
| 数据库与资金 | 当前回归覆盖迁移、外键、事务回滚、转账守恒、期初、退款、软删除/恢复、版本与幂等性 |
| 解析与桌面 | 当前覆盖固定业务日期、金额/字段证据、多笔、确认录入、管理表单及 worker 桥接 |
| 查询与统计 | 已接入统一报告快照、历史筛选、趋势/分类/排行、前期比较、零分母、退款与整数聚合 |
| 交换与报告 | 已接入预览、重复标识、原子批次及撤销、CSV/XLSX 安全往返、PDF/PNG 一致性与失败保留 |
| 系统与网络 | 阶段 7：快捷键冲突、托盘退出、AI 超时/取消、过时响应、更新限流/离线 |
| 发行 | 当前验证本机完整便携目录和中文路径；干净 Windows、安装器、升级/卸载验收在阶段 8 |

资金集成测试使用真实临时 SQLite 文件，并在部分写入后注入失败验证完整回滚。固定时钟、UUID 工厂和合成数据，避免机器当前日期或个人账户影响测试。

## CI 与报告

GitHub Actions 配置以仓库中的 workflow 为准。Windows 任务配置了全部 pytest、检查、源码/冻结启动和便携包验证，权限限定为所需范围；PR 任务不自动发布，也不依赖私人凭据。实际远端执行以 [Actions 页面](https://github.com/Bidao-Penknife/OpenLedger/actions)为准，新增测试是否通过分别记录本地验收与实际 CI 日志。

本地通过、远端 CI 通过、打包成功与干净系统验证是不同证据，阶段报告分别说明。工作流存在或静态检查通过不能代替真正的远端执行。

## 阶段 7 系统与可选服务

单实例测试使用独立真实 QCoreApplication 子进程验证锁、消息、活跃持有者与 crash 恢复。热键测试使用短生命周期的专用组合，验证实际 RegisterHotKey、WM_HOTKEY Qt 原生分发和注销；不注入用户键盘输入。托盘与关闭策略用可控边界验证，无托盘不隐藏，显式退出等待资金回执。

快速窗口和主窗共用真实临时资金服务验证确认、不重复、失败保留、原草稿保持及未来词阻断。AI 模块使用假传输、本地 HTTP 与假 Win32 凭据接口，覆盖最小请求、大小、超时、取消、重定向、字段白名单、金额/日期/ID，以及 UI 修改后丢弃旧结果。不会读取真实用户密钥或调用真实远端模型。更新检查使用假 Release 传输验证稳定版本、URL、错误和取消，只允许显式打开页面。

翻译测试核对全部 tr 常量、静态动态标签、唯一 context/source、占位符、lrelease 确定性和实际英语控件。主题测试验证系统信号、人工覆盖及有效 QSS/图表主题，配置文件保持 system 选择。插件与 sync DTO 验证 API 版本、默认禁用、金额整数字符串及修改序号。

启动验证的 --smoke-features 在源码、冻结和实际解压包中加载 QM、解析隐藏快速草稿并运行离线 AI/Release 引擎，资金数据保持不变；禁用实际托盘和热键，不访问 Windows 凭据或网络。该离线诊断与专用真实 Win32/IPC 测试分别提供证据。

## Android 共享核心与设备测试

`tests/integration/test_mobile_bridge.py` 使用真实临时 SQLite 验证 JSON 契约、只读草稿、整数分金额、失败回滚和幂等重试。运行 `uv run --locked pytest tests/integration/test_mobile_bridge.py` 不需要 Android SDK。

Android APK 的内嵌 Python、Schema 资源和界面必须在设备上另验。`./scripts/build_android.ps1` 只构建与检查；设备执行使用 `./android/gradlew.bat -p android connectedDebugAndroidTest`。请使用新建的合成模拟器：已有账户或流水时 UI 测试跳过且不删除数据。核心设备测试只写独立缓存目录。模拟器通过与真机认证分别记录；当前结果见[阶段 10](phase10-validation.md)。
