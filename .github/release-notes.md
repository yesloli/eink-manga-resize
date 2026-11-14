<!--
Release 正文模板：softprops/action-gh-release 直接读这个文件（body_path）。
发新版本前把「本版本要点」整段换成新内容，下面的下载说明不用动。
-->

下载下面的 `eink-manga-resize-v1.3-windows-x64.exe`，双击即用，无需安装。
程序内的界面名是「墨水屏阅读器点对点适配」。

处理 MOBI / AZW3 需要另外安装 [Calibre](https://calibre-ebook.com/download_windows)；
处理 EPUB / ZIP / CBZ / PDF 不需要任何额外依赖。

首次运行如遇 SmartScreen 提示，选「更多信息 → 仍要运行」——单文件 EXE 未做代码签名，属正常现象。

---

## 1.3 修复

**有图却整本不动**：两种写法此前够不到，工具还会显示「已适配，无需处理」。

- 文件名写成 `%XX` 编码（包里真实名字是中文）—— 现在找不到时会先 URL 解码再试
- 封面只登记在 OPF、页面没有引用 —— 现在一并处理（阅读器书架用的就是它）
- 顺带补上大小写不敏感的路径匹配，扫描判定与实际改动一致

CSS 背景图仍然不处理：审了 116 本，四分之三是白底版式图，真正的整页插画多数比屏幕还小，收益可以忽略。

全部历史更新见 [CHANGELOG.md](https://github.com/yesloli/eink-manga-resize/blob/main/CHANGELOG.md)。
