# 可选服务与扩展开发

阶段 7 将 Windows 桌面入口与可选 AI 接入同一财务边界。架构决策见 [ADR-017](../adr/017-desktop-and-optional-services.md)，资金事务与幂等回执见[资金核心接口](financial-core.md)。以下契约是当前代码接口，不代表插件市场、云同步或任意第三方模块加载已实现。

## 模块结构

```text
src/openledger/
├── application/
│   ├── dto/ai.py                  # 无密钥 AIConfig
│   ├── dto/parsing.py             # 修订号、证据与建议来源
│   └── ports/
│       ├── ai.py                  # AIProvider / CredentialStore
│       └── sync.py                # 同步 DTO 与预留 Protocol
├── infrastructure/
│   ├── ai.py                      # 有限 HTTP、响应验证、无密钥配置
│   ├── credentials.py             # Windows Credential Manager
│   ├── updates.py                 # 手动匿名 GitHub Release 查询
│   └── platform/
│       ├── hotkeys.py             # RegisterHotKey 与原生事件过滤
│       └── single_instance.py     # 数据目录锁与有限激活协议
├── plugins/
│   ├── contracts.py               # API 版本、Manifest、能力 DTO
│   └── registry.py                # 显式注册与选择
└── presentation/
    ├── appearance.py              # 偏好主题与有效主题分离
    ├── languages.py               # 创建窗口前安装翻译
    ├── desktop.py                 # 托盘与快捷键生命周期
    └── views/
        ├── quick_entry.py         # 独立草稿，共享资金命令桥
        ├── ai_settings.py         # 明确配置、启用与凭据操作
        └── updates.py             # 手动检查、取消、旧结果保护
```

## 单实例与 Windows 生命周期

`InstanceCoordinator(data_directory, parent=None)` 使用 `Path.resolve()` 与 Windows 路径大小写规范化作为身份依据，持有同目录 `QLockFile` 后才能初始化资金数据库。`acquire()` 返回 `False` 表示已有锁持有者；权限或监听失败通过 `LedgerError` 报错。只有持锁进程清理过期 IPC 端点。

`activate("show" | "quick", timeout_ms=1500)` 使用 `QLocalSocket` 发送一条动作并等待确认。服务端设置当前用户访问权限，限制消息长度、同时接入数和部分消息超时；每个连接独立缓冲，接受后只发出一次 `activationRequested(str)`。未知动作、额外数据和过长请求被拒绝。激活接口不携带交易字段，不调用 ledger，不提供资金写入权限。关闭协调器先停止接入，再释放锁；不能根据连接失败擅自删除活跃进程锁。

`GlobalHotkey` 使用线程级 `RegisterHotKey` 与 `MOD_NOREPEAT`，由 Qt `QAbstractNativeEventFilter` 接收 `WM_HOTKEY`，不安装键盘钩子。新组合先注册成功才注销旧组合；冲突保留旧绑定。`DesktopController.apply()` 独立应用托盘偏好，随后返回热键是否成功。关闭时显式注销热键并移除原生过滤器。

`QuickEntryWindow` 不创建写入器，`commandRequested` 交给主窗口现有 `CommandBridge`。两处草稿各有稳定交易 UUID；失败保留 UUID 与字段，成功只清理发起窗口。输入变化使解析失效，输入法组合状态禁止快捷确认。`command_finished(success, error_code=None)` 仅展示安全码，不包含 SQL、认证头或远端正文。关闭快速窗口只隐藏；主窗口显式退出等资金提交完成后才释放资源。自动启动诊断不启用托盘或默认热键，原生注册验证使用专门的短生命周期测试组合。

## AI 适配器

`AIProvider.parse(request, config, key, cancel=None) -> ParseResult` 只获取调用者明确提供的 `ParseRequest`、无密钥配置、服务密钥与取消条件。当前 `AIParser` 使用可注入 `AITransport.complete(endpoint, key, body, cancel)`，方便离线验证和替换兼容服务；没有数据库句柄、历史查询器或资金命令对象。

请求发送原文、参考日期、时区、当前账本 ID、人民币币种，以及可用账户/分类/支付方式 ID、名称、类型。`default_account_id`、渠道账户映射、余额、账目历史、审计和附件不发送。默认关闭，构造对象与保存配置不触发 HTTP；只有主窗口明确点击 AI 解析读取对应凭据并执行请求。快速窗口保持本地规则路径。

当前 HTTP 协议为非流式 Chat Completions 与 `response_format={"type": "json_object"}`，用户自行指定模型。响应大小、超时与重定向受限，远程需要验证 HTTPS；仅用户允许时支持回环 HTTP。错误响应正文不展示或记录，取消为协作式，阻塞 socket 仍受有限超时控制。实现自定义服务时保留这些边界，不把“兼容”等同于所有供应商支持 JSON mode。

响应首先验证 JSON envelope、完成状态、字段白名单、原文 span、金额精度、时间精度、未来日期、允许 ID 和分类类型，再构造不可变 `ParseResult`。建议来源为 `ai_suggestion`，没有保存权限；用户核对后经同一 ledger 命令保存为 `ai_assisted`。主窗口检查 draft ID、revision 和取消状态；原文、字段、配置、时区或解析方式变化使旧响应失效。插件不能绕过这些检查直接填充资金表。

`WindowsCredentialStore` 仅访问明确的应用凭据目标，不枚举用户凭据，没有明文文件回退。目标依据解析后的数据目录与基础 endpoint 生成 SHA-256，命名为 `OpenLedger/AI/<哈希>`；模型变化不会切换该服务凭据。配置 JSON 与密钥分别保存，配置写入失败时尝试恢复原凭据；失败状态由设置界面提示。恢复账本到新目录后需重新配置。

## 插件 API 版本 1

`PluginManifest(id, name, kind, version, api_version=1)` 验证稳定 ID、PEP 440 版本和能力。`kind` 支持 `ai`、`analytics`、`import`、`theme`。`PluginRegistry.register(manifest, adapter)` 只注册，不启用；`select(kind, id)` 明确选择，`select(kind, None)` 关闭。默认不选可选能力，也不扫描目录、导入外部 Python 或启动后台插件。当前内置 AI 兼容适配器从应用组装层明确注册。

| 能力 | 输入 | 输出 | 当前范围 |
| --- | --- | --- | --- |
| AIPlugin | ParseRequest、AIConfig、密钥、取消条件 | ParseResult | 内置兼容适配器与配置界面已接入 |
| AnalyticsPlugin | 已选定 ReportData、PluginContext | PluginInsight 元组 | 只预留建议契约 |
| ImportPlugin | FileTable、ExchangeMapping、PluginContext | PluginImportCandidate 元组 | 只预留候选契约，仍须正式导入校验与确认 |
| ThemePlugin | PluginContext | ThemePalette | 只预留颜色 token 契约 |

`PluginContext` 仅含应用版本、locale、时区。分析输入是用户选定的不可变报告，导入输入是已提供的表格 DTO，主题返回命名颜色 token；均没有自动数据库写入能力。若未来加载第三方 Python，需要另行设计信任和隔离流程。当前同进程 Protocol 是工程边界，不能阻止恶意 Python 通过自身权限访问进程或文件。

## 手动版本查询

`UpdateService.check(owner, repo, cancel=None)` 只查询用户明确配置的公开 GitHub latest Release endpoint。默认配置为空，不请求网络；设置页点击检查才保存配置并发起匿名请求。请求不带 Token、Cookie 或财务上下文，禁用环境代理路由、拒绝重定向并限制字节和时间。公司网络依赖代理时可能无法连接；当前没有代理设置功能。

使用 `packaging.version.Version` 比较版本，忽略 draft/预发布信息，并校验 Release URL 属于配置仓库。返回 `UpdateResult` 是通知 DTO，不是下载指令。改变 owner/repo 或取消后，旧结果不更新界面；只有另点发布页按钮才打开浏览器。测试使用可注入 `ReleaseTransport` 和合成响应，不声称远端服务或本仓库 Release 已发布。

## 外观与翻译

`ThemeController` 将 `light/dark/system` 偏好与实际浅/深主题分开，跟随系统监听 `QStyleHints.colorSchemeChanged`；人工选择不受系统变化影响。创建窗口前安装 `.qm`，保留 `.ts` 作为翻译源。界面文字使用 Qt `.tr()`，不要翻译存储的实体名称、备注或原文。语言选择下次启动生效，防止运行时重建表单丢草稿；英语 UI 不代表英语规则解析，图表内部绘制文案、报告和领域提示仍可能保留中文。

桌面偏好、AI 无密钥配置与更新仓库分别保存在独立小型原子 JSON 文件，不进入资金备份。配置文件无效时回到安全默认值；密钥仍通过 Windows 凭据存储取得。

## 未来同步

`application/ports/sync.py` 预留 `SyncChange`、`SyncEnvelope`、`SyncConflict`、`SyncResult`，以及 `CloudSyncPort` 与 `DataSyncPort`。同步 DTO 用递增 `change_seq`、实体版本和 `expected_version` 表达变更；金额字段以十进制整数字符串传输，冲突需要显式返回，不能默默覆盖。未来应用变更仍须复用 ledger 事务与审计边界。

目前没有同步 transport、云账号、服务端、登录 UI 或自动应用远端数据。预留契约不会在本地启动时连接云端。实现同步前需单独定义鉴权、设备身份、删除传播、加密和冲突策略，并增加真实事务与故障测试。

## 验证与官方资料

平台测试覆盖独立真实 Qt 子进程激活、活跃锁互斥、崩溃后过期锁恢复、部分 IPC 消息与超时、原生 Win32 注册/事件/注销、冲突保留旧热键及托盘不可用。快速窗口测试覆盖纯解析、单次确认、共享写入桥、失败重试、组合输入、草稿保留和时区失效。AI/版本查询使用离线可注入传输检查请求与错误边界；完整阶段统计与冻结程序验证由统一验收记录提供。

- [Qt QLockFile](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QLockFile.html)
- [Qt QLocalServer](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalServer.html)
- [Qt QAbstractNativeEventFilter](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractNativeEventFilter.html)
- [Win32 RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
- [Win32 UnregisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-unregisterhotkey)
- [Qt 系统主题](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QStyleHints.html)
- [Qt 翻译流程](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/translations.html)
- [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)
- [GitHub 最新 Release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)

发布仍是未签名的 Windows x64 开发包；正式安装程序、安装/卸载与干净 Windows 验收属于阶段 8，不以源码或本机测试替代这些检查。
