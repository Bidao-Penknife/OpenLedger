# 阶段 10：Android 预览与本机回归

日期：2026-10-05。Windows 版本保持 `1.0.0rc1`；Android 版本为 `0.1.0-alpha1-preview`。本地构建和运行验收通过，GitHub 建仓仍需完成 CLI 浏览器登录。远端结果按交付记录更新，不以工作流配置代替执行结果。

维护者已授权直接新建公开仓库，并指定在当前 Windows 电脑测试。本期采纳阶段 9 的普通用户安装、升级、修复、恢复与卸载证据，不把无 Python 的独立 Windows 作为本次交付前提。既有阶段记录和 Windows 候选制品不覆盖。

## 实施结构

```text
src/openledger/mobile/bridge.py                  API v1 JSON、校验、共享用例
tests/integration/test_mobile_bridge.py           真实 SQLite 移动接口回归
android/app/src/main/java/org/openledger/android/ 原生界面、持久确认与写入队列
android/app/src/androidTest/                      APK 内真实 Python / SQLite 测试
android/app/src/main/assets/licenses/             运行时原文通知与来源摘要
scripts/build_android.ps1                        可复用构建与检查入口
scripts/verify_android_apk.py                     SDK 工具检查实际 APK
scripts/format_android.py                        固定 ktfmt 及下载摘要
.github/workflows/android.yml                    只读构建与制品保存
```

架构见 [ADR-019](../adr/019-android-shared-core.md)，安装与构建见 [Android README](../../android/README.md)。Android 在构建时直接选择共享源码的依赖集合，不复制金额、资金事务或规则解析。共享 Schema v1 的 SQL SHA256 仍为 `a369dc6c350105a8f2d00772132fb57cc31b38c8703def7409d2bf50cfea9347`。

## 实测结果

| 验证 | 实际结果 |
| --- | --- |
| Python 回归 | 1021 通过，0 失败、错误或跳过；201.63 秒；包含 33 项新增移动接口测试 |
| 移动接口 | 首次无假交易、解析只读、整数分、请求大小、严格 JSON、无 Qt 导入、并发/重启重试、错误回滚 |
| 代码质量 | Ruff lint 与 171 个 Python 文件格式通过；严格 mypy 检查 125 个源码文件通过；7 个 Kotlin/Gradle 文件格式通过 |
| Windows 源码 | 原生 Qt `windows` 启动通过；独立合成数据目录，没有读取日常账本 |
| Windows 安装 | 采纳阶段 9：便携合成记录、首次 9 组、升级 11 组检查通过；全 16 表与偏好保留，卸载项清理 |
| Android 构建 | Gradle 8.13、AGP 8.13.2、Kotlin 2.2.21、Chaquopy 17.0.0、JDK 17；公开 PowerShell 构建入口实际通过 |
| Android Lint | 0 错误、8 警告；包含固定工具/测试依赖版本提示和有意同步持久化确认命令的 commit 提示 |
| 实际 APK | 包名、版本、API 24/36、ARM64/x86_64、CRC、调试签名、16 KB 对齐检查通过；零应用权限、关闭云备份 |
| 设备测试 | 本机 WHPX Android 15 / API 35 / x86_64 合成模拟器，真实 APK 内 5 项测试通过；飞行模式 |
| UI 记账 | 明确创建合成现金账户，期初 1000 元；解析并确认咖啡 25 元；资产 975 元、月支出 25 元、流水 2 条（含期初） |
| 冷启动保留 | 结束该预览应用进程，确认进程退出，再重新启动；界面资产和月支出与已保存账目一致 |
| CI 静态检查 | actionlint 检查 Windows、Android 与发行工作流通过；未执行远端 CI |

桌面 Python 为 3.12.5、SQLite 为 3.45.3；Android 实际 SQLite 为 3.50.4。Schema、迁移与资金不变量通过各自运行时验证，未假设两个平台的 SQLite 二进制版本相同。模拟器使用本机已可用的 WHPX，没有启用额外 Windows 功能或重启系统。SDK/JDK/Gradle/CLI 工具均放在隔离工作目录，未纳入源码。

## 产物与范围

APK 包名 `org.openledger.android.preview`、版本 `0.1.0-alpha1-preview`，37,517,373 字节，SHA256 `5c81461b000036a64a7f49b825800fd300d84f882f15d2f111efa7b6b47f1112`。使用调试签名，证书 SHA256 `25c634b18e197e3df31abcd858134703296eb9d2520e39c27b30b1e45d2af704`；稳定发布签名尚未配置。最低 Android 7.0 / API 24，仅 64 位 ARM 与 x86。

Android 支持本地自然语言草稿、手工收支、账户/账本创建、流水搜索分页、总资产和本月收支、跟随系统明暗。解析后须确认，不读取微信、支付宝或银行通知。手机与桌面分别保存，本期没有云同步、手机备份、导入导出、交易编辑/删除、转账/退款表单、完整统计图表或 AI 配置。

运行时原文通知共 14 文件，与 APK 一起保存；固定下载材料带来源与摘要。Gradle Wrapper 分发包有固定 SHA256，依赖版本使用 lockfile；尚未声明所有 Gradle 传递依赖都经过独立下载摘要复核。应用卸载或清除数据会删除手机账本，移动备份上线前使用合成数据试用。

没有执行 ARM64 真机、Android 7 的设备运行、真实中文输入法长时间交互或跨端数据交换验收。已有安装数据的 UI 夹具会跳过，不能将跳过记为完整 UI 通过；本次使用全新模拟器，5 项均实际运行。确认命令恢复依赖持久 UUID 回执，额外的进程终止竞态与低存储压力测试可在后续扩大覆盖。

## 工作项

| 编号 | 本期状态 |
| --- | --- |
| OLG-039 | 本地源码审查与 Git 导入准备；远端建仓待 CLI 登录完成 |
| OLG-040 | 当前 Windows 验收采用、1021 项回归与原生源码启动通过 |
| OLG-041 | 共享 Python 移动适配、输入验证与 Schema 字节检查通过 |
| OLG-042 | 记账闭环、流水、资产和冷启动保留通过 |
| OLG-043 | 实际 APK 构建、静态检查与本机模拟器通过；真机待测 |
| OLG-044 | 后续里程碑：移动备份、编辑/删除、转账/退款、导入导出、图表与可选 AI |

GitHub App 当前支持已有仓库内容与 Issue 操作，未提供创建仓库工具；可用的 GitHub CLI 需要用户在 GitHub 完成浏览器设备登录。此前登录码已过期，账户验证与新仓库创建不能用旧码继续。用户已经授权建仓，缺少的是 CLI 的实际认证，不是重复请求建仓许可。未创建线上 Release 或稳定 tag。

交付目录保留 APK、完整源码、Git 离线快照、SHA256、两端验证记录与手机截图；具体 Git 提交、归档摘要、远端状态和证据索引见本阶段交付记录。证据只含合成账户、测试结果和截图，不含个人数据库、备份、凭据或签名私钥。
