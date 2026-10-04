# Qt Widgets 冻结范围

当前应用使用 `QtCore`、`QtGui`、`QtWidgets` 和用于单实例本地 IPC 的 `QtNetwork`，采用 Windows 系统输入法。自定义 `hooks/hook-PySide6.QtGui.py` 基于已安装 PyInstaller 6.22.3 的 Qt hook 行为编写：调用官方 `add_qt6_dependencies` 收集常规依赖后，在插件交给 `Analysis` 的 DLL 递归扫描前移除 `imageformats/qpdf` 和 `platforminputcontexts/qvirtualkeyboard`。

现行界面使用 Qt Widgets，QSS、迁移 SQL、tzdata 和翻译资源随包保留；PDF 报告用 QtGui `QPdfWriter`。合成输入法事件和原生 `qwindows` 检查均不替代独立 Windows 或真实输入法全流程。当前冻结与安装候选见[阶段 8 记录](../../docs/development/phase8-validation.md)，更早记录保留历史结果。

PE 版本资源由 `scripts/release_tools.py prepare` 生成，完整 Windows 脚本自动执行。单独调用 spec 前先生成 `build/release/windows-version.txt`；版本包含用户可见的候选后缀及用于比较的四段整数。bundle-manifest.json 记录实际文件摘要，安装器编译前核对完整目录。

默认 hook 会收集整类 QtGui 插件。这两个未使用的插件会带入 Qt PDF、Virtual Keyboard 以及后者所需的 QML/Quick 模块。其余默认插件和真实 DLL 依赖均保留，包括 `qwindows`、常规图片格式和样式。Spec 排除本阶段未使用的 PDF、Virtual Keyboard、QML、Quick 和 WebEngine Python 绑定，并检查 Analysis 最终 TOC：若这些可选模块 DLL 或被禁用插件再次出现，构建立刻失败。最终目录不执行删 DLL 的补救操作。

每次升级 PyInstaller 或 PySide6 后，都要检查官方 hook 行为、最终模块清单以及冻结程序启动和资源加载。后续若明确引入这些能力，应先设计功能、依赖和许可证分发方案，再调整冻结范围；本说明不把插件过滤视作完整的发布许可审核。

构建脚本把 `PYINSTALLER_CONFIG_DIR` 固定在仓库 `build/pyinstaller-cache`，拒绝缓存/构建/分发输出经过符号链接或 junction；构建期间清除继承的 `PYTHONPATH`，将 DLL 搜索用 PATH 限定在项目 Python 和 Windows 系统目录，并在 `finally` 恢复调用者环境。这使 `--clean` 使用工作区缓存，也避免其他 Python 工具目录或同名 DLL 污染冻结结果。

官方资料：[PyInstaller Hook 机制](https://pyinstaller.org/en/stable/hooks.html)、[PyInstaller Qt hook 源码](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/utils/hooks/qt/__init__.py)、[Qt 模块列表](https://doc.qt.io/qt-6/qtmodules.html)。
