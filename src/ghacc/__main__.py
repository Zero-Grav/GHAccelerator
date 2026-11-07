# -*- coding: utf-8 -*-
"""程序入口：GUI（默认） / CLI 降级 / 自检 / 应急恢复 / IP 刷新。

    python -m ghacc                    图形界面（Tk 不可用时自动降级 CLI）
    python -m ghacc --cli              命令行常驻（服务器 / 无桌面环境）
    python -m ghacc --selftest         自检 + 取证（退出码 0/1/2）
    python -m ghacc --refresh-ips      从官方 meta 刷新 IP 池
    python -m ghacc --restore          应急恢复系统代理与 hosts
    python -m ghacc --status           打印 JSON 状态
    python -m ghacc --version          版本信息
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# 允许「python src/ghacc/__main__.py」与源码目录直跑
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "ghacc"

from .config import Config
from .logger import get_bus, get_logger
from .version import APP_NAME, APP_VER


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghacc", description="%s v%s - GitHub 访问加速（本地优选直连）" % (APP_NAME, APP_VER))
    parser.add_argument("--cli", action="store_true", help="强制命令行模式（无 GUI）")
    parser.add_argument("--selftest", action="store_true", help="运行自检并输出取证 JSON")
    parser.add_argument("--refresh-ips", action="store_true", help="从 api.github.com/meta 刷新官方 IP 池")
    parser.add_argument("--restore", action="store_true", help="应急恢复：系统代理 + hosts")
    parser.add_argument("--status", action="store_true", help="打印当前状态 JSON")
    parser.add_argument("--version", action="store_true", help="打印版本")
    parser.add_argument("--config-dir", help="指定配置目录（也可设 GHACC_CONFIG_DIR）")
    parser.add_argument("--base-dir", help="指定项目根目录（调试用）")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--no-auto-start", action="store_true",
                        help="CLI 模式下不自动开启（仅打印状态）")
    return parser


def load_config(args) -> Config:
    if args.config_dir:
        os.environ["GHACC_CONFIG_DIR"] = args.config_dir
    cfg = Config.load(Path(args.base_dir) if args.base_dir else None)
    if args.log_level:
        cfg.set_setting("log_level", args.log_level)
    return cfg


def _configure_console() -> None:
    """冻结的 console exe 在管道/重定向场景下 stdout 可能退化为 locale 编码，
    中文会变成 '?'，导致 --status / --selftest 输出与取证文件不可用。
    显式固定为 UTF-8；仅在真实控制台下额外切代码页，不改调用方终端状态。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            if sys.stdout is not None and sys.stdout.isatty():
                import ctypes
                ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass


def main(argv=None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    if args.version:
        print("%s %s (Python %s, %s)" % (APP_NAME, APP_VER,
                                         sys.version.split()[0], sys.platform))
        return 0

    cfg = load_config(args)

    if args.selftest:
        from .healthcheck import print_summary, run_selftest
        code, report = run_selftest(Path(args.base_dir) if args.base_dir else None)
        print_summary(report)
        return code

    if args.restore:
        from . import sysproxy
        from .hosts_mode import HostsManager
        setup = {}
        try:
            from .logger import setup_logging
            setup_logging(cfg)
        except Exception:
            pass
        result = sysproxy.restore_now(cfg)
        hosts = HostsManager(cfg)
        ok, msg = hosts.restore()
        result["hosts"] = {"ok": ok, "message": msg}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") or ok else 1

    from .app import Accelerator
    acc = Accelerator(cfg=cfg)
    acc.install_shutdown_hooks()

    if args.refresh_ips:
        result = acc.refresh_ips()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2

    if args.status:
        print(json.dumps(acc.status(), ensure_ascii=False, indent=2))
        return 0

    # GUI 可用性探测；失败自动降级 CLI（受限环境 / 无桌面 / 缺 python3-tk）
    gui = not args.cli
    if gui:
        try:
            import tkinter  # noqa: F401
        except Exception as exc:
            print("[降级] GUI 不可用（%s），自动切换 CLI 模式" % exc, file=sys.stderr)
            gui = False

    if gui:
        try:
            from .ui.tkinter_ui import run_gui
            return run_gui(acc)
        except Exception as exc:
            print("[降级] GUI 启动失败（%s），切换 CLI 模式" % exc, file=sys.stderr)
            gui = False

    return run_cli(acc, auto_start=not args.no_auto_start)


def run_cli(acc, auto_start: bool = True) -> int:
    log = get_logger("cli")
    bus = get_bus()
    acc.subscribe(lambda kind, payload: None)
    print("%s v%s | 引擎: 本地 CONNECT 隧道（无 MITM / 无根证书）" % (APP_NAME, APP_VER))
    print("配置文件: %s" % (acc.cfg.loaded_dir or "内置默认"))
    print("按 Ctrl+C 关闭加速并恢复系统网络设置\n")

    if auto_start:
        result = acc.start()
        print("[开启] %s | state=%s" % (result["message"], result["state"]))
        for reason in result.get("degraded_reasons", []):
            print("[降级] %s" % reason)
        if not result.get("ok"):
            return 1
        status = acc.status()
        print("本地代理: %s   系统代理后端: %s   优选缓存: %d 条" %
              (status["listen"], status["system_proxy"]["backend"],
               status["resolver"]["cached"]))
        for d in status["decisions"]:
            print("  %-32s -> %-16s rtt=%.1fms source=%s" %
                  (d["host"], d["ip"], d["rtt_ms"], d["source"]))
        print("")

    seq = bus.seq
    try:
        while True:
            lines, seq = bus.since(seq, limit=50)
            for line in lines:
                print(line)
            __import__("time").sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在关闭...")
    finally:
        result = acc.stop()
        print("[关闭] %s" % result["message"])
        for key, value in (result.get("cleanup") or {}).items():
            print("  %-10s %s" % (key, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
