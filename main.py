# -*- coding: utf-8 -*-
"""冻结包（PyInstaller）入口。源码运行请用 `python -m ghacc`。"""
import sys

from ghacc.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
