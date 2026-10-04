# 第三方组件与许可证说明

Android 预览 APK 的原始运行时许可材料单独保存在 [Android licenses](android/app/src/main/assets/licenses/NOTICE.md)，随 APK 的 `assets/licenses` 分发。覆盖 Chaquopy、Python、Kotlin、OpenSSL、SQLite、时区数据及标准库中的压缩 / FFI 组件，来源与摘要见同目录 `sources.json`。Android 不打包 Qt、JDK、Android SDK 或 formatter。

OpenLedger 的原创源码采用 [MIT License](LICENSE)。依赖、打包运行库、Qt 插件及其内部第三方代码保留原有授权；项目的 MIT 许可不能替代这些组件的许可条件。

本文描述依赖来源与发行工作要求。**具体分发版本、许可证全文和文件清单，以本次锁文件与构建产物中的许可证材料为准。** 当前未做出“完整法务审核已经完成”的结论。

## 运行与构建组件

| 组件 | 用途 | 许可与依据 |
| --- | --- | --- |
| Python / CPython | 源码运行与冻结解释器 | PSF License Agreement 及 Python 发行包中的历史许可证；见 [Python 许可](https://docs.python.org/3/license.html) |
| PySide6、Shiboken6 6.11.2 | Python 的 Qt 绑定与支持库 | Qt for Python 提供 LGPLv3/GPL 等开源与商业授权路径；当前项目使用适用的 LGPLv3 库。见 [PySide6 6.11.2 许可说明](https://pypi.org/project/PySide6/6.11.2/) |
| Qt 6 运行库与平台插件 | 窗口、控件与系统集成 | 不同模块的许可条件可能不同，需要核对实际打包模块；见 [Qt 许可说明](https://www.qt.io/development/qt-framework/qt-licensing) |
| tzdata | IANA 时区数据库后备数据 | 包装代码采用 Apache-2.0，时区数据另见其发行包声明；见 [tzdata 项目](https://pypi.org/project/tzdata/) |
| openpyxl 3.1.5 | 本地 XLSX 读取与写入 | MIT；本阶段安装包 `METADATA`、MIT classifier 与 `LICENCE.rst` 已核对，精确原文随构建产物保留 |
| et-xmlfile 2.0.0 | openpyxl 的 XML 写入依赖 | 主体 MIT；发行包的 `LICENCE.python` 另保留所含 Python 标准库代码许可，与 `LICENCE.rst` 一同分发 |
| defusedxml 0.7.1 | XLSX XML 解析防护 | 安装包元数据为 `PSFL`，许可 classifier 为 Python Software Foundation License，实际 `LICENSE` 为 PSF License Version 2 |
| packaging | 稳定版本与插件版本的 PEP 440 比较 | Apache-2.0 / BSD-2-Clause；保留锁定安装包的 LICENSE、LICENSE.APACHE、LICENSE.BSD 和元数据 |
| PyInstaller | Windows 冻结程序与 bootloader | GPL 及允许分发构建程序的特殊例外；见 [PyInstaller 许可](https://pyinstaller.org/en/stable/license.html) |
| Inno Setup 6.7.3 | 安装与卸载程序 | 保留 Inno Setup 版权、网站与许可原文；固定版本来源和 SHA256 见 [Inno 材料](packaging/licenses/InnoSetup/sources.json) |

Ruff、mypy、pytest、pytest-qt、uv 与构建后端用于开发和构建。它们通常不作为应用运行库随 exe 分发，但分发源码、开发环境镜像或工具本体时仍需要核对相关许可证。版本以 `uv.lock` 和实际工具输出为准。

阶段 6 的新增组件信息取自本地锁定环境的实际安装元数据和许可文件，不用包名或仓库 MIT 推断其授权。构建收集器需要同时识别 `LICENSE` 与 `LICENCE`，将新增依赖原文及元数据保留在便携包 `licenses/` 中；实际收集结果与制品检查仍以[阶段 6 验收记录](docs/development/phase6-validation.md)为准。

## Qt 与 LGPL 发行考虑

计划使用 Qt Widgets 的动态库分发方式，不修改 PySide6 或 Qt 上游源码。`onedir` 便于保留可辨识的动态库、插件和通知，但动态链接本身不表示所有 LGPL 义务已经满足。发行前需要核对通知、许可文本、对应库源码提供方式，以及用户替换或重新链接库的条件。依据见 [Qt 官方 LGPL/GPL 义务说明](https://www.qt.io/development/open-source-lgpl-obligations)。

新增 Qt 模块前逐一审阅其许可，不假设所有模块都可按 LGPL 使用。Qt 还包含来自其他作者的代码，需要保留实际随包分发组件的声明。Qt 官方提供 [第三方代码目录](https://doc.qt.io/qt-6/licenses-used-in-qt.html)；核对时选择与锁定 Qt 版本对应的文档和源码，不能仅沿用“最新版”目录。

当前统计图表、PNG 与 PDF 使用 QtGui 的 QPainter、QImage 和 QPdfWriter，PDF 写入不需要 QtPdf 模块，也未引入 Matplotlib 或 ReportLab。报告字体从系统选择，Qt 在生成 PDF 时嵌入需要的字形；项目和便携包不复制 Microsoft YaHei 等系统字体原文件。新增图形或字体资源仍需逐项核对其来源与许可。

当前锁定的 PySide6/Shiboken6 `6.11.2` wheel 使用开源授权元数据，但附带的许可材料不包含全部 LGPLv3/GPLv3 全文。上游明确 PyPI wheel 可用于开源或商业两种路径；商业许可文件不会自动授予本项目商业授权。工程已在 [packaging/licenses/Qt](packaging/licenses/Qt/) 补入从 Qt 官方 `v6.11.2` 仓库获得的 LGPLv3 与配套 GPLv3 原文，并保存版本 `6.11.2` 的 Core、Gui、Network、Image Formats、SVG 第三方归属文档及其文档许可。

构建时上述目录复制为便携包的 `licenses/Qt/`；其中 `sources.md` 说明未修改的库、精确版本源码获取位置与材料范围，`notice-sources.json` 保存来源和 SHA256。模块归属页面可能包含本 Windows 构建未使用的组件，不能把这份文档集合称为完整的实际制品 SBOM。正式发行仍需将材料与 DLL、插件、间接组件清单逐项核对并确保对应源码的持续可获取性。

## 构建与再分发

构建产物需要携带项目 LICENSE、本文以及当前分发依赖的许可证材料和版本清单。重新分发时保留整个应用目录中的通知材料，避免仅复制 exe；不能在用户协议中禁止 LGPL 所允许的库修改与相关调试行为。

正式 Release 前应核对：冻结包实际包含的 DLL、Qt 插件、Python 扩展、数据文件及其来源；上游许可全文是否齐全；所需源码或获取方式是否对应准确版本；新增图标、字体、报告模板是否有授权。发现缺失时修正构建和通知，再发布制品。

MIT 项目许可、生成一份依赖清单和保留动态 DLL 都不能单独证明最终发行满足所有第三方要求。当前材料是发行审阅的起点，后续阶段按具体制品完成核对。
