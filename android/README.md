# OpenLedger Android 0.2.0-beta1

中文 Android 手机应用，与 Windows 版本共享 Python 3.12 记账核心、SQLite Schema v1、中文规则解析和资金事务。本期使用 Kotlin 原生界面，技术依据见 [ADR-019](../docs/adr/019-android-shared-core.md)。

## 已实现功能

- 自然语言解析为可编辑草稿，确认后记账。
- 收入、支出、日期、时间粒度、分类、账本、账户、支付方式、备注、对象、商户与地点。
- 交易详情、版本校验编辑、软删除与恢复；转账、关联原支出的退款、期初余额修改与余额校准。
- 账户、账本、分类、标签、支付渠道创建/编辑/归档；默认项与渠道账户绑定。
- 流水搜索分页及账本、账户、分类、标签、类型、日期与删除状态组合筛选。
- 月度趋势、分类占比、消费排行、等长前期比较；完整 PDF 与 PNG 报告。
- CSV（UTF-8/GB18030）及 XLSX 列映射、工作表选择、预览、重复提示、逐行勾选、整批提交和批次撤销；CSV/XLSX 导出。
- 系统文件选择器中的完整 `.olbackup` 备份；恢复到新目录，保留原数据并支持切回；共享桌面备份格式。
- PNG/JPEG/WebP 收据附件，预览、删除/恢复、完整备份迁移。
- 可选兼容 AI 草稿解析、Android Keystore 加密密钥、手动 GitHub APK 更新提醒。
- 中文与英语界面、浅色/深色/系统主题、可选 IANA 时区；桌面快捷方式与快捷设置磁贴。
- 已确认请求在进程重启后可恢复，UUID 回执防止重复扣款。

Android 版本为 `0.2.0-beta1`，versionCode 为 2。APK 包名仍为 `org.openledger.android.preview`，公开测试包继续使用原调试签名。最低 Android 7.0 / API 24，支持 `arm64-v8a` 和 `x86_64`，不支持 32 位手机。

手机与桌面各自本地保存。AI 默认关闭，不绑定服务；只有明确点击 AI 解析或检查更新才联网。APK 仅申请普通 `INTERNET` 权限，文件读写使用系统选择器，不读取支付通知。卸载或清除应用数据仍会删除账本，请先导出完整备份。实际云同步与插件市场按原需求留待扩展。

## 下载与功能路线

[新版 APK 下载](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/android-v0.2.0-beta1)。原 alpha 与 Windows 版本保留在 [v1.0.0rc1](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/v1.0.0rc1)。操作方法见[手机使用指南](../docs/user/android.md)，功能与剩余兼容性工作见[Android 路线](../docs/development/android-roadmap.md)。

## 构建

需要 Python 3.12、JDK 17、Android SDK Platform 36 / Build Tools 35.0.0。SDK 可使用 Android Studio 内的安装版本或官方命令行工具，不需要 NDK。Gradle Wrapper 固定 8.13 并校验下载摘要。

在项目根目录运行：

```powershell
uv sync --locked --group dev
$env:JAVA_HOME = 'C:\Path\To\jdk-17'
$env:ANDROID_HOME = 'C:\Path\To\Android\Sdk'
./scripts/build_android.ps1
```

或者在 `android` 目录执行：

```powershell
$env:OPENLEDGER_BUILD_PYTHON = 'C:\Path\To\python.exe'
./gradlew.bat --no-daemon assembleDebug assembleDebugAndroidTest lintDebug
```

输出：`android/app/build/outputs/apk/debug/app-debug.apk`。脚本核对签名、两个 ABI、权限、版本和原始迁移摘要后，将预览 APK 复制到 `dist/android`。私钥、SDK 位置、Gradle 缓存与个人账本不会进入 Git。

## 安装与验证

把 APK 复制到符合要求的手机，允许该次安装来源后安装；也可用已配置的 ADB：

```powershell
adb install -r dist/android/OpenLedger-0.2.0-beta1-android-preview.apk
```

第一次创建一个合成现金账户，设置余额和起算日期，然后输入“咖啡25元”，核对账户与分类后保存。重启应用核对余额及流水。支付渠道与资金账户是分别确认的字段，不隐式认为“微信支付”必然从某个账户扣款。

设备测试在独立合成目录调用 APK 内真实 Python / SQLite：

```powershell
./android/gradlew.bat -p android connectedDebugAndroidTest
```

共享核心测试只写入独立临时目录。UI 测试需要显式参数 `synthetic_device=true`，仅用于新建的合成模拟器：先将空备份恢复到测试目录，结束时切回原目录，检查原数据未改变；未显式授权时跳过 UI 测试。不要对日常手机传入该参数。编译测试 APK 与执行设备测试是不同结果，必须区分成功、失败与跳过。

本期验收见[阶段 11 记录](../docs/development/phase11-validation.md)，历史 alpha 验收保留在[阶段 10](../docs/development/phase10-validation.md)。本机模拟器为 Android 15 / x86_64，未将其结果记为 ARM64 真机认证。固定正式签名、Android 7 设备运行、真实中文输入法及低存储压力仍需稳定版验收。

## 开发边界

`src/openledger/mobile` 将 JSON API v1 转为共享资金、统计、交换与备份用例；Kotlin 负责触控表单、系统文件流、Keystore 和绘图。金额以整数分或十进制文本跨接口，禁止浮点账务。AI Provider 可通过组合根显式注入，`plugins` 的 AI/分析/导入/主题契约与 `application/ports/sync.py` 同步端口随 APK 源码闭包保留；当前没有扫描外部代码或自动应用远端变更。共享 Schema v1 不变，附件操作使用已有附件表并写入审计与 UUID 回执。

HTTPS 使用 Chaquopy 的 certifi CA 副本，构建检查核对其摘要；密钥按数据目录与服务地址隔离，财务备份不包含密钥。新增依赖的原始许可证与来源摘要见 `app/src/main/assets/licenses/mobile-feature-sources.json`。

系统云备份和设备迁移显式排除所有应用数据域，迁移通过用户选择的完整财务备份进行。Android 12 及以后单独设置提取规则，依据[官方备份配置](https://developer.android.com/identity/data/autobackup)，不单靠 `allowBackup=false`。
