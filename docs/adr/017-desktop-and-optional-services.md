# ADR-017：桌面入口、可选服务与扩展契约

- 状态：已接受；阶段 7 统一验收另行记录
- 日期：2026-10-03
- 对应计划：OLG-025–030

## 问题与约束

桌面应用需要在常驻、快捷输入和异步服务之间保持同一资金边界。多个进程不能各自初始化同一账本；托盘不可用时不能把用户锁在不可见窗口；AI 和远端版本信息均不能取得财务写入权限。首次启动仍应完全本地，不需要账号、密钥或远端仓库。

## 决策

以解析后的绝对数据目录作为实例身份，Qt `QLockFile` 持锁后才初始化资金数据库。`QLocalServer` 只接受有限长度的 `show`/`quick` 消息和确认回复，拒绝未知/过长/超时消息，不接受交易内容。不同数据目录可分别运行，热键冲突按实际 Windows 注册结果提示。

Qt 托盘和 Windows `RegisterHotKey` 仅在正常交互启动时启用；测试和启动诊断不接管用户快捷键。偏好可关闭托盘、关闭隐藏或全局热键，快捷键支持 Ctrl/Alt/Shift 与字母、数字、F 键组合。快捷键换绑失败保留已有绑定，托盘设置独立应用。托盘不可用时关闭主窗口正常退出；「退出」和 Ctrl+Q 明确退出。运行中的资金提交先完成并交付回执，再注销热键和释放实例锁。

快速记账使用独立无模式窗口和独立未保存草稿，复用 `TransactionForm`、`LocalParser` 与主窗口的同一个 `CommandBridge`。Enter 解析、Ctrl+Enter 确认，与阶段 5 一致；修订阶段 2 快速窗口中 Enter 直接提交的规划，保留统一确认语义。Esc/关闭仅隐藏快速窗口，失败保留字段和稳定交易 ID，成功只清理该窗口草稿。解析器对未支持的未来词也给出阻断提示，不能默认为今天。

AI 配置默认关闭；用户自行填写兼容 API 基础地址和模型。点击主窗口的 AI 解析才发送当前输入、参考日期/时区、当前账本 ID 及可用账户/分类/支付方式名称和 ID，不发送资金历史、余额、附件或审计。适配器使用 Chat Completions JSON mode，支持替换 transport/provider，不绑定默认模型，不把「兼容」解释为所有厂商必然支持。响应必须通过本地金额、时间、ID、分类类型、span 与字段白名单验证，所有建议标记 `ai_suggestion` 并进入可编辑确认表单，最终明确保存标记 `ai_assisted`。原文/表单/配置变化、取消或先选择本地/手工解析后，旧 AI 结果不得覆盖草稿。

密钥只经 Windows Credential Manager 保存，目标名按数据目录和 API endpoint 命名。配置 JSON 不含密钥，构造界面不读取密钥；失败不展示远端响应正文、Authorization 或密码。HTTPS 验证、禁止重定向、响应大小限制和有限超时由 HTTP 适配器执行；本机 HTTP 必须显式启用且限回环地址。取消是协作式，正在进行的 socket 调用可能等待其超时，应用关闭先安全释放任务资源。

更新检查只在用户配置真实 GitHub owner/repo 并点击检查后调用匿名官方 latest Release API。默认仓库留空，不编造项目地址，不自动检查或下载。只比较稳定版本，校验项目归属和 release 页面 URL；用户另点「打开发布页」才打开浏览器。版本比较采用 `packaging.version.Version`，增加实际运行依赖及许可收集。

主题偏好 `light`/`dark`/`system` 与有效绘制主题分离；`QStyleHints.colorSchemeChanged` 更新跟随系统状态，人工浅/深选择不随系统变化。Qt `.ts`/`.qm` 资源支持简体中文与英语，语言选择在下次启动、创建控件前生效，避免重建界面丢失草稿。用户资料、中文解析原文、领域层校验说明和中文报告内容不翻译成新数据。

插件采用 API 版本 1 的 Manifest、不可变 DTO 和显式注册/选择。默认不启用可选能力，不扫描或导入外部 Python，不实现市场；当前 AI Provider 作为内置适配器注册。接口不传递数据库或 ledger 写入对象。这是可信适配器的工程边界，不是恶意 Python 的隔离沙箱。同步仅预留 `CloudSyncPort`/`DataSyncPort`、修改序号、金额字符串和冲突 DTO，没有网络实现或云端 UI。

## 后果与验证

桌面设置、AI 配置、更新仓库和凭据独立于资金数据库及备份；恢复新目录后需重新设置。未保存草稿只在本次运行保留。Windows 单实例/热键需要真实子进程和 Win32 原生事件验证，AI/更新用本地假服务覆盖请求与错误边界，不以虚构远端运行替代测试。源码、冻结目录和实际解压 ZIP 都检查翻译、插件、快速草稿与离线服务引擎；干净 Windows、真实输入法、安装与卸载仍属阶段 8。

依据：[Qt 系统外观](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QStyleHints.html)、[Qt 翻译流程](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/translations.html)、[Win32 RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)、[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)、[GitHub latest Release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)。
