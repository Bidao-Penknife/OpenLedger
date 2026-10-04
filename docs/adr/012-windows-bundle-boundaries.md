# ADR-012：Windows 冻结包的插件与 DLL 来源边界

状态：阶段 3 已采用。日期：2026-10-02。

## 问题与决策

默认 QtGui hook 会收集已安装的图像与输入插件。最小 Widgets 应用因此可能带入未使用的 Qt PDF、Virtual Keyboard 及其 QML/Quick 依赖，扩大体积和许可范围。自定义 hook 调用 PyInstaller 的标准 `add_qt6_dependencies`，在 DLL 递归扫描前过滤 `qpdf*`、`qvirtualkeyboard*` 和 `qtvirtualkeyboard*` 插件；其余实际依赖仍由标准 hook 收集。

Spec 显式排除未使用的 PDF、QML、Quick、Virtual Keyboard 和 WebEngine 绑定，并对最终二进制 TOC 加检查：如果仍出现相应 DLL/插件，构建失败。不能在最终目录盲目删除依赖库，让构建看似成功但运行失败。升级 PyInstaller 或 PySide6 后必须重新验证这些规则。

首次 exe 验证还发现构建宿主 PATH 中另一个工具的 `icuuc.dll` 被收集；其接口与 Windows 系统 ICU 不同，造成 QtCore 缺失入口。静态导入/导出比较定位了该冲突；隔离该 DLL 后真实启动恢复正常。因此构建脚本在同步项目环境后，将 PATH 限制为项目虚拟环境、当前 Python 安装与 Windows 系统目录，清除继承的 PYTHONPATH，结束时恢复环境。PyInstaller 缓存也限定在仓库 build 内。

## 验证与后果

源码和冻结程序都从另一个含中文/空格的工作目录启动，数据写入独立目录。冻结 smoke 清除外部 Python/Qt 环境变量，并将 PATH 限制为 Windows 系统目录，验证包内 Python、Qt、资源和平台插件；这不等于干净 Windows 虚拟机验收。

许可目录记录确切依赖版本和原始许可材料，Qt 归属文档保存来源及摘要。后续新增 Qt 模块时需要重新审阅分发范围；插件过滤不构成完整许可审阅或安全沙箱。

依据：已安装的 PyInstaller 6.22.3 Qt hook 源码、实际二进制依赖检查，以及[官方 Hook 文档](https://pyinstaller.org/en/stable/hooks.html)。
