# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile 规格：单个 exe，便于 U 盘 / 内网分发。

取舍：启动时会解压到 %TEMP%（约 2~6 秒），且在企业终端管控环境下
可能被判定为「批量释放文件 / 安装行为」而拦截。受限环境请改用
scripts/app.spec 产出的 onedir 便携目录。

构建：python -m PyInstaller --noconfirm scripts/app-onefile.spec
产物：dist/GHAccelerator-portable.exe
"""
import os


def _project_root():
    here = os.path.abspath(globals().get("SPECPATH", "") or os.getcwd())
    for candidate in (here, os.path.dirname(here), os.getcwd(),
                      os.path.dirname(os.getcwd())):
        if os.path.isfile(os.path.join(candidate, "main.py")) and \
                os.path.isdir(os.path.join(candidate, "src", "ghacc")):
            return candidate
    raise RuntimeError("无法定位项目根（需含 main.py 与 src/ghacc/），请从项目根目录执行构建")


ROOT = _project_root()
SRC = os.path.join(ROOT, "src")

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "main.py")],
    pathex=[SRC],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "config", "settings.json"), "config"),
        (os.path.join(ROOT, "config", "rules.json"), "config"),
    ],
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.scrolledtext", "tkinter.filedialog",
        "tkinter.messagebox", "tkinter.constants",
        "winreg", "http.client", "ssl", "socketserver",
        "concurrent.futures", "logging.handlers", "ipaddress", "queue",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide2", "PySide6", "PyQt5", "numpy", "matplotlib", "pytest",
              "pydoc", "doctest", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, a.binaries, a.zipped_data, a.datas, [],
    name="GHAccelerator-portable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
