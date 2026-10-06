# 阶段 11：Android 本地财务 beta 功能与验收

日期：2026-10-06。Android `0.2.0-beta1-preview` / versionCode 2；Windows 保持 `1.0.0rc1`。维护者授权按既定路线连续补齐 APK，在当前电脑测试并上传。源码、工具链和历史发布制品保留。

## 完成范围

依次完成完整备份/持久恢复、交易与资金操作、资料与筛选、统计/表格交换/原生报告、可选 AI/Keystore、主题语言时区/更新/快捷入口，最后补入图片附件与扩展契约。具体界面操作见[手机指南](../user/android.md)，状态与剩余稳定版验收见[功能路线](android-roadmap.md)。

```text
src/openledger/mobile/
  bridge.py        API v1 输入/金额/时间/错误边界
  files.py         受限暂存、完整备份、持久恢复来源
  mutations.py     允许的共享资金与资料命令
  exchange.py      持久映射预览/决定、批次与导出
  reports.py       共享统计快照、精确金额文本
  attachments.py   受限图片、共享审计与备份
  updates.py       只检查本项目 APK Release
android/app/src/main/java/org/openledger/android/
  MainActivity / LedgerApplication
  TransactionActions / ManagementActions / AnalysisActions
  FileActions / ExchangeActions / AttachmentActions
  SettingsActions / SecureSecrets / AppPreferences / QuickEntry
  FinanceChart / ReportRenderer
```

核心资金、统计、备份与交换复用原 Python 实现。Kotlin 负责触控表单、SAF 字节流、凭据、系统入口和原生绘图；不重复实现资金计算。API v1 保留，Schema v1 原始 SQL 不变，附件使用原有表并进入相同事务、审计和 UUID 回执。

## 本机实际结果

| 项目 | 结果 |
| --- | --- |
| Python 全量 | **1067 通过，0 失败/错误/跳过**；Windows 原生 Qt；307.22 秒 |
| 移动新增测试 | 完整备份与缓存清理后的恢复、编辑/删除/转账/退款与资料、精确交换与损坏计划、可选 AI 与版本、图片/审计/备份迁移 |
| 质量 | Ruff lint 与 189 Python 文件格式通过；严格 mypy 139 文件通过；22 Kotlin/Gradle 文件格式通过；498 条 Qt 翻译覆盖/编译通过 |
| Android 构建 | 公开 `scripts/build_android.ps1` 实际成功，固定 Gradle/JDK/SDK 与原公开调试密钥 |
| Android Lint | 0 错误，15 警告、1 提示；包含固定工具依赖、英文复数、程序化图表构造器及拼接标签提示 |
| 设备测试 | **13 通过，0 跳过**；Android 15 / API 35 / x86_64，独立合成模拟器、飞行模式 |
| 真实 UI | 期初 1000、咖啡 25、编辑至 30、删除/恢复、转账 100、退款 5、收据附件；资产 975、净支出 25；主题、英语重建与快捷入口 |
| 报告边界 | 原生 PDF 可重新打开/渲染、PNG 成功；只有退款期间净支出 -5，占比为“—”，无浮点或空值崩溃 |
| 备份恢复 | 不覆盖原目录；完整格式桌面互通；引用图片完整迁移；持久保存选择后的恢复来源，缓存清理与重试通过 |
| 升级/冷启动 | 原公开 alpha 覆盖至最终 beta，签名相同；结束进程后再次启动，资产/支出一致，原合成数据库摘要不变 |
| APK 静态验证 | CRC、包名/版本、API 24/36、ARM64/x86_64、签名、ZIP 16 KB 对齐、不可变迁移通过 |
| 权限与隐私 | 仅 INTERNET；无广泛存储/通知/相机权限；系统云备份与设备迁移的所有数据域显式排除并在实际 APK 核对 |
| 资源与许可 | 230 对中文/英语资源；23 个原文许可/通知/来源材料；固定 CA 副本与新增依赖许可摘要通过 |

UI 夹具只在显式 `synthetic_device=true` 的合成设备运行，先恢复空备份到新目录，结束时切回原数据并核对原资产/变更序号；没有使用原非空安装跳过测试。共有资金确认队列绑定数据目录，恢复切换与清理待确认命令在同一偏好提交中完成。

最终 APK 为 `38,714,223` 字节，SHA256：

```text
38cd2bacf3386feacaee710082d52e2710fffe59727dd3810715c3c3f2a303f4
```

调试证书 SHA256 `25c634b18e197e3df31abcd858134703296eb9d2520e39c27b30b1e45d2af704`，与原公开 alpha 一致。Schema 原文 SHA256 `a369dc6c350105a8f2d00772132fb57cc31b38c8703def7409d2bf50cfea9347`。嵌入 HTTPS CA SHA256 `9102e6a3644a071ba6cdbd4a53698f291c4a64b18450a08bc046548b6db5cc8b`；CA 是 Chaquopy 的 certifi 副本，不假设使用 Android 系统 CA。

截图及原生 PDF 的首页渲染已检查中文、英语、金额和布局。文件交换测试在实际 APK 的 openpyxl/SQLite 运行时读写，未将仅编译测试 APK 当作设备通过。只读 JSON、严格类型/金额、失败回滚、版本与退款依赖仍由共享资金核心校验。

## GitHub 与交付

工作项为 [#7](https://github.com/Bidao-Penknife/OpenLedger/issues/7)、[#8](https://github.com/Bidao-Penknife/OpenLedger/issues/8)、[#9](https://github.com/Bidao-Penknife/OpenLedger/issues/9)、[#10](https://github.com/Bidao-Penknife/OpenLedger/issues/10)、[#11](https://github.com/Bidao-Penknife/OpenLedger/issues/11)。稳定版总项 [#6](https://github.com/Bidao-Penknife/OpenLedger/issues/6)保留真机、最低系统、正式签名及迁移工作。

[新版预发布](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/android-v0.2.0-beta1)提供 APK、完整源码、Git 快照、验证包、功能/使用文档与 SHA256。GitHub Actions 实际结果按[工作流记录](https://github.com/Bidao-Penknife/OpenLedger/actions)及交付清单中的具体提交/运行核对，不以配置文件代替成功结果。原 Windows/alpha Release 保持其旧二进制和历史证据。

公开证据只包含合成测试、摘要、截图和报告，不含账本数据库、`.olbackup`、API 密钥、签名私钥、SDK 或缓存。源码归档与 Git 内容核对，发布资产下载后校验 SHA256。

## 实际限制

本次实测为本机 x86_64 模拟器，未执行 ARM64 真机、Android 7 运行、真实中文输入法长期使用、OEM 设备迁移或低存储压力。最低系统与两种 ABI 为构建/静态检查结果。测试包继续使用调试签名，固定正式签名和预览迁移需要稳定版流程。

没有用真实用户 AI 凭据调用商业服务；受控传输检查禁用/缺失密钥、TLS 配置、建议校验及不发送历史/余额，实际 Keystore 与 CA 在 APK 内验证。AI 密钥与财务备份分离，系统备份/设备迁移使用[官方显式排除规则](https://developer.android.com/identity/data/autobackup)。手机/桌面独立保存，仅备份互通；云同步服务器、插件市场、支付通知读取、OCR 和语音仍未实现。原需求中的同步与插件 API 已预留。
