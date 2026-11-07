# -*- coding: utf-8 -*-
"""产物校验：存在性 + 可解析性 + 关键文件计数 + 自检调用 + SHA256。

用法：
    python scripts/verify_artifacts.py                                  # 校验源码交付树
    python scripts/verify_artifacts.py --dir dist/GHAccelerator --frozen  # 校验 exe 产物
    python scripts/verify_artifacts.py --no-selftest                  # 跳过自检（快速检查）

打包日志显示成功 != 产物可用；本脚本是发版前的最后一道闸。
frozen 模式直接调用被校验目录里的 GHAccelerator-cli.exe --selftest，
而不是回头跑源码自检 —— 必须用产物本身自证可用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SOURCE_REQUIRED = [
    "README.md", "requirements.txt", "main.py", "run.bat", "run.sh", "run.command",
    "config/settings.json", "config/rules.json",
    "src/ghacc/__init__.py", "src/ghacc/__main__.py", "src/ghacc/version.py",
    "src/ghacc/config.py", "src/ghacc/logger.py", "src/ghacc/ip_pool.py",
    "src/ghacc/prober.py", "src/ghacc/resolver.py", "src/ghacc/proxy_server.py",
    "src/ghacc/sysproxy.py", "src/ghacc/hosts_mode.py", "src/ghacc/app.py",
    "src/ghacc/healthcheck.py", "src/ghacc/ui/__init__.py",
    "src/ghacc/ui/tkinter_ui.py",
    "scripts/selftest.py", "scripts/verify_artifacts.py",
    "scripts/build_windows.bat", "scripts/app.spec", "scripts/app-onefile.spec",
    "tests/__init__.py", "tests/test_config.py", "tests/test_resolver.py",
    "tests/test_proxy.py",
]

FROZEN_REQUIRED = [
    "GHAccelerator.exe", "GHAccelerator-cli.exe", "README.md",
    "config/settings.json", "config/rules.json",
]

FROZEN_RUNTIME = [
    "_tkinter.pyd", "_tcl_data", "_tk_data", "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll", "_ssl.pyd", "_socket.pyd",
    "config/settings.json", "config/rules.json",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path, frozen: bool = False, run_selftest: bool = True) -> int:
    checks: List[Dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    required = FROZEN_REQUIRED if frozen else SOURCE_REQUIRED
    missing = [rel for rel in required if not (root / rel).is_file()]
    record("必需文件存在性", not missing,
           "缺失: %s" % missing if missing else "%d/%d" % (len(required), len(required)))

    # 配置必须能被 JSON 解析且通过业务校验
    try:
        cfg_dir = root / "config"
        settings = json.loads((cfg_dir / "settings.json").read_text(encoding="utf-8"))
        rules = json.loads((cfg_dir / "rules.json").read_text(encoding="utf-8"))
        from ghacc.config import Config
        cfg = Config(settings=settings, rules=rules, base_dir=root)
        problems = cfg.validate()
        record("配置可解析且通过校验", not problems,
               "；".join(problems[:3]) if problems else
               "domains=%d pools=%d" % (len(rules.get("domains", [])),
                                        len(rules.get("ip_pools", {}))))
    except Exception as exc:
        record("配置可解析且通过校验", False, "%s: %s" % (type(exc).__name__, exc))

    if frozen:
        # 冻结产物：不检查源码，改查运行库 / Tk 数据 / 目录条目数
        internal = root / "_internal"
        missing_rt = [n for n in FROZEN_RUNTIME if not (internal / n).exists()]
        record("运行库与 Tk 数据齐全", not missing_rt,
               "缺失: %s" % missing_rt if missing_rt else "%d 项关键依赖" % len(FROZEN_RUNTIME))
        all_files = [p for p in root.rglob("*") if p.is_file()]
        record("产物条目数 >= 100", len(all_files) >= 100, "count=%d" % len(all_files))
        sizes = {}
        for name in ("GHAccelerator.exe", "GHAccelerator-cli.exe"):
            p = root / name
            sizes[name] = p.stat().st_size if p.is_file() else 0
        record("exe 体积在预期区间", all(1_000_000 < v < 10_000_000 for v in sizes.values()),
               str(sizes))
    else:
        py_files = sorted((root / "src" / "ghacc").rglob("*.py"))
        bad_syntax = []
        for path in py_files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except Exception as exc:
                bad_syntax.append("%s: %s" % (path.name, exc))
        record("Python 源码语法编译", not bad_syntax and len(py_files) >= 12,
               "；".join(bad_syntax[:3]) if bad_syntax else "%d 个模块" % len(py_files))
        record("模块条目数 >= 12", len(py_files) >= 12, "count=%d" % len(py_files))

    # 自检：frozen 必须用产物自身的 CLI exe
    if run_selftest:
        try:
            if frozen:
                cmd = [str(root / "GHAccelerator-cli.exe"), "--selftest"]
                cwd = str(root)
            else:
                cmd = [sys.executable, str(ROOT / "scripts" / "selftest.py")]
                cwd = str(ROOT)
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=240)
            tail = [ln.strip() for ln in (proc.stdout or "").splitlines()
                    if "total=" in ln or "exit_code=" in ln or "frozen=" in ln]
            record("产物自检退出码 0", proc.returncode == 0,
                   "rc=%d %s" % (proc.returncode, " | ".join(tail[-3:])[:170]))
        except Exception as exc:
            record("产物自检退出码 0", False, "%s: %s" % (type(exc).__name__, exc))
    else:
        record("产物自检退出码 0", True, "skipped (--no-selftest)")

    # SHA256 清单：frozen 覆盖目录内全部文件，源码覆盖交付清单
    try:
        if frozen:
            targets = sorted([p for p in root.rglob("*")
                              if p.is_file() and p.name != "SHA256SUMS.txt"])
            lines = ["%s  %s" % (sha256(p), p.relative_to(root).as_posix())
                     for p in targets]
        else:
            targets = [root / rel for rel in required if (root / rel).is_file()]
            lines = ["%s  %s" % (sha256(p), (root / rel).as_posix())
                     for rel, p in zip([r for r in required if (root / r).is_file()], targets)]
        (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        record("SHA256 清单生成", True, "%d 条 -> SHA256SUMS.txt" % len(lines))
    except Exception as exc:
        record("SHA256 清单生成", False, str(exc))

    print("=" * 70)
    print(" 产物校验 | root=%s | mode=%s" % (root, "frozen" if frozen else "source"))
    print("=" * 70)
    for item in checks:
        print(" %-34s %-5s %s" % (item["name"], "PASS" if item["ok"] else "FAIL",
                                   item["detail"][:90]))
    failed = [c["name"] for c in checks if not c["ok"]]
    print("-" * 70)
    print(" 结论: %s  (%d/%d 通过)" %
          ("可交付" if not failed else "不可交付，失败项: " + ", ".join(failed),
           len(checks) - len(failed), len(checks)))
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GHAccelerator 产物校验")
    parser.add_argument("--dir", help="待校验目录（默认源码树根目录）")
    parser.add_argument("--frozen", action="store_true", help="按冻结产物清单校验")
    parser.add_argument("--no-selftest", action="store_true", help="跳过自检")
    args = parser.parse_args()
    target = Path(args.dir).resolve() if args.dir else ROOT
    sys.exit(verify(target, frozen=args.frozen, run_selftest=not args.no_selftest))
