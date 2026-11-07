# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 规格（禁装 / 只读盘环境首选，零解压、零注册表）。

同一个 _internal 目录下产出两个入口：
    GHAccelerator.exe        GUI（console=False，双击即用，无黑窗）
    GHAccelerator-cli.exe    CLI（console=True，用于 --selftest / --status / --restore）

构建：python -m PyInstaller --noconfirm --clean scripts/app.spec
产物：dist/GHAccelerator/
"""
import os


def _project_root():
    # SPECPATH 在不同 PyInstaller 版本里可能是「spec 文件目录」或「工作目录」，
    # 不依赖其语义，改用项目锚点（main.py + src/ghacc/）自动定位。
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
        (os.path.join(ROOT, "README.md"), "."),
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

exe_gui = EXE(  # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GHAccelerator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 不用 UPX：降低杀软误报概率
    console=False,
    disable_windowed_traceback=False,
)
exe_cli = EXE(  # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GHAccelerator-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(  # noqa: F821
    exe_gui, exe_cli, a.binaries, a.zipped_data, a.datas,
    strip=False, upx=False, name="GHAccelerator",
)
