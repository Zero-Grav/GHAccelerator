# -*- coding: utf-8 -*-
"""版本与缓存版本常量。

CACHE_VER：凡影响「优选结果结构 / 决策缓存结构」的改动必须递增，
否则升级后旧缓存继续命中，会让修复看起来无效。
"""

APP_NAME = "GHAccelerator"
APP_VER = "1.0.0"
CACHE_VER = 1
BUILD_ID = "2026-11-07"

# 交付形态标识（会显示在界面与 selftest 里，现场截图即可判断走哪条路径）
FORM_SOURCE = "source"      # 源码运行
FORM_FROZEN = "frozen"      # PyInstaller 冻结
FORM_ONEDIR = "onedir"      # 目录便携
