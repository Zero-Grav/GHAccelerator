# -*- coding: utf-8 -*-
"""结构化日志 + 环形缓冲 + 多级目录降级。

降级链（每条都做真实写入探针，失败即跳过，绝不抛错）：
    用户目录 ~/.ghacc/logs -> 程序目录 logs/ -> 系统临时目录 ghacc/logs -> 纯内存
界面通过 LogBus.since(seq) 增量拉取，日志不进入 GUI 线程之外的控件操作。
"""
from __future__ import annotations

import collections
import logging
import os
import sys
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Config, is_writable_dir
from .version import APP_VER

_FMT = "%(asctime)s | %(levelname)-5s | %(name)-12s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_ROOT_NAME = "ghacc"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3


class LogBus(logging.Handler):
    """线程安全的环形日志缓冲，供 GUI / CLI 消费。"""

    def __init__(self, capacity: int = 2000):
        logging.Handler.__init__(self)
        self._capacity = 2000
        self._lines: collections.deque = collections.deque(maxlen=2000)
        self._lock = threading.Lock()
        self._seq = 0
        self.set_capacity(capacity)
        self.setFormatter(logging.Formatter("%(levelname)-5s | %(name)-12s | %(message)s"))

    def set_capacity(self, capacity: int) -> None:
        """deque.maxlen 只读，容量变更必须重建（保留已有行）。"""
        size = max(100, int(capacity))
        with self._lock:
            self._capacity = size
            self._lines = collections.deque(self._lines, maxlen=size)

    @property
    def capacity(self) -> int:
        with self._lock:
            return self._capacity

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:
            text = record.getMessage()
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        with self._lock:
            self._seq += 1
            self._lines.append("%s | %s" % (stamp, text))

    def since(self, seq: int, limit: int = 300) -> Tuple[List[str], int]:
        """返回 seq 之后新增的行与最新 seq。"""
        with self._lock:
            total = self._seq
            missed = max(0, total - int(seq))
            if missed == 0:
                return [], total
            items = list(self._lines)[-min(missed, limit):]
            return items, total

    def tail(self, n: int = 200) -> List[str]:
        with self._lock:
            return list(self._lines)[-n:]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


_BUS: Optional[LogBus] = None
_LOG_DIR: Optional[Path] = None
_LOG_MODE = "memory"


def get_bus() -> LogBus:
    global _BUS
    if _BUS is None:
        _BUS = LogBus()
    return _BUS


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("%s.%s" % (_ROOT_NAME, name))


def log_exception(logger: logging.Logger, where: str, exc: BaseException) -> None:
    """统一异常记录：带类型名与位置，便于现场按文本检索。"""
    logger.error("%s | %s: %s", where, type(exc).__name__, exc, exc_info=True)


def setup_logging(cfg: Optional[Config] = None,
                  base_dir: Optional[Path] = None,
                  force_memory: bool = False) -> Tuple[logging.Logger, LogBus, Dict[str, Any]]:
    """初始化日志系统，永不因 IO 问题抛异常。

    返回 (root_logger, bus, info)；info 含 mode / dir，供 --selftest 断言。
    """
    global _LOG_DIR, _LOG_MODE
    cfg = cfg or Config.load(base_dir)
    base = Path(base_dir) if base_dir else cfg.base_dir
    level = getattr(logging, str(cfg.settings.get("log_level", "INFO")), logging.INFO)
    want_file = bool(cfg.settings.get("log_to_file", True)) and not force_memory

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    bus = get_bus()
    bus.set_capacity(int(cfg.settings.get("log_max_lines", 2000)))
    root.addHandler(bus)

    # stderr 在 --noconsole 冻结包里可能为 None，必须判空
    if sys.stderr is not None:
        try:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(logging.Formatter(_FMT, _DATEFMT))
            root.addHandler(stream)
        except Exception:
            pass

    mode, directory = "memory", None
    if want_file:
        candidates = [
            Path.home() / ".ghacc" / "logs",
            base / "logs",
            Path(tempfile.gettempdir()) / "ghacc" / "logs",
        ]
        for candidate in candidates:
            if not is_writable_dir(candidate):
                continue
            try:
                fh = RotatingFileHandler(str(candidate / "diagnose.log"),
                                         maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
                                         encoding="utf-8")
                fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
                root.addHandler(fh)
                if candidate == candidates[0]:
                    mode = "user"
                elif candidate == candidates[1]:
                    mode = "program"
                else:
                    mode = "temp"
                directory = candidate
                break
            except Exception:
                continue

    _LOG_DIR, _LOG_MODE = directory, mode
    info = {"mode": mode, "dir": str(directory) if directory else None,
            "level": logging.getLevelName(level), "buffer": bus.capacity}
    root.info("start v%s pid=%s python=%s", APP_VER, os.getpid(), sys.version.split()[0])
    root.info("log mode=%s dir=%s", mode, directory or "(memory-only)")
    return root, bus, info


def log_info() -> Dict[str, Any]:
    return {"mode": _LOG_MODE, "dir": str(_LOG_DIR) if _LOG_DIR else None}
