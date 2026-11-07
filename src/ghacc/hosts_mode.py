# -*- coding: utf-8 -*-
"""可选 hosts 模式：标记块 + 备份 + 原子写 + 精确回滚。

仅当用户显式把 mode 设为 hosts / both 时启用，且需要系统文件写权限。
默认交付形态（proxy 模式）完全不碰 hosts，因此可在无管理员权限下运行。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import Config, is_writable_dir
from .logger import get_logger
from .sysproxy import state_dir

_LOG = get_logger("hosts")

BEGIN = "# >>> GHACC MANAGED BLOCK BEGIN (do not edit) <<<"
END = "# <<< GHACC MANAGED BLOCK END >>>"
_BACKUP_KEEP = 3


def hosts_path() -> Path:
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def _strip_managed(text: str) -> str:
    pattern = re.escape(BEGIN) + r".*?" + re.escape(END) + r"[ \t]*\r?\n?"
    return re.sub(pattern, "", text, flags=re.S | re.M)


class HostsManager(object):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = hosts_path()
        self.backup_path: Optional[Path] = None

    def inspect(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {"path": str(self.path), "exists": self.path.is_file(),
                                "managed_present": False, "writable": False}
        try:
            if self.path.is_file():
                text = self.path.read_text(encoding="utf-8", errors="replace")
                info["managed_present"] = BEGIN in text
                info["size"] = len(text)
            info["writable"] = is_writable_dir(self.path.parent)
        except Exception as exc:
            info["error"] = "%s: %s" % (type(exc).__name__, exc)
        return info

    def _backup(self, original: str) -> Optional[Path]:
        directory = state_dir(self.cfg)
        if directory is None:
            _LOG.warning("无可用备份目录，hosts 备份跳过")
            return None
        target = directory / ("hosts_backup_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
        try:
            target.write_text(original, encoding="utf-8")
            # 只保留最近 3 份，避免塞满受限磁盘
            backups = sorted(directory.glob("hosts_backup_*.txt"), reverse=True)
            for old in backups[_BACKUP_KEEP:]:
                try:
                    old.unlink()
                except Exception:
                    pass
            return target
        except Exception as exc:
            _LOG.warning("hosts 备份失败: %s", exc)
            return None

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".ghacc.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(path))

    def apply(self, mapping: Dict[str, str]) -> Tuple[bool, str]:
        """写入标记块。mapping: {host: ip}；失败绝不留半截文件。"""
        if not mapping:
            return False, "无有效映射，跳过 hosts 写入"
        if not is_writable_dir(self.path.parent):
            return False, "hosts 目录不可写（需要管理员权限 / 只读盘），已跳过 hosts 模式"
        try:
            original = self.path.read_text(encoding="utf-8", errors="replace") \
                if self.path.is_file() else ""
            self.backup_path = self._backup(original)
            cleaned = _strip_managed(original)
            lines = [BEGIN]
            lines += ["%-40s %s" % (ip, host) for host, ip in sorted(mapping.items())]
            lines.append(END)
            block = "\n".join(lines) + "\n"
            if cleaned and not cleaned.endswith("\n"):
                cleaned += "\n"
            self._atomic_write(self.path, cleaned + "\n" + block)
            _LOG.info("hosts 已写入 %d 条，备份=%s", len(mapping), self.backup_path)
            return True, "写入 %d 条映射" % len(mapping)
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)

    def restore(self) -> Tuple[bool, str]:
        """精确移除标记块，保留用户原有内容。"""
        if not self.path.is_file():
            return True, "hosts 文件不存在，无需恢复"
        if not is_writable_dir(self.path.parent):
            return False, "hosts 目录不可写，无法自动清理（请手动删除标记块）"
        try:
            original = self.path.read_text(encoding="utf-8", errors="replace")
            if BEGIN not in original:
                return True, "未发现管理块，无需恢复"
            cleaned = _strip_managed(original)
            self._atomic_write(self.path, cleaned)
            _LOG.info("hosts 管理块已清除")
            return True, "管理块已清除"
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)

    def recover_stale(self) -> Dict[str, Any]:
        """启动自愈：清掉上次异常退出遗留的标记块。"""
        info = self.inspect()
        if not info.get("managed_present"):
            return {"recovered": False, "reason": "no-managed-block"}
        ok, msg = self.restore()
        _LOG.warning("检测到残留 hosts 管理块 -> 清理 (%s)", msg)
        return {"recovered": ok, "reason": msg}
