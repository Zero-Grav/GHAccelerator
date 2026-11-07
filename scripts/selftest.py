# -*- coding: utf-8 -*-
"""独立自检入口：python scripts/selftest.py  （无需安装包，直接可用）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghacc.healthcheck import print_summary, run_selftest  # noqa: E402

if __name__ == "__main__":
    # 不传 base_dir：与冻结 exe 的 `--selftest` 走完全相同的默认路径，
    # 避免「源码自检通过、exe 自检失败」的覆盖盲区。
    code, report = run_selftest()
    print_summary(report)
    sys.exit(code)
