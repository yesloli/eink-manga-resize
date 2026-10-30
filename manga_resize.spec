# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。

用法：
    pyinstaller --noconfirm --clean manga_resize.spec

产物在 dist/ 下，单文件、无控制台窗口。

排除掉 pymupdf 全家桶（它会把 onnxruntime / numpy 一起拖进来，体积爆炸），
PDF 只依赖 pypdfium2 —— 够用且轻。build_icon.py 用的 Pillow 也不进包，
运行时只需要 ffmpeg 子进程 + pypdfium2 + tkinterdnd2。
"""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("tkinterdnd2", "pypdfium2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 运行时资源：图标与顶栏 logo（_res() 在冻结后从 sys._MEIPASS 取）
datas += [("assets/app.ico", "assets"), ("assets/app_header.png", "assets")]

EXCLUDES = [
    "fitz", "pymupdf", "pymupdf4llm", "pymupdf_layout", "pymupdf_layout",
    "onnxruntime", "numpy", "networkx", "psutil", "flatbuffers",
    "tabulate", "protobuf", "tqdm", "yaml",
    "matplotlib", "scipy", "pandas", "IPython", "pytest",
    "setuptools", "pip", "wheel",
]

a = Analysis(
    ["manga_resize.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="墨水屏阅读器漫画优化",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/app.ico",
)
