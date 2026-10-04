# ADR-018：当前用户安装与可审阅的发行候选

状态：阶段 8 采用。日期：2026-10-04。

程序使用固定 AppId 的 Inno Setup 6.7.3 安装器，默认写入当前用户的 `%LOCALAPPDATA%\Programs\OpenLedger`；资金数据仍写入 `%LOCALAPPDATA%\OpenLedger`。`PrivilegesRequired=lowest`，不开放全用户安装。卸载只移除安装器记录的程序文件、快捷方式和当前用户卸载项，保留数据、备份及 Windows 凭据。安装器不登记开机启动或文件关联。

升级前用户明确退出应用并保留已验证备份。所有应用和维护进程持有共享的 `Local\OpenLedger.InstallationGate` 命名对象，安装与卸载遇到活跃对象时拒绝操作；不强制终止、不请求重新启动。它与每个数据目录的单实例锁职责不同，不禁止运行多个账本目录。PE 版本和安装标记由源码版本生成；同版本修复可执行，较低版本不能覆盖已有较高版本。程序未知文件由用户保留，安装脚本没有递归清空目录规则。

发行版本先设为 `1.0.0rc1`。本地 Windows 11 普通权限安装和文件/数据验收不能代替无 Python 的干净系统或真实输入法手测。本机没有 Windows Sandbox，不擅自启用需要提升权限及重启的系统功能；提供不依赖 Python 的 PowerShell 验收入口和清洁系统记录模板。正式 `1.0.0` 需完成独立 Windows 验收、来源/签名与发布审核，候选版不假称稳定发行。

构建工作流使用读权限，核对 tag 与源码版本，生成准确源码、便携 ZIP、安装 exe、SHA256 和清单。单独的手动发行流程在指定版本 tag 上重新运行检查，先归档结果，发布步骤只创建草稿 Release；维护者审核具体制品后发布。仓库没有 remote 时不创建线上 Release、Issue 或提交。生产签名证书由维护者选择与保护，本项目不生成自签名文件冒充可信签名。

依据：[Inno 安装权限](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)、[非管理员安装模式](https://jrsoftware.org/ishelp/topic_admininstallmode.htm)、[安装参数](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)、[卸载参数](https://jrsoftware.org/ishelp/topic_uninstcmdline.htm)、[版本比较](https://jrsoftware.org/ishelp/topic_isxfunc_comparepackedversion.htm)、[GitHub Release API](https://docs.github.com/en/rest/releases/releases#create-a-release)。工具下载使用官方固定版本与 SHA256，并验证 Pyrsys B.V. 的 Authenticode 签名。下载与许可原文保留来源清单。
