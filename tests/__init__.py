# -*- coding: utf-8 -*-
"""单元测试包：把 src 挂到 sys.path，保证无需安装即可运行。"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
