# OpenLedger Android 预览版

中文 Android 手机应用，与 Windows 版本共享 Python 3.12 记账核心、SQLite Schema v1、中文规则解析和资金事务。本期使用 Kotlin 原生界面，技术依据见 [ADR-019](../docs/adr/019-android-shared-core.md)。

## 第一版范围

- 自然语言解析为可编辑草稿，确认后记账。
- 收入、支出、日期、时间粒度、分类、账本、账户、支付方式、备注、对象、商户与地点。
- 创建账户并明确填写期初余额、创建账本。
- 本地流水搜索与分页、账户余额、总资产和本月收支。
- 跟随系统明暗主题，中文文本集中在 Android 资源文件，便于后续翻译。
- 已确认请求在进程重启后可恢复，UUID 回执防止重复扣款。

Android 版本为 `0.1.0-alpha1`，预览 APK 的包名为 `org.openledger.android.preview`，使用调试签名。最低 Android 7.0 / API 24，支持 `arm64-v8a` 和 `x86_64`，不支持 32 位手机。

手机与桌面各自本地保存，本期没有跨端同步、AI 服务、交易编辑/删除、CSV/Excel 导入导出、移动备份或完整统计图表。APK 未申请网络权限，不读取微信、支付宝或银行通知。卸载或清除数据会删除手机账本；移动备份上线前请使用合成数据试用。

## 下载与功能路线

[公开预发布下载](https://github.com/Bidao-Penknife/OpenLedger/releases/tag/v1.0.0rc1)包含实际经过本机模拟器测试的 APK。已实现与未实现功能、手机界面限制及下一阶段验收标准见[Android 功能清单与后续路线](../docs/development/android-roadmap.md)。当前优先补齐本地备份恢复。

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
adb install -r dist/android/OpenLedger-0.1.0-alpha1-android-preview.apk
```

第一次创建一个合成现金账户，设置余额和起算日期，然后输入“咖啡25元”，核对账户与分类后保存。重启应用核对余额及流水。支付渠道与资金账户是分别确认的字段，不隐式认为“微信支付”必然从某个账户扣款。

设备测试在独立合成目录调用 APK 内真实 Python / SQLite：

```powershell
./android/gradlew.bat -p android connectedDebugAndroidTest
```

请使用新建的合成数据模拟器运行 UI 测试；已有账户或流水的安装会跳过 UI 夹具，测试不清除用户账本。共享核心测试只写入独立临时目录。设备测试需要实际运行的模拟器或手机，编译测试 APK 与执行设备测试是不同结果，验收记录必须区分。

本机实际结果：1021 项 Python 测试通过；APK 中的 5 项设备测试在 Android 15 / x86_64 模拟器通过；飞行模式下实际完成期初 1000 元、咖啡支出 25 元、余额 975 元，并在结束应用进程后重新启动核对。签名、ABI、16 KB 对齐、零应用权限及不可变 Schema 检查通过。没有将模拟器结果记为 ARM64 真机认证，完整记录见[阶段 10 验收](../docs/development/phase10-validation.md)。
