# GHAccelerator

> GitHub 访问加速 · v1.0.0 · MIT · [Releases](https://github.com/Zero-Grav/GHAccelerator/releases)

[![selftest](https://github.com/Zero-Grav/GHAccelerator/actions/workflows/selftest.yml/badge.svg)](https://github.com/Zero-Grav/GHAccelerator/actions/workflows/selftest.yml)

从 **GitHub 官方 IP 网段**中测速选出当前最优地址，把本地 CONNECT 隧道钉到该 IP，改善国内网络下的 DNS 污染与连接抖动。

**不装根证书 · 不解密 HTTPS · 不需要管理员权限 · 零第三方运行依赖**（Python ≥ 3.8 标准库 + Tkinter）。

---

## 边界（先看这个）

| ✅ 做 | ❌ 不做 |
|---|---|
| 白名单 GitHub 域名的官方 IP 优选 | 境外代理 / VPN / 中继节点 |
| 本地 `127.0.0.1` 代理 + 系统代理（HKCU） | TLS 中间人 / 自签 CA / 脚本注入 |
| 一键启停、状态反馈、规则可配置 | 劫持非白名单流量（一律原样直连） |
| 失败回退、结构化日志、`--selftest` | 绕过网络管控 / 企业安全策略 |
| 只记 host:port 与字节数，不联网上报 | 承诺访问依法不可访问的内容 |

候选 IP 只来自 GitHub 官方网段，流量终点始终是 GitHub 自有服务器；浏览器验证的仍是 GitHub 官方证书。企业环境请先取得 IT 许可后再使用。

## 运行

```bash
python -m ghacc                 # 图形界面（默认）
python -m ghacc --cli            # 无桌面 / 服务器
python -m ghacc --selftest       # 自检 + 取证（退出码 0/1/2）
python scripts/selftest.py      # 同上，无需安装包
```

三端启动脚本：`run.bat` / `run.sh` / `run.command`；GUI 不可用时自动降级 CLI。

**Windows 免安装**：到 [Releases](https://github.com/Zero-Grav/GHAccelerator/releases) 下载 `GHAccelerator-1.0.0-win64.zip`，解压后双击 `GHAccelerator.exe`；`GHAccelerator-cli.exe` 用于 `--selftest` / `--status` / `--restore`。单文件版 `GHAccelerator-portable.exe` 启动时解压到 `%TEMP%`（约 2~6 秒），企业管控环境建议用便携目录版。

## 配置

| 文件 | 内容 |
|---|---|
| `config/settings.json` | 监听端口（默认 `18792`）、模式、探测超时、TLS 校验开关、缓存 TTL、日志级别、回退策略 |
| `config/rules.json` | 目标域名白名单（精确 + `*.suffix` 通配）、三个官方 IP 池、`fallback` |

GUI 的「目标网址与规则」「设置」两个标签页可直接编辑并保存；用户改动优先写 `~/.ghacc/config`，程序目录只读时自动降级为纯内存配置。`--refresh-ips` 从 `api.github.com/meta` 补充候选，失败则完整保留内置池。

## 原理

```
浏览器 → 系统代理 127.0.0.1:18792 → 本地代理
  CONNECT github.com:443
   → 优选：并发 TCP 测速 + TLS 证书校验（SNI=真实域名，淘汰被劫持 IP）
   → socket.connect(官方 IP, 443) → 200 Connection Established → 纯字节透传
浏览器 ⇄ GitHub 官方服务器直接完成 TLS 握手（官方证书，无中间人）
```

## 关键能力

| 能力 | 说明 |
|---|---|
| 一键启停 | 6 态状态机；开启前快照系统代理，关闭时**精确恢复**；异常退出后启动自愈 + `--restore` |
| 状态反馈 | 4 色状态灯、降级原因、计数器与优选结果表、常驻引擎标识、`--status` JSON 导出 |
| 失败回退 | TLS 校验失败 → TCP-only 重测 → 系统 DNS 直连 → DEGRADED → ERROR 全撤回；DNS 解析带 1.5s 硬超时 |
| 日志/异常 | `diagnose.log`（2MB×3 轮转）→ 目录四级降级到纯内存；日志脱敏；`selftest_<ts>.json` 证据 |
| 可选 hosts 模式 | 标记块 + 备份 + 原子写 + 精确回滚（需管理员权限，默认关闭） |

## 自检与取证

`python -m ghacc --selftest` 全部在回环内完成，不依赖 GitHub 是否可达，含 4 组反向断言（优选全失败回退、系统代理只读降级、日志目录只读、启停快照一致性）。现场反馈三件套：selftest 输出、`diagnose.log`、GUI 状态截图。

## 平台

| 平台 | 状态 |
|---|---|
| Windows 10/11 x64 | ✅ 主目标，本机已测 + exe 已打包 |
| Windows 7 SP1 x64 | ⚠️ 需 Python 3.8.10 独立构建链，**需目标机实测**；现有 exe 基于 Py3.14，不支持 Win7 |
| macOS 11+ / Linux GNOME | ⚠️ 源码运行；签名/公证、系统代理后端**需目标机实测** |
| 无桌面 / 服务器 | ✅ `--cli` |

CI：`.github/workflows/selftest.yml` 在 ubuntu / macOS / windows 三平台跑 selftest + 单测（覆盖 Python 3.8 / 3.10 / 3.11 / 3.14）。

## 构建与发布

```bash
scripts\build_windows.bat        # onedir 双 exe + onefile 单文件
python scripts/verify_artifacts.py --dir dist/GHAccelerator --frozen   # 产物校验（含 exe 自检）
```

发布流程、哈希校验与**代码签名落地指南**见 [docs/RELEASE.md](docs/RELEASE.md)。当前 exe **未签名**，SmartScreen 可能提示「未知发布者」；仓库附 `SHA256SUMS.txt`、Release 附哈希清单供 IT 白名单，不引导关闭防护。

## 文档与许可

- [docs/DESIGN.md](docs/DESIGN.md) — 完整设计、环境假设、平台矩阵、验证数据与缺陷闭环（T1–T5）
- [docs/RELEASE.md](docs/RELEASE.md) — Release 流程、哈希校验、代码签名指南
- MIT License，见 [LICENSE](LICENSE)

> 本工具仅用于改善**可合法访问**的 GitHub 服务的解析与连接质量，不提供任何境外代理节点、不解密用户流量、不绕过网络管控。使用者应自行确认符合所在网络规范与所在地法律法规。
