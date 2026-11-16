# -*- coding: utf-8 -*-
"""版本与缓存版本常量。

CACHE_VER：凡影响「优选结果结构 / 决策缓存结构」的改动必须递增，
否则升级后旧缓存继续命中，会让修复看起来无效。

v1.0.0 -> v1.0.1 (T6)：优选判定从「TLS 握手」推进到「真实 HTTP 请求」，
ProbeResult 结构新增 http_ok 字段，故 CACHE_VER 1 -> 2。
"""

APP_NAME = "GHAccelerator"
APP_VER = "1.0.1"
CACHE_VER = 2
BUILD_ID = "2026-11-16"

# 交付形态标识（会显示在界面与 selftest 里，现场截图即可判断走哪条路径）
FORM_SOURCE = "source"      # 源码运行
FORM_FROZEN = "frozen"      # PyInstaller 冻结
FORM_ONEDIR = "onedir"      # 目录便携
