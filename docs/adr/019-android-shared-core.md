# ADR-019：Android 原生界面与共享 Python 核心

状态：已采纳。日期：2026-10-04。

## 问题与约束

项目新增 Android APK 交付。已有 Windows Python 3.12 / PySide6 应用不能作为手机触控界面直接使用。用户指定在当前 Windows 电脑构建与测试，不以安装 Linux、启用系统虚拟化或重启为开发前提。记账规则必须保留统一的整数分、事务、审计、迁移及幂等回执。

## 决策

保留 Windows PySide6 界面，新增 Kotlin 原生 Android 界面。Chaquopy 17.0.0 嵌入 Python 3.12。APK 在构建时从 `src/openledger` 选择共享服务的完整依赖集合，不维护复制版业务代码。`src/openledger/mobile/bridge.py` 是 API v1 JSON 适配层，不开放任意 SQL、文件路径或插件执行。

最低 API 24，ARM64 与 x86_64。Gradle 8.13 / AGP 8.13.2 / JDK 17 固定版本。第一版为 Android 0.1.0-alpha1 预览，使用独立 `.preview` 包名与调试签名，不与 Windows 1.0.0rc1 的完整功能等级混淆。

Qt 官方 Android 部署工具目前要求 Unix 构建宿主；Chaquopy 可在 Windows 构建，并支持当前 Python 版本与 SQLite 标准库。后续如改为 Qt Quick，须另立 ADR 并证明实际构建、触控与可访问性收益。

- [Qt Android 部署要求](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-android-deploy.html)
- [Chaquopy Gradle / Python 集成](https://chaquo.com/chaquopy/doc/current/android.html)
- [AGP 8.13 兼容矩阵](https://developer.android.com/build/releases/agp-8-13-0-release-notes)

## 数据与确认边界

SQLite 在 Android 应用私有 `files/ledger/database` 目录内。Schema v1 的 SQL 字节不修改。手机与桌面分别存储；跨端备份与同步在后续里程碑设计，禁止直接覆盖运行中的数据库。

解析为只读草稿，保存须显式确认。金额输入是十进制字符串，转换和验证在共享 Python 中进行。移动界面只格式化整数，不负责余额运算。Android 将已经确认的完整命令和 UUID 写入私有 SharedPreferences，再执行事务；重启后可恢复同一请求，避免重复记账。

默认不请求网络、短信、通知读取、无障碍、存储或通讯录权限。不会读取其他应用的支付通知。本期“智能”是本地中文规则解析；AI 和云传输另行实现。

## 后果与验证

工程同时包含 Kotlin UI 与 Python 核心，维护者需要 JDK 和 Android SDK。第一版没有完整桌面功能，不做插件市场、支付采集或云同步。APK 卸载会删除应用数据，移动备份功能上线前应使用合成账目试用。

验证包含：独立 Python 进程无 Qt 导入、真实 SQLite 的移动 API 测试、既有 Windows 回归、APK 签名 / ABI / 权限 / 迁移摘要检查，以及设备上的嵌入式 Python 测试。未执行的设备或真机检查必须单独注明。
