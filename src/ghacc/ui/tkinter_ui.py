# -*- coding: utf-8 -*-
"""Tkinter 桌面界面。

线程纪律：所有网络/启停操作都在后台线程执行；工作线程只把结果放进
queue.Queue，主线程通过 root.after 轮询并更新控件。GUI 对象绝不在工作线程创建。
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable, Dict, List, Optional

from ..config import match_rule
from ..logger import get_bus, get_logger
from ..version import APP_NAME, APP_VER

_LOG = get_logger("ui")

_STATE_TEXT = {
    "STOPPED": ("已停止", "#8b949e"),
    "STARTING": ("正在开启…", "#d29922"),
    "RUNNING": ("加速中", "#3fb950"),
    "DEGRADED": ("降级运行", "#e3a008"),
    "STOPPING": ("正在关闭…", "#d29922"),
    "ERROR": ("故障", "#f85149"),
}


class AppUI(object):
    def __init__(self, root: tk.Tk, acc):
        self.root = root
        self.acc = acc
        self.bus = get_bus()
        self._seq = self.bus.seq
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._state = acc.state
        self._build()
        self.acc.subscribe(self._on_event)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._poll)
        self._refresh_status()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.root.title("%s v%s  |  GitHub 访问加速" % (APP_NAME, APP_VER))
        self.root.geometry("900x680")
        self.root.minsize(820, 600)
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        # ---- 顶部：状态 + 一键开关 ----
        head = ttk.LabelFrame(outer, text=" 加速控制 ", padding=12)
        head.pack(fill=tk.X)
        left = ttk.Frame(head)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        row = ttk.Frame(left)
        row.pack(fill=tk.X)
        self.canvas = tk.Canvas(row, width=18, height=18, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, padx=(0, 8))
        self._lamp = self.canvas.create_oval(2, 2, 16, 16, fill="#8b949e", outline="")
        self.state_var = tk.StringVar(value="已停止")
        ttk.Label(row, textvariable=self.state_var,
                  font=("TkDefaultFont", 15, "bold")).pack(side=tk.LEFT)

        self.engine_var = tk.StringVar(value="引擎: 本地 CONNECT 隧道 / 无 MITM / 无根证书")
        ttk.Label(left, textvariable=self.engine_var, foreground="#57606a").pack(anchor=tk.W, pady=(6, 0))
        self.info_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.info_var, foreground="#57606a").pack(anchor=tk.W, pady=(2, 0))
        self.reason_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.reason_var, foreground="#bf8700",
                  wraplength=620).pack(anchor=tk.W, pady=(2, 0))

        self.toggle_btn = ttk.Button(head, text="开启加速", width=14, command=self._toggle)
        self.toggle_btn.pack(side=tk.RIGHT, padx=(16, 0), ipady=12)

        # ---- 操作按钮 ----
        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(10, 6))
        for text, command in (
            ("重新测速", self._reprobe),
            ("刷新官方 IP", self._refresh_ips),
            ("运行自检", self._selftest),
            ("应急恢复", self._restore),
            ("导出状态", self._export_status),
        ):
            ttk.Button(actions, text=text, command=command).pack(side=tk.LEFT, padx=(0, 8))

        # ---- 分页 ----
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self._build_overview()
        self._build_rules()
        self._build_settings()
        self._build_logs()

        self.footer_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.footer_var, foreground="#57606a").pack(anchor=tk.W)

    def _build_overview(self) -> None:
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="概览")

        stats = ttk.LabelFrame(tab, text=" 运行统计 ", padding=10)
        stats.pack(fill=tk.X)
        self.stat_vars: Dict[str, tk.StringVar] = {}
        labels = [("listen", "本地代理"), ("mode", "工作模式"),
                  ("sysproxy", "系统代理"), ("cached", "优选缓存"),
                  ("active", "活跃连接"), ("accelerated", "已加速请求"),
                  ("direct", "直连请求"), ("failed", "失败请求"),
                  ("bytes", "转发流量")]
        grid = ttk.Frame(stats)
        grid.pack(fill=tk.X)
        for i, (key, label) in enumerate(labels):
            var = tk.StringVar(value="-")
            self.stat_vars[key] = var
            col, row = (i % 3) * 2, i // 3
            ttk.Label(grid, text=label + "：", foreground="#57606a").grid(
                row=row, column=col, sticky=tk.W, padx=(0, 4), pady=3)
            ttk.Label(grid, textvariable=var).grid(row=row, column=col + 1,
                                                   sticky=tk.W, pady=3)

        table = ttk.LabelFrame(tab, text=" 当前优选结果 ", padding=8)
        table.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        columns = ("host", "ip", "rtt", "source", "tls")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=8)
        for col, text, width in (("host", "目标域名", 250), ("ip", "优选 IP", 150),
                                 ("rtt", "RTT (ms)", 90), ("source", "判定来源", 120),
                                 ("tls", "证书校验", 90)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=tk.W)
        scroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_rules(self) -> None:
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="目标网址与规则")

        top = ttk.LabelFrame(tab, text=" 加速域名白名单（仅这些域名被接管） ", padding=8)
        top.pack(fill=tk.BOTH, expand=True)
        cols = ("host", "enabled", "pool")
        self.rule_tree = ttk.Treeview(top, columns=cols, show="headings", height=9)
        for col, text, width in (("host", "域名 / 通配", 300), ("enabled", "启用", 80),
                                 ("pool", "IP 池", 160)):
            self.rule_tree.heading(col, text=text)
            self.rule_tree.column(col, width=width, anchor=tk.W)
        sb = ttk.Scrollbar(top, orient=tk.VERTICAL, command=self.rule_tree.yview)
        self.rule_tree.configure(yscrollcommand=sb.set)
        self.rule_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        edit = ttk.Frame(tab)
        edit.pack(fill=tk.X, pady=8)
        ttk.Label(edit, text="新域名:").pack(side=tk.LEFT)
        self.new_host = tk.StringVar()
        ttk.Entry(edit, textvariable=self.new_host, width=30).pack(side=tk.LEFT, padx=4)
        ttk.Label(edit, text="IP 池:").pack(side=tk.LEFT)
        self.new_pool = tk.StringVar(value="github_web")
        ttk.Combobox(edit, textvariable=self.new_pool, width=16, state="readonly",
                     values=["github_web", "github_api", "github_cdn"]).pack(side=tk.LEFT, padx=4)
        ttk.Button(edit, text="添加", command=self._rule_add).pack(side=tk.LEFT, padx=2)
        ttk.Button(edit, text="启用/禁用", command=self._rule_toggle).pack(side=tk.LEFT, padx=2)
        ttk.Button(edit, text="删除", command=self._rule_delete).pack(side=tk.LEFT, padx=2)

        pool_box = ttk.LabelFrame(tab, text=" IP 池（JSON，可自定义官方 IP 候选） ", padding=6)
        pool_box.pack(fill=tk.BOTH, expand=True)
        self.pool_text = tk.Text(pool_box, height=7, wrap=tk.NONE, font=("Consolas", 9))
        psb = ttk.Scrollbar(pool_box, orient=tk.VERTICAL, command=self.pool_text.yview)
        self.pool_text.configure(yscrollcommand=psb.set)
        self.pool_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        psb.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(tab)
        bottom.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bottom, text="应用规则并保存", command=self._save_rules).pack(side=tk.LEFT)
        ttk.Button(bottom, text="重新载入", command=self._load_rules).pack(side=tk.LEFT, padx=6)
        ttk.Label(bottom, text="非白名单域名一律原样直连，不劫持、不转发",
                  foreground="#57606a").pack(side=tk.LEFT, padx=12)
        self._load_rules()

    def _build_settings(self) -> None:
        tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(tab, text="设置")
        s = self.acc.cfg.settings
        self.set_vars: Dict[str, Any] = {}
        rows = [
            ("listen_port", "本地代理端口", "entry", "18792"),
            ("mode", "工作模式", "combo", ["proxy", "hosts", "both"]),
            ("fallback", "优选失败策略", "combo", ["direct", "reject"]),
            ("probe_timeout_ms", "单 IP 探测超时 (ms)", "entry", "1200"),
            ("probe_workers", "并发探测数", "entry", "12"),
            ("connect_timeout_s", "连接上游超时 (s)", "entry", "8.0"),
            ("cache_ttl_s", "优选缓存 TTL (s)", "entry", "600"),
            ("refresh_interval_s", "后台重测间隔 (s, 0=关)", "entry", "0"),
            ("log_level", "日志级别", "combo", ["DEBUG", "INFO", "WARNING", "ERROR"]),
        ]
        for i, (key, label, kind, extra) in enumerate(rows):
            ttk.Label(tab, text=label + "：").grid(row=i, column=0, sticky=tk.W, pady=5)
            var: Any = tk.StringVar(value=str(s.get(key, extra if isinstance(extra, str) else "")))
            if kind == "combo":
                ttk.Combobox(tab, textvariable=var, values=extra, state="readonly",
                             width=24).grid(row=i, column=1, sticky=tk.W, pady=5)
            else:
                ttk.Entry(tab, textvariable=var, width=27).grid(row=i, column=1,
                                                                sticky=tk.W, pady=5)
            self.set_vars[key] = (var, kind, extra)

        self.auto_sysproxy = tk.BooleanVar(value=bool(s.get("set_system_proxy", True)))
        ttk.Checkbutton(tab, text="自动设置/恢复系统代理（Windows HKCU，免管理员）",
                        variable=self.auto_sysproxy).grid(row=len(rows), column=0,
                                                         columnspan=2, sticky=tk.W, pady=8)
        self.tls_verify = tk.BooleanVar(value=bool(s.get("probe_tls_verify", True)))
        ttk.Checkbutton(tab, text="探测时校验 TLS 证书（强烈建议开启，排除被劫持 IP）",
                        variable=self.tls_verify).grid(row=len(rows) + 1, column=0,
                                                      columnspan=2, sticky=tk.W, pady=4)

        ttk.Button(tab, text="保存设置", command=self._save_settings).grid(
            row=len(rows) + 2, column=0, sticky=tk.W, pady=14)
        ttk.Label(tab, text="注意：端口/模式改动需「关闭加速 → 再开启」后生效",
                  foreground="#57606a").grid(row=len(rows) + 2, column=1, sticky=tk.W)

    def _build_logs(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="日志与诊断")
        bar = ttk.Frame(tab)
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="清空显示", command=self._clear_log_view).pack(side=tk.LEFT)
        ttk.Button(bar, text="导出日志", command=self._export_log).pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text="日志只记录 host:port 与字节数，不记录 URL / 凭据",
                  foreground="#57606a").pack(side=tk.LEFT, padx=10)
        self.log_text = scrolledtext.ScrolledText(tab, wrap=tk.WORD, height=20,
                                                  font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)
        for line in self.bus.tail(200):
            self._append_log(line)

    # ------------------------------------------------------------------
    # 事件与轮询（唯一允许更新控件的地方）
    # ------------------------------------------------------------------
    def _on_event(self, kind: str, payload: Dict[str, Any]) -> None:
        self._queue.put(("event", kind, payload))

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "done":
                    self._busy = False
                    self._on_done(item[1])
                elif item[0] == "event":
                    _LOG.debug("event %s", item[1])
        except queue.Empty:
            pass
        lines, self._seq = self.bus.since(self._seq, limit=100)
        for line in lines:
            self._append_log(line)
        self._refresh_status()
        self.root.after(200, self._poll)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_done(self, result: Any) -> None:
        self.toggle_btn.configure(state=tk.NORMAL)
        if isinstance(result, dict) and result.get("error"):
            messagebox.showerror("操作失败", str(result["error"]))
        elif isinstance(result, dict) and result.get("message"):
            if result.get("state") == "ERROR":
                messagebox.showerror("开启失败", str(result["message"]))
        self._refresh_status()

    def _async(self, fn: Callable[[], Any]) -> None:
        if self._busy:
            messagebox.showinfo("请稍候", "上一个操作仍在执行")
            return
        self._busy = True
        self.toggle_btn.configure(state=tk.DISABLED)
        self._append_log("--- 操作开始，请稍候 ---")

        def worker():
            try:
                result = fn()
            except Exception as exc:
                result = {"error": "%s: %s" % (type(exc).__name__, exc)}
            self._queue.put(("done", result))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_status(self) -> None:
        text, color = _STATE_TEXT.get(self.acc.state, ("未知", "#8b949e"))
        self.state_var.set(text)
        self.canvas.itemconfig(self._lamp, fill=color)
        self.toggle_btn.configure(text="关闭加速" if self.acc.state in
                                  ("RUNNING", "DEGRADED", "STARTING") else "开启加速")
        status = self.acc.status()
        reasons = status.get("degraded_reasons") or []
        self.reason_var.set("；".join(reasons) if reasons else "")
        proxy = status.get("proxy", {})
        counters = proxy.get("counters", {})
        self.info_var.set("监听 %s  |  系统代理 %s  |  引擎 %s" %
                          (status.get("listen"), status.get("system_proxy", {}).get("backend"),
                           "本地隧道（无 MITM）"))
        values = {
            "listen": status.get("listen", "-"),
            "mode": status.get("mode", "-"),
            "sysproxy": status.get("system_proxy", {}).get("applied") or "未设置",
            "cached": str(status.get("resolver", {}).get("cached", 0)),
            "active": str(counters.get("active", 0)),
            "accelerated": str(counters.get("accelerated", 0)),
            "direct": str(counters.get("direct", 0)),
            "failed": str(counters.get("failed", 0)),
            "bytes": "%.1f KB" % ((counters.get("bytes_up", 0) +
                                   counters.get("bytes_down", 0)) / 1024.0),
        }
        for key, value in values.items():
            self.stat_vars[key].set(value)
        self.footer_var.set("配置: %s  |  日志: %s  |  Python %s  |  v%s" %
                            (status["config"]["loaded_dir"], status["log"]["mode"],
                             status["app"]["python"], APP_VER))

        for row in self.tree.get_children():
            self.tree.delete(row)
        for d in status.get("decisions", []):
            self.tree.insert("", tk.END, values=(d["host"], d["ip"], d["rtt_ms"],
                                                 d["source"],
                                                 "是" if d["tls_ok"] else "仅TCP"))

    # ------------------------------------------------------------------
    # 按钮动作
    # ------------------------------------------------------------------
    def _toggle(self) -> None:
        if self.acc.state in ("STOPPED", "ERROR"):
            self._async(self.acc.start)
        else:
            self._async(self.acc.stop)

    def _reprobe(self) -> None:
        self._async(self.acc.refresh_probe)

    def _refresh_ips(self) -> None:
        self._async(self.acc.refresh_ips)

    def _selftest(self) -> None:
        def job():
            from ..healthcheck import print_summary, run_selftest
            code, report = run_selftest()
            return {"code": code, "summary": report["summary"],
                    "evidence": report.get("evidence_file"),
                    "message": "自检完成：通过 %d/%d，退出码 %d" %
                               (report["summary"]["passed"],
                                report["summary"]["total"], code)}
        self._async(job)

    def _restore(self) -> None:
        def job():
            from .. import sysproxy
            from ..hosts_mode import HostsManager
            if self.acc.state in ("RUNNING", "DEGRADED"):
                self.acc.stop()
            a = sysproxy.restore_now(self.acc.cfg)
            b = HostsManager(self.acc.cfg).restore()
            return {"message": "系统代理: %s；hosts: %s" % (a.get("message"), b[1])}
        self._async(job)

    def _export_status(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            initialfile="ghacc_status.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.acc.status(), fh, ensure_ascii=False, indent=2)
            self._append_log("状态已导出: " + path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    # ------------------------------------------------------------------
    # 规则 / 设置
    # ------------------------------------------------------------------
    def _load_rules(self) -> None:
        for row in self.rule_tree.get_children():
            self.rule_tree.delete(row)
        for rule in self.acc.cfg.rules.get("domains", []):
            self.rule_tree.insert("", tk.END,
                                  values=(rule.get("host", ""),
                                          "是" if rule.get("enabled", True) else "否",
                                          rule.get("pool", "")))
        self.pool_text.delete("1.0", tk.END)
        self.pool_text.insert("1.0", json.dumps(
            self.acc.cfg.rules.get("ip_pools", {}), ensure_ascii=False, indent=2))

    def _rule_add(self) -> None:
        host = self.new_host.get().strip().lower()
        if not host or not match_rule(host, host):
            messagebox.showwarning("输入有误", "域名格式不合法")
            return
        for row in self.rule_tree.get_children():
            if self.rule_tree.item(row)["values"][0] == host:
                messagebox.showinfo("已存在", "该域名已在列表中")
                return
        self.rule_tree.insert("", tk.END, values=(host, "是", self.new_pool.get()))
        self.new_host.set("")

    def _rule_toggle(self) -> None:
        for row in self.rule_tree.selection():
            values = list(self.rule_tree.item(row)["values"])
            values[1] = "否" if values[1] == "是" else "是"
            self.rule_tree.item(row, values=values)

    def _rule_delete(self) -> None:
        for row in self.rule_tree.selection():
            self.rule_tree.delete(row)

    def _save_rules(self) -> None:
        if self.acc.state in ("RUNNING", "DEGRADED"):
            messagebox.showwarning("请先关闭", "规则修改需在加速关闭状态下应用")
            return
        try:
            pools = json.loads(self.pool_text.get("1.0", tk.END))
            if not isinstance(pools, dict):
                raise ValueError("IP 池必须是 JSON 对象")
            domains: List[Dict[str, Any]] = []
            for row in self.rule_tree.get_children():
                host, enabled, pool = self.rule_tree.item(row)["values"]
                domains.append({"host": str(host), "enabled": enabled == "是",
                                "pool": str(pool)})
            rules = dict(self.acc.cfg.rules)
            rules["domains"] = domains
            rules["ip_pools"] = pools
            old = self.acc.cfg.rules
            self.acc.cfg.set_rules(rules)
            problems = self.acc.cfg.validate()
            if problems:
                self.acc.cfg.set_rules(old)
                raise ValueError("；".join(problems[:5]))
            ok, where = self.acc.cfg.save()
            self.acc.resolver.invalidate()
            messagebox.showinfo("已保存", "规则已应用并保存到 %s" % where if ok
                                else "规则已应用到内存，但无法落盘（只读环境）")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _save_settings(self) -> None:
        if self.acc.state in ("RUNNING", "DEGRADED"):
            messagebox.showwarning("请先关闭", "设置修改需在加速关闭状态下保存")
            return
        old = dict(self.acc.cfg.settings)
        try:
            for key, (var, kind, extra) in self.set_vars.items():
                value = var.get()
                if key in ("listen_port", "probe_timeout_ms", "probe_workers"):
                    self.acc.cfg.set_setting(key, int(value))
                elif key in ("connect_timeout_s", "cache_ttl_s", "refresh_interval_s"):
                    self.acc.cfg.set_setting(key, float(value))
                else:
                    self.acc.cfg.set_setting(key, value)
            self.acc.cfg.set_setting("set_system_proxy", bool(self.auto_sysproxy.get()))
            self.acc.cfg.set_setting("probe_tls_verify", bool(self.tls_verify.get()))
            problems = self.acc.cfg.validate()
            if problems:
                raise ValueError("；".join(problems[:5]))
            ok, where = self.acc.cfg.save()
            messagebox.showinfo("已保存", "设置已保存到 %s；重启加速后生效" % where if ok
                                else "设置已应用到内存，但无法落盘（只读环境）")
        except Exception as exc:
            for key, value in old.items():
                self.acc.cfg.set_setting(key, value)
            messagebox.showerror("保存失败", str(exc))

    # ------------------------------------------------------------------
    def _clear_log_view(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _export_log(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".log",
                                            initialfile="ghacc_diagnose.log",
                                            filetypes=[("Log", "*.log"), ("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self.bus.tail(self.bus.capacity)))
            self._append_log("日志已导出: " + path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def _on_close(self) -> None:
        if self.acc.state in ("RUNNING", "DEGRADED", "STARTING", "STOPPING"):
            if not messagebox.askokcancel("退出", "加速正在运行，退出前将自动恢复系统网络设置。确认退出？"):
                return
            try:
                self.acc.stop()
            except Exception as exc:
                _LOG.error("退出清理失败: %s", exc)
        self.root.destroy()


def run_gui(acc) -> int:
    root = tk.Tk()
    AppUI(root, acc)
    root.mainloop()
    return 0
