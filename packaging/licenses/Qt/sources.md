# Qt / PySide6 6.11.2：许可材料与对应源码

记录日期：2026-10-02。当前工程锁定 PySide6、PySide6-Essentials、PySide6-Addons 和 Shiboken6 `6.11.2`，Qt 运行库版本为 `6.11.2`。具体被冻结的 DLL 和插件以构建产生的依赖/文件清单为准，本文件不是完整二进制 SBOM。

## 开源授权与原文

本项目按 LGPLv3 开源路径使用适用的 PySide6、Shiboken 与 Qt 库。项目 MIT 许可不替代这些组件的许可。PySide6 上游说明 PyPI wheel 同时适用于开源和商业授权路径，见 [PySide6 6.11.2 的维护者说明](https://pypi.org/project/PySide6/6.11.2/)；随 wheel 附带商业许可文本本身不表示本项目取得了商业授权。

本目录补齐从 Qt 官方 `qtbase` 的 `v6.11.2` 标签取得的开源许可全文。LGPLv3 包含 GPLv3 的基础条款，因此同时提供两份原文：

| 本地文件 | 官方来源 | SHA256 |
| --- | --- | --- |
| [LGPL-3.0-only.txt](LGPL-3.0-only.txt) | [Qt qtbase v6.11.2](https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/LGPL-3.0-only.txt) | `da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768` |
| [GPL-3.0-only.txt](GPL-3.0-only.txt) | [Qt qtbase v6.11.2](https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/GPL-3.0-only.txt) | `8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903` |
| [GFDL-1.3-no-invariants-only.txt](GFDL-1.3-no-invariants-only.txt) | [Qt qtbase v6.11.2 文档许可](https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/GFDL-1.3-no-invariants-only.txt) | 见 [来源清单](notice-sources.json) |

以上文件保留下载到的原始字节，未翻译或修改许可正文。GFDL1.3 用于随包保存的 Qt 文档，不将其当作 Qt 动态库的授权。wheel 自带的许可文件也由构建过程保留，不用它们替换这里的开源全文。

## 对应版本源代码获取

维护者已核对以下官方发行目录包含 `6.11.2` 的源码包。本阶段只保存获取方式，没有下载这些完整源码包。

| 对应组件 | 官方源码获取位置 |
| --- | --- |
| PySide6 与 Shiboken6 | [Qt for Python 6.11.2 源码目录](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/)，包含 `pyside-setup-everywhere-src-6.11.2.tar.xz` / `.zip` |
| Qt Core、Gui、Widgets、Network、OpenGL 与 QtBase 平台/样式插件 | [Qt 6.11.2 分模块源码目录](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/)，其中 `qtbase-everywhere-src-6.11.2.tar.xz` / `.zip` |
| Qt SVG 与 SVG 插件 | 同一目录中的 `qtsvg-everywhere-src-6.11.2.tar.xz` / `.zip` |
| Qt Image Formats 插件 | 同一目录中的 `qtimageformats-everywhere-src-6.11.2.tar.xz` / `.zip` |

将源码包、版本说明和上游构建文档一并保存后，可以查看和重新构建对应库。Qt for Python 的 [通用源码构建指南](https://doc.qt.io/qtforpython-6/building_from_source/index.html) 提供绑定与 Shiboken 的构建入口，具体参数应以下载的 `6.11.2` 源码说明为准；Qt 的 [6.11 Windows 源码构建指南](https://doc.qt.io/qt-6.11/windows-building.html) 提供 Qt 运行库构建步骤。

本工程使用发布者提供的 wheel 和动态库，未修改 PySide6、Shiboken 或 Qt 的源码和 DLL；PyInstaller 负责收集它们，不对库源码打补丁。应用原创 Python 代码及构建脚本按项目 MIT 许可提供。分发目录保留动态库，用户可按适用许可证修改或替换接口兼容的库；原型没有库签名强制校验或 DRM。实际替换步骤与兼容性仍需在正式发行阶段验证。

上游下载位置由第三方维护。正式分发前，维护者需要确保对应源码持续可获取，必要时与制品一同提供镜像或明确、可执行的源码提供方式。当前来源链接不是一份已经建立的长期书面源码提供承诺，也不证明最终发行满足全部许可条件。

## Qt 第三方归属文档

本目录保存 [Qt 6.11 官方第三方归属索引](qt-6.11-third-party-code.html) 的原始 HTML，以及其中以下分组链接到的原始归属页面：

- Qt Core：20 个页面。
- Qt GUI：27 个页面。
- Qt Image Formats：2 个页面。
- Qt SVG：1 个页面。
- Qt Network：2 个页面。

下载时官方索引注明所列组件对应 Qt `6.11.2`。索引没有独立 Qt Widgets 或 Qt OpenGL 分组；GUI 分组包括图形相关归属，Widgets/OpenGL 的基础实现及平台依赖还应结合 QtBase 源码和实际 DLL 清单审阅，不能因未列分组推断其无需许可声明。

这些文档可能包含其他平台或不同构建选项的组件。保存模块级材料不表示每一项都被本 Windows 制品实际包含，也不表示已枚举所有间接组件。索引中的其他模块页面、样式或资源未全部保存，部分链接仍需联网查看。

Qt 文档的版权与 GFDL 声明保留在原始 HTML 中；本目录附带对应 GFDL 全文。各第三方组件的版权与许可正文保留在其归属页面中。[notice-sources.json](notice-sources.json) 列出每个已下载文件的来源 URL、字节数、SHA256 和获取时间，供离线核对和后续发行审阅。
