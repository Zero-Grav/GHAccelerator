# GHAccelerator v1.0.0 — GitHub 访问加速（本地优选直连）

> **结论先行**：这是一个**完整可运行的桌面项目**，用「GitHub 官方 IP 优选 + 本地 CONNECT 隧道」改善国内网络下 GitHub 域名的解析与连接质量。参考 Watt Toolkit / DevSidecar 的产品形态（一键启停、系统代理、域名白名单、优选测速、加速日志、网络自检），但**明确不采用它们的 TLS 中间人方案**：不生成/安装根证书、不解密 HTTPS、不注入脚本、不转发到第三方镜像。
>
> 交付形态：**源码目录（零第三方运行依赖）**，附带可选 PyInstaller onedir 构建规格。全部核心链路已在本机通过自动化验证（自检 10/10、单测 22/22、真实启停冒烟通过）；**Windows 7 / macOS / Linux 目标机尚未真机验证，标注为「需目标机实测」**。

| 项 | 值 |
|---|---|
| 版本 / 构建日期 | `1.0.0` / `2026-11-07` |
| 运行期依赖 | **无**（Python ≥ 3.8 标准库） |
| 代码规模 | 15 个 Python 模块 + 3 个测试模块 + 2 份配置 + 3 端启动脚本 |
| 默认监听 | `127.0.0.1:18792`（仅回环，不对外暴露） |
| 引擎标识 | `local-CONNECT-tunnel / no-MITM / no-root-CA` |
| 完整性清单 | `SHA256SUMS.txt`（由 `scripts/verify_artifacts.py` 生成） |
| Windows exe | ✅ 已打包：`dist/GHAccelerator/`（GUI + CLI 双 exe）与 `GHAccelerator-portable.exe`，详见第 18 节 |
| 源码仓库 | <https://github.com/Zero-Grav/GHAccelerator>（MIT License，公开） |

---

## 1. 目标运行平台

| 平台 | 支持级别 | 运行时 / 前提 | 交付形态 | 备注 |
|---|---|---|---|---|
| **Windows 10 / 11 x64** | ✅ 主目标（本机已测） | Python 3.8 ~ 3.14（本机 3.14.3）/ Tk 8.6 | 源码目录；或 onedir 便携 | 系统代理走 `HKCU`，**免管理员权限** |
| Windows 7 SP1 x64 | ⚠️ 条件支持，**需目标机实测** | 必须 Python **3.8.10 x64**（3.9+ 官方不支持 Win7）；PyInstaller ≤ 5.13.2 | **onedir 便携**，不可 onefile | 运行时已 EOL，仅用于受限旧机；技术推理，非实测结论 |
| macOS 11+（Intel / Apple Silicon） | ⚠️ 条件支持，**需目标机实测** | 系统 Python 3 或 Homebrew Python；`networksetup` | 源码目录 | 未签名，Gatekeeper 会提示「未知开发者」；正式分发需 Developer ID 签名 + 公证（本项目不含） |
| Linux 桌面（Ubuntu 20.04+ / GNOME） | ⚠️ 条件支持，**需目标机实测** | `python3` + `python3-tk`；`gsettings` | 源码目录 / `--cli` | KDE / 无桌面会话请手动配置代理；不提供 AppImage |
| 服务器 / 无桌面 / 无 Tk 环境 | ✅ 支持 | 任意 Python ≥ 3.8 | `--cli` | GUI 不可用时入口自动降级 |
| WinXP / Vista / 32 位系统 | ❌ 明确不支持 | — | — | 运行时与 TLS 栈不满足 |
| iOS / Android | ❌ 明确不支持 | — | — | 桌面工具，无移动端形态 |

> 版本矩阵依据：Win7 最后可用 Python 为 3.8.10；PyInstaller 6.x bootloader 不支持 Win7；Qt6 / Tauri 2 / .NET 6+ 均不覆盖 Win7。本项目用纯标准库，因此**不存在 Qt / 原生 dll / 运行库版本矩阵问题**——这是选型的直接收益。

---

## 2. 交互方式

| 入口 | 命令 / 操作 | 场景 |
|---|---|---|
| **图形界面（默认）** | `run.bat` / `./run.sh` / `run.command`，或 `python -m ghacc` | 桌面日常使用：一键开关、状态灯、日志窗、规则与设置编辑 |
| **CLI 常驻** | `python -m ghacc --cli` | 无桌面 / 服务器 / Tk 缺失；Ctrl+C 自动恢复系统设置 |
| **自检取证** | `python -m ghacc --selftest` 或 `python scripts/selftest.py` | 现场排查；退出码 `0` 全通过 / `1` 关键失败 / `2` 有降级 |
| **状态导出** | `python -m ghacc --status`；GUI「导出状态」 | 打印/保存 JSON 状态（含引擎标识、优选表、计数器、日志模式） |
| **官方 IP 池刷新** | `python -m ghacc --refresh-ips` | 从 `api.github.com/meta` 更新候选并写回配置 |
| **应急恢复** | `python -m ghacc --restore`；GUI「应急恢复」 | 异常退出后强制恢复系统代理与 hosts |
| **版本** | `python -m ghacc --version` | 现场确认构建版本 |

GUI 交互元素：大号「开启加速 / 关闭加速」按钮；状态灯（灰=停止 / 绿=加速中 / 橙=降级运行 / 红=故障）；概览页（监听地址、模式、优选缓存数、活跃连接、加速/直连/失败计数、转发流量、优选结果表）；「目标网址与规则」页（域名增删、启停、IP 池 JSON 编辑）；「设置」页；「日志与诊断」页（实时日志、导出）；操作按钮（重新测速 / 刷新官方 IP / 运行自检 / 应急恢复 / 导出状态）。

**线程纪律**：所有启停、测速、刷新在后台线程执行，工作线程只把结果放入队列，主线程通过 `root.after` 更新控件；GUI 对象绝不在工作线程创建或操作。

---

## 3. 功能边界

### 3.1 做什么（已实现）

| 能力 | 实现 |
|---|---|
| 官方 IP 优选 | 对内置/`meta` 刷新的 GitHub 官方 IPv4 并发 TCP+TLS 探测，按 RTT 排序选优；**TLS 校验用真实域名做 SNI，只有持有 GitHub 合法证书的 IP 才入选**，机制上排除被劫持地址 |
| 本地代理 | 仅监听 `127.0.0.1` 的 HTTP/HTTPS(CONNECT) 代理；白名单域名把 TCP 钉到优选 IP 后**纯字节透传** |
| 系统代理 | Windows `HKCU`（免管理员）/ macOS `networksetup` / Linux GNOME `gsettings`，开启前快照、关闭时精确恢复 |
| 可选 hosts 模式 | 标记块 + 备份（保留最近 3 份）+ 原子写 + 精确回滚，不丢用户原有内容 |
| 可配置规则 | 目标域名白名单（精确 + `*.suffix` 通配）、逐域名 IP 池、启停开关、IP 池 JSON 直接编辑；GUI 与 `config/rules.json` 双通道 |
| 状态反馈 | 6 态状态机 + 状态灯 + 计数器 + 优选表 + 降级原因文本 + 界面常驻引擎标识 |
| 失败回退 | 见第 10 节，5 级降级链 |
| 日志与诊断 | 结构化 `diagnose.log`、2MB×3 轮转、环形缓冲（2000 行）、`--selftest` JSON 证据、隐私脱敏 |
| 异常退出自愈 | 启动检测残留、仅当代理地址仍为本程序写入值时才恢复；`atexit` + 信号钩子 + `--restore` |
| 自检协议 | 10 项检查（含 4 组反向断言），全程回环，不依赖 GitHub 可达性 |

### 3.2 明确不做什么（硬边界，不可通过配置打开）

- **不提供任何境外代理 / VPN / 中继 / 隧道节点**；候选 IP 只来自 GitHub 官方网段，流量终点始终是 GitHub 自己的服务器。
- **不做 TLS 中间人**：不生成/安装根证书、不解密 HTTPS、不读取请求内容、不注入 JS、不改写响应。浏览器验证的仍是 GitHub 官方证书。
- **不劫持非白名单流量**：非白名单域名一律按系统 DNS 原样直连（硬编码，不可配置）。`fallback` 只影响「白名单域名优选失败」时的行为。
- **不绕过网络管控 / 企业安全策略 / 终端防护 / 签名校验 / 许可证机制**；被拦截时提供降级路径与 IT 白名单说明，不提供规避方法。
- **不承诺访问依法不可访问的内容**，不宣称可突破网络审查；本工具定位是改善**已可合法访问**的 GitHub 服务的解析与连接质量。
- **不做系统驱动 / 虚拟网卡 / TUN 全局接管**，不改注册表以外的系统组件，不改浏览器/ Git / npm 全局配置。
- **不联网上报、不自动更新、不收集用户信息**；日志只记 `host:port` 与字节数，不记 URL path / query / Authorization / Cookie。
- 参考产品中的镜像站转发、包管理器源改写、账号切换、本地令牌、库存管理等**与本项目定位无关的能力，一律不实现**。

> 合规提示：企业网络中使用前请与 IT 确认代理策略白名单；需要白名单时可提供 `SHA256SUMS.txt` 中的哈希、版本号与用途说明。本项目不协助关闭或绕过任何安全防护。

---

## 4. 假设清单（不预设技术栈，先列假设再推导选型）

按「开工五问」逐项给出**最严苛假设**。方案在这些假设下成立；若假设不成立，右侧列明应对。

| # | 问题 | 本次采用的假设 | 若不成立的应对 |
|---|---|---|---|
| A1 | 目标平台与版本 | 主目标 Win10/11 x64；可能遇到 Win7 SP1 x64 / macOS 11+ / Ubuntu 20.04+ | Win7 用 Python 3.8.10 独立构建链；macOS/Linux 走源码 + `--cli`；均需目标机实测 |
| A2 | 环境是否受限 | **假设最严苛**：无管理员权限、程序目录只读、禁安装、禁联网、终端管控、禁批量释放文件 | 默认 proxy 模式零注册表写系统目录、零管理员；配置/日志/状态四级降级到内存；分发用 onedir 而非 onefile |
| A3 | 硬件档位 | 4GB 内存 / 机械盘 / 高 DPI 屏；并发连接 ≤ 50 | 单进程多线程、探测并发默认 12、无大内存缓存；GUI 用 Tk（无 WebView 内存开销） |
| A4 | 分发方式 | U 盘 / 内网共享 / 目录便携优先 | `onedir` 零解压零注册表；附 `SHA256SUMS.txt`；不默认做安装包 |
| A5 | 能力依赖 | 只需 socket / TLS / DNS / 本地 GUI；**不需要** OCR、模型、数据库、硬件外设 | 无原生库 → 无 dll/so/dylib 加载失败面 |
| A6 | 网络前提 | GitHub 域名**本身可合法访问**，问题是 DNS 污染、解析慢、链路抖动、IP 频繁失效 | 若域名级不可达，优选也无效 → 明确失败并回退直连，不伪造成功状态 |
| A7 | 安全前提 | 不引入任何第三方信任根；用户不愿把 HTTPS 解密权交给本地工具 | 选「安全模式」路线（无 MITM），见第 5 节 |
| A8 | 权限前提 | 拿不到管理员权限 | Windows 系统代理写 `HKCU`；hosts 模式设为**可选**且失败自动降级 |
| A9 | 合规前提 | 企业环境可能拦截本地监听/代理设置 | 不绕过；提供降级路径、哈希与 IT 白名单说明 |
| A10 | 共存前提 | 机器上可能已有其他代理软件 | 端口可配置；启动自愈只恢复「仍等于本程序写入值」的代理，不触碰他人设置 |

**待确认项（未问清，按最严苛假设出方案，不默认为干净环境）**：目标机是否已有全局代理 / TUN 软件；企业终端管控是否拦截 `127.0.0.1` 监听；是否需要 macOS 公证与 Windows 代码签名；真实目标系统的 Python 版本与 `python3-tk` 是否预装；IT 白名单流程与对接人。

---

## 5. 环境-方案矩阵与技术选型取舍

### 5.1 环境-方案矩阵

| 环境特征 | 运行时 | GUI | 交付形态 | 必做加固 |
|---|---|---|---|---|
| Win10/11 普通办公机 | Python 3.8~3.14 标准库 | Tkinter | 源码目录 / onedir | 系统代理快照 + 精确恢复 |
| Win7 SP1 x64 | Python 3.8.10（独立构建） | Tkinter 8.6 | **onedir**（禁 onefile） | 运行库随包 + 真机复验 |
| 终端管控 / 禁装 / 只读盘 | 同上 | 同上 / `--cli` | **onedir 便携**（零解压零注册表） | 配置与日志纯内存兜底 |
| macOS 11+ | 系统/Homebrew Python | Tkinter | 源码目录 | 签名+公证需另做；未签名会触发 Gatekeeper |
| Linux GNOME | python3 + python3-tk | Tkinter | 源码目录 | gsettings 失败时改手动代理 |
| 无桌面 / 服务器 | 同上 | 无（自动降级） | `--cli` | 信号钩子 + atexit 恢复 |

### 5.2 三个备选与取舍

选型判据严格按顺序：**① 目标平台支持度（硬门槛）② 关键库生态 ③ 体积与启动 ④ 团队熟悉度**。

| 方案 | 优点 | 为什么不选 |
|---|---|---|
| **A. Python 3.8+ 纯标准库 + Tkinter（本次采用）** | 零第三方依赖 → 无 dll/运行库矩阵；代码完全可审计；Win7 有可用链；体积 ≈ 数百 KB；`socketserver`/`ssl`/`http.client` 原生够用 | GUI 观感朴素（本项目不做视觉设计，接受） |
| B. .NET 8 / Avalonia（Watt Toolkit 路线） | 跨平台 UI 精致、YARP 反代成熟 | .NET 6+ 不支持 Win7；自包含发布 +60MB；引入运行库分发与版本矩阵；对「只读盘 + 无管理员」不友好 |
| C. Electron + Node（DevSidecar 路线） | 生态全、界面现代 | 体积 150~300MB、启动 5~15s、依赖树庞大（实测社区版约 1400 个包）；Tauri 2 明确不支持 Win7；与「低配 + 受限环境」假设冲突 |
| D. Go 单二进制 | 无运行时、并发模型好 | GUI 需额外原生库；交叉编译与证书信任处理复杂；对本项目收益不足以抵消验证成本 |

**为什么不照搬参考产品的默认模式（自签根 CA + MITM）**：

1. 调研显示默认模式把本地根 CA 放入信任链，理论上可读取 GitHub Token；社区已报告 Python 3.13+ 严格 X.509 校验会拒绝 AKI 为空的叶证书（本机即 Python 3.14），以及 Firefox 需手动导入证书。
2. MITM + hosts 改写需要管理员权限（Win UAC / Linux pkexec / macOS osascript），与 A8 假设冲突。
3. 加速 GitHub 的主要收益来自「解决 DNS 污染 / 选到可达官方 IP」；**用 SNI 不变的 CONNECT 隧道指向官方 IP 即可拿到这部分收益**，TLS 仍由浏览器与 GitHub 直接完成。
4. 边界收敛：不解密、不改写，日志不接触请求内容，安全与合规面小一个数量级。

**取舍代价（诚实标注）**：无 MITM 意味着不能做镜像站替换、URL 重写、Release 链接改写、响应缓存等「增强模式」能力；遇到域名级不可用或 IP 全失效时，只能回退直连，加速效果弱于带镜像的默认模式。这是为「免管理员 + 无信任根 + 可审计」主动交换的。

---

## 6. 架构与数据流

```
浏览器 / Git
   │  CONNECT github.com:443   （系统代理 127.0.0.1:18792，HKCU）
   ▼
proxy_server.py  ── 白名单判定（config.rule_for）
   │                    │
   │ 命中               │ 未命中 → 原样直连系统 DNS（不劫持）
   ▼                    │
resolver.py  ── TTL 缓存 ──► 失效/未命中 ──► prober.py
                                              │ TCP + TLS(SNI=真实域名, 校验证书)
                                              ▼
                                        ip_pool.py（官方 IPv4 段）
   │ Decision(ip)
   ▼
socket.connect(优选官方 IP, 443) ── 200 Connection Established ── 双向字节透传
   ▼
浏览器 ⇄ GitHub 官方服务器直接完成 TLS（官方证书，无中间人）
```

| 模块 | 职责 |
|---|---|
| `config.py` | 配置加载/校验/保存、四级目录降级、白名单匹配、安全边界校验 |
| `logger.py` | 结构化日志、环形缓冲、目录降级、轮转、脱敏 |
| `ip_pool.py` | 官方 IP 池、`api.github.com/meta` 刷新（pinned-IP + 正常 SNI 校验） |
| `prober.py` | 并发 TCP/TLS 测速优选，全链路不抛异常 |
| `resolver.py` | 决策缓存（TTL）、三层失败回退、统计 |
| `proxy_server.py` | 本地代理、白名单分流、DNS 硬超时、双向 relay、计数器 |
| `sysproxy.py` | 三平台系统代理快照/设置/恢复、残留自愈 |
| `hosts_mode.py` | 可选 hosts 标记块/备份/原子写/精确回滚 |
| `app.py` | 6 态状态机、启停编排、降级决策、atexit/信号钩子 |
| `healthcheck.py` | `--selftest` 协议（10 项 + 4 组反向断言） |
| `ui/tkinter_ui.py` | 桌面界面（4 个标签页） |
| `__main__.py` | GUI / CLI / 自检 / 恢复 / 刷新 / 状态 多入口 |

---

## 7. 运行入口（三端）

| 平台 | 启动 | 自检 | CLI |
|---|---|---|---|
| Windows | `run.bat` | `run.bat --selftest` | `run.bat --cli` |
| Linux | `./run.sh` | `./run.sh --selftest` | `./run.sh --cli` |
| macOS | `run.command`（或 `./run.sh`） | `./run.sh --selftest` | `./run.sh --cli` |
| 任意（源码直跑） | `python -m ghacc` | `python scripts/selftest.py` | `python -m ghacc --cli` |

环境变量：`GHACC_CONFIG_DIR`（配置目录）、`GHACC_STATE_DIR`（状态/备份目录）、`PYTHONPATH`（指向 `src/`）。CLI 支持 `--config-dir` / `--base-dir` / `--log-level` / `--no-auto-start`。

**只读环境**：程序读不到 `config/*.json` 时回落到内置默认配置；用户改动优先写 `~/.ghacc/config`；全不可写则纯内存运行并在界面标注，不会启动失败。

---

## 8. 已实现功能清单

| # | 需求项 | 实现 | 位置 | 验证 |
|---|---|---|---|---|
| F1 | **一键开启与关闭** | 6 态状态机 + 幂等 start/stop，严格逆序撤回所有系统级副作用 | `app.py` / `tkinter_ui.py` | 真实启停冒烟、R5 快照恢复断言 |
| F2 | **加速状态反馈** | 状态灯（4 色）+ 文字 + 降级原因 + 计数器 + 优选表 + 常驻引擎标识 + JSON 状态导出 | `tkinter_ui.py` / `app.status()` | GUI 构建冒烟（4 tabs）、`--status` |
| F3 | **目标网址与规则可配置** | 白名单精确/通配匹配、逐域名 IP 池、启停开关、GUI 编辑 + `rules.json`、官方 meta 刷新、设置持久化 | `config.py` / `rules.json` / UI 规则页 | 单测 10 项配置用例、配置校验断言 |
| F4 | **失败回退** | 5 级降级：优选 TLS→TCP-only→系统 DNS 直连→DEGRADED→ERROR 全撤回；DNS 硬超时；连接级重试 | `resolver.py` / `proxy_server.py` / `app.py` | R1/R2/R3/R4 反向断言 |
| F5 | **日志与异常处理** | `diagnose.log`（2MB×3 轮转）、四级目录降级、环形缓冲、异常统一带类型名与栈、隐私脱敏、`--selftest` 证据 | `logger.py` / `healthcheck.py` | R3 只读目录降级、自检 JSON |
| F6 | 官方 IP 优选与证书校验 | 并发探测 + SNI 证书验证排除劫持 IP | `prober.py` / `ip_pool.py` | 实网优选结果（见第 13 节） |
| F7 | 系统代理自动设置/恢复 | HKCU / networksetup / gsettings + 快照 | `sysproxy.py` | R5 快照一致性断言 |
| F8 | 异常退出残留自愈 | 启动检测 + 条件恢复 + atexit + 信号 + `--restore` | `sysproxy.recover_if_needed` / `app.py` | `--restore` 入口、R5 |
| F9 | 可选 hosts 模式 | 标记块、备份、原子写、精确回滚 | `hosts_mode.py` | hosts 回滚断言（用户内容保留） |
| F10 | 自检与现场取证 | 10 项检查、4 组反向断言、JSON 证据、退出码协议 | `healthcheck.py` | 自检 10/10 |
| F11 | 三端启动与 CLI 降级 | `run.bat` / `run.sh` / `run.command` / `--cli` | 根目录脚本 | 入口冒烟 |
| F12 | 可选打包与产物校验 | PyInstaller onedir spec、构建脚本、SHA256 清单 | `scripts/` | 产物校验 6/6 |

---

## 9. 配置说明

### `config/settings.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `listen_host` / `listen_port` | `127.0.0.1` / `18792` | 仅允许回环（校验强制），端口可改 |
| `mode` | `proxy` | `proxy` / `hosts` / `both`；hosts 需系统文件写权限 |
| `set_system_proxy` | `true` | 关闭后仅提供本地代理地址，由用户手动配置 |
| `probe_timeout_ms` / `probe_workers` | `1200` / `12` | 单 IP 探测超时与并发数 |
| `probe_tls_verify` | `true` | **强烈建议开启**；关闭则只按 TCP RTT 选优 |
| `connect_timeout_s` | `8.0` | 上游连接超时；DNS 解析硬超时取 `min(该值, 1.5s)` |
| `cache_ttl_s` / `refresh_interval_s` | `600` / `0` | 优选缓存 TTL；后台重测间隔（0=关） |
| `fallback` | `direct` | 白名单优选失败：`direct`=回退系统 DNS 直连 / `reject`=返回 502 便于排障 |
| `log_level` / `log_to_file` / `log_max_lines` | `INFO` / `true` / `2000` | 日志级别、落盘开关、环形缓冲容量 |
| `system_proxy_bypass` | `localhost;127.*;10.*;192.168.*;<local>` | 系统代理绕过列表 |

### `config/rules.json`

- `domains[]`：`host`（支持 `*.suffix` 单级通配）、`enabled`、`pool`。默认 12 条，其中 `objects.githubusercontent.com` 等对象存储域名因 IP 变化频繁默认关闭。
- `ip_pools`：3 组官方 IPv4 候选 —— `github_web`（140.82.112.0/20、20.205.243.0/24 段）、`github_api`、`github_cdn`（Fastly 185.199.108.0/22）。
  > 内置地址是官方公开网段的**常用示例**，会随官方调整失效；失效项被 TLS 校验自动淘汰，不会造成错误连接。可用 `--refresh-ips` 从 `api.github.com/meta` 的 `web/api/git/pages/hooks/importer` 段补充候选（失败则完整保留内置池）。
- `fallback`：规则级回退策略（与 settings 一致）。
- **非白名单域名一律直连**，此项不可通过配置关闭。

---

## 10. 失败回退矩阵

| 故障 | 系统行为 | 状态 | 用户可见 |
|---|---|---|---|
| 全部候选 IP 的 TLS 校验失败 | 降级为 TCP-only 重测（记 WARNING，优选表标注 `tcp-fallback`） | RUNNING | 概览页来源列、日志 |
| 全部候选 IP 不可达 | 回退系统 DNS 直连；DNS 解析有 1.5s 硬超时 | RUNNING | 概览页直连计数、日志 ERROR |
| 优选 IP 连接失败 | 自动重试一次系统 DNS 直连 | RUNNING | 日志 WARNING |
| 上游完全不可达 | 返回 `502 Bad Gateway`，代理进程存活 | RUNNING | 失败计数 +1、日志 |
| 系统代理设置被拒（只读注册表 / 无权限 / 管控） | 保留本地代理，提示手动配置地址 | **DEGRADED** | 橙色状态灯 + 降级原因文本 |
| hosts 写入失败（无管理员 / 只读盘） | 跳过 hosts 模式，其余能力保留 | DEGRADED | 降级原因 + 日志 |
| 日志/配置/状态目录全部不可写 | 降级纯内存，功能不中断 | RUNNING/DEGRADED | 界面底部显示 `log: memory` |
| 本地端口被占用 | 启动失败，**撤回所有已生效副作用** | ERROR | 明确错误文本，可改端口重试 |
| 配置校验失败 | 拒绝半启动 | ERROR | 列出前 4 条具体问题 |
| 进程崩溃 / 强杀 / 断电重启 | 下次启动检测残留，仅当代理地址仍等于本程序写入值才按快照恢复 | STOPPED（自愈） | 启动日志 WARNING；或手动 `--restore` |

> `fallback=reject` 仅作用于「白名单域名且优选失败」，用于排障；**不会**让非白名单网站打不开。

---

## 11. 日志与异常处理

- **位置降级链**：`~/.ghacc/logs/diagnose.log` → 程序目录 `logs/` → 系统临时目录 `ghacc/logs/` → 纯内存。每级做真实写入探针，失败即跳过、不抛异常。
- **格式**：`时间 | 级别 | 模块 | 关键字段`，UTF-8；单文件 ≤ 2MB，轮转 3 份（避免塞满只读盘/小容量 U 盘）。
- **隐私**：只记 `host:port`、优选 IP、RTT、字节数、状态迁移；**不记 URL path / query / header / 凭据**。
- **异常**：统一 `log_exception()` 带类型名、位置与栈；所有系统级副作用撤回放在各自 `try` 中，互不阻断；`stop()` 用 `finally` 保证状态归位。
- **`--selftest` 退出码**：`0` 全通过 / `1` 关键失败 / `2` 有降级项；输出 `selftest_<timestamp>.json` 证据文件。
- **现场取证清单**（反馈时一并提供）：① `selftest` 输出（`selftest.txt` 或 JSON）② `diagnose.log` ③ GUI 状态截图（含引擎标识）。三层证据覆盖「环境 → 过程 → 结果」。

---

## 12. 防断网设计（针对参考产品头号现场故障）

调研中两款参考产品的共同高频故障是：**异常退出后系统代理未还原，导致整机断网 / 重启后暂时上不了网**。本项目的结构性对策：

1. 开启前用 `backend.read()` 保存**完整快照**（启用标志、地址、端口、绕过列表、原始值）。
2. 关闭时按快照**精确恢复**，不是简单「把代理关掉」——用户原本就有的代理不会被抹掉。
3. 状态文件 `sysproxy_state.json` 落盘；启动时若发现残留，**仅当当前代理地址仍等于本程序写入值**才恢复，否则不触碰（避免误伤其他代理软件或用户后续设置）。
4. `atexit` + `SIGINT/SIGTERM` 钩子 + `start()` 失败时的 `_cleanup_all()` 三重保障。
5. `--restore` / GUI「应急恢复」独立入口，不依赖程序曾正常启动。
6. **反向断言 R5**：`开启 → 关闭 → 系统代理状态 == 开启前快照`，写进 selftest 防回归。

---

## 13. 验证记录（本机实测数字）

环境：`Windows-10-10.0.19045-SP0` / `Python 3.14.3` / `Tk 8.6` / `frozen=False`，2026-11-07。

| 验证项 | 命令 | 结果 |
|---|---|---|
| 源码语法编译 | `python -m compileall -q src tests scripts main.py` | **通过**，15 个模块 |
| 自检协议 | `python scripts/selftest.py` | **10/10 PASS**，退出码 `0`，耗时 `5481ms`；反向断言 `{R1_dns_bounded, R2, R5, R3} = 全 True` |
| 单元测试 | `python -m unittest discover -s tests -v` | **22/22 OK**，`2.731s` |
| 真实配置启停 | `Accelerator.start() → status() → stop()` | `STOPPED → RUNNING → STOPPED`；`127.0.0.1:18792` 监听成功；`cleanup={'proxy': {'ok': True}}`，无残留 |
| 实网 IP 优选 | 同上（TLS 校验开启） | `github.com → 20.200.245.247`，RTT `221.8ms`，`source=tls`；`raw.githubusercontent.com → 185.199.111.133`，RTT `339.5ms`，`source=tls` |
| GUI 构建 | `AppUI(root, acc)` + `root.update()` | **通过**，4 个标签页，状态 `STOPPED` |
| 产物校验 | `python scripts/verify_artifacts.py` | 见 `SHA256SUMS.txt`；必需文件 / 配置校验 / 语法 / 条目数 / selftest / 哈希 六项 |

**本次自检捕获并修复的真实缺陷（T1）**：

| 项 | 内容 |
|---|---|
| 现象 | 优选全部失败后回退直连，客户端等待 6s 超时收不到 502 |
| 根因 | `socket.create_connection(timeout=...)` 的超时**只作用于 connect，不覆盖 `getaddrinfo`**；DNS 阻塞导致「回退比不加速还慢」 |
| 修复 | 拆出 `_resolve_infos()`：daemon 线程 + `join(1.5s)` 硬超时；IP 字面量跳过 DNS；用已解析地址建连避免二次解析 |
| 验证 | R1 从 `6005ms / FAIL(TimeoutError)` → `1512ms / PASS(502)`；新增断言 `elapsed_ms < 4000` 防回归 |
| checklist 新增 | 「任何回退直连路径必须有 DNS 硬超时，且必须在客户端超时前返回明确错误」 |

> **未验证项（需目标机实测）**：Win7 SP1 + Python 3.8.10 实机启动；macOS `networksetup` 设置/恢复与 Gatekeeper 行为；Linux GNOME `gsettings` 与无桌面 `--cli`；PyInstaller onedir 冻结产物（本机未安装 PyInstaller，只提供 spec 与构建脚本）；真实浏览器/Git 端到端加速效果与长时稳定性；企业终端管控对本机回环监听的拦截行为；`mode=hosts/both` 在真实系统 hosts 文件上的写入（自检只用临时文件）。

---

## 14. 交付物清单

```
gh-accelerator/
├── README.md                      本交付说明
├── requirements.txt               运行期零依赖声明（含构建期版本参考）
├── main.py                        冻结包入口
├── run.bat / run.sh / run.command 三端启动脚本
├── SHA256SUMS.txt                 完整性清单（verify_artifacts.py 生成）
├── config/
│   ├── settings.json              运行参数（必要配置）
│   └── rules.json                 目标域名白名单 + 官方 IP 池（必要配置）
├── src/ghacc/
│   ├── __init__.py / version.py / __main__.py
│   ├── config.py / logger.py / ip_pool.py / prober.py / resolver.py
│   ├── proxy_server.py / sysproxy.py / hosts_mode.py
│   ├── app.py / healthcheck.py
│   └── ui/__init__.py / ui/tkinter_ui.py
├── scripts/
│   ├── selftest.py                独立自检入口
│   ├── verify_artifacts.py        产物校验 + 哈希清单
│   ├── app.spec                   PyInstaller onedir 规格
│   └── build_windows.bat          Windows 构建脚本（含旧产物改名让位）
├── tests/
│   ├── __init__.py / test_config.py / test_resolver.py / test_proxy.py
└── logs/                          运行时日志目录（可写时）
```

---

## 15. 打包、分发与回滚

- **首选形态 onedir 目录便携**：禁装 / 只读盘 / 终端管控环境下，onefile 每次启动向 `%TEMP%` 批量释放文件容易被判为「安装行为」。onedir 零解压、零注册表、免管理员。
- 构建：`pip install pyinstaller==6.11.1`（Win7 目标改用 `5.13.2` + Python 3.8.10 独立环境）→ `scripts\build_windows.bat`。脚本会先把旧产物目录改名让位，再逐个 `Test-Path` 校验，**不信「打包日志显示成功」**。
- **签名**：本项目未签名。正式分发需 Windows 代码签名（SmartScreen 信誉）与 macOS `codesign` + `notarytool` 公证；无证书时提供 `SHA256SUMS.txt` 供 IT 加白名单，不教导用户关闭防护。
- **回滚**：一键关闭即恢复系统代理/hosts；异常退出用 `--restore`；版本回退保留旧目录（`GHAccelerator_old_*`）即可切回。
- **EOL 风险**：Python 3.8 已于 2024-10 停止安全支持，仅在受限旧机使用，并应限制其网络暴露面（本项目只监听回环）。

---

## 16. 已知限制

1. 无 MITM → 不支持镜像站替换、URL/响应改写、脚本注入、Release 链接加速；域名级不可达时只能回退直连。
2. TLS 校验依赖本机系统 CA 信任库；CA 缺失时会整体降级为 TCP-only 优选（可用性提高，安全性下降，界面与日志会标注）。
3. Firefox 等不走系统代理设置的客户端需手动配置代理地址；HTTPS 代理设为 `127.0.0.1:18792` 即可，无需导入任何证书。
4. 明文 HTTP 请求按「单请求转发」处理；GitHub 主流流量均走 HTTPS/CONNECT，不受影响。
5. 系统代理在 Windows 依赖 WinINet 刷新通知；某些管控环境刷新失败时，需重开浏览器或手动应用设置。
6. 与其他全局代理 / TUN 模式软件可能冲突；建议二选一，或把 `set_system_proxy=false` 后手动指定代理链。
7. `objects.githubusercontent.com` 等对象存储域名 IP 变化频繁，默认关闭加速，需要时自行启用并刷新 IP 池。

---

## 17. 合规声明

本工具仅用于改善**可合法访问**的 GitHub 相关服务的域名解析与连接质量，候选项全部来自 GitHub 官方公布的 IP 网段，流量终点为 GitHub 自有服务器。工具不提供、不内置、不连接任何第三方代理节点或境外中继，不解密用户 HTTPS 流量，不收集或上报数据，不协助绕过网络管控、企业安全策略或签名校验。

使用者应自行确认其行为符合所在网络的使用规范与所在地法律法规；企业环境请先取得 IT 部门许可。因网络环境差异导致的可用性差异，以目标机实测为准。

---

## 18. Windows exe 打包产物（2026-11-07 实测）

> 本仓库**不包含 exe 二进制**，只放源码、配置、文档与构建脚本；请按本节命令本地重建。发布用二进制包建议走 GitHub Releases / 内网共享，避免把 30MB 级二进制写进 git 历史。

构建环境：`Python 3.14.3` + `PyInstaller 6.22.3`（contrib hooks 2026.9），Windows 10 19045 x64。
构建命令：`scripts\build_windows.bat`；或分别执行
`python -m PyInstaller --noconfirm --distpath dist --workpath build\onedir scripts\app.spec`
与 `python -m PyInstaller --noconfirm --distpath dist --workpath build\onefile scripts\app-onefile.spec`。

| 产物 | 形态 | 说明 |
|---|---|---|
| `dist/GHAccelerator/GHAccelerator.exe` | onedir / `console=False` | 双击即用 GUI，无黑窗 |
| `dist/GHAccelerator/GHAccelerator-cli.exe` | onedir / `console=True` | `--selftest` / `--status` / `--restore` / `--cli` |
| `dist/GHAccelerator/_internal/` | 共享运行时 | 两个 exe 共用；含 `_tkinter.pyd`、`_tcl_data`、`_tk_data`、`VCRUNTIME140*.dll`、`_ssl.pyd` 与随包 config |
| `dist/GHAccelerator-portable.exe` | onefile / `console=False` | 13,454,589 B；启动时解压到 `%TEMP%`，约 2~6 秒 |
| `GHAccelerator-1.0.0-win64.zip` | 便携 ZIP | onedir 目录 + README + `SHA256SUMS.txt` |

**为什么是两个 exe 共用一个目录**：GUI 版 `console=False` 双击体验好，但看不到 stdout；自检与现场取证必须有控制台输出。同一份 `_internal` 下再加一个 CLI 入口，只多约 2.8 MB，不重复携带运行时。

**冻结包的配置加载顺序**：`~/.ghacc/config`（用户改动）→ exe 同级 `config/`（现场可编辑）→ `_MEIPASS/config`（随包兜底）→ 内置默认。GUI 保存的设置不会被随包配置覆盖。

**逐个产物验证结果**（不看构建日志，只认产物自身自检）：

| 检查 | 结果 |
|---|---|
| onedir CLI `--version` / `--status` | rc=0；`frozen=true`；`loaded_dir=dist\GHAccelerator\config` |
| onedir CLI `--selftest` | **10/10 PASS**，`frozen=True`，4263ms，rc=0，4 组反向断言全 True |
| onedir GUI `--version` | rc=0（bootloader 与顶层 import 链正常） |
| onefile `--version` / `--selftest` | rc=0；**10/10 PASS**，4376ms（解压后模块与数据收集完整） |
| 中文输出编码 | `--status` stdout 为纯 JSON、UTF-8，中文完整、无 `?` 替换字符 |
| 运行库 | `_internal` 内 Tk 数据、VC 运行库、SSL/socket pyd 齐全 |

**打包过程中捕获并修复的缺陷**：

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| T2 | 首次构建报 `script '...\main.py' not found` | PyInstaller 6.22 的 `SPECPATH` 指向工作目录而非 spec 目录，项目根算错一层 | spec 改用「`main.py` + `src/ghacc/` 锚点」自动定位，不依赖版本相关变量 |
| T3 | exe 执行 `--selftest` 抛 `AttributeError: Config.project_base_dir` | `healthcheck.py` 误把模块级函数当类方法；源码自检因传入 `base_dir` 被短路，从未覆盖该路径 | 改用模块级 `project_base_dir()`；`scripts/selftest.py` 改为不传 `base_dir`，与 exe 走同一路径 |
| T4 | 冻结 CLI 的 `--status` 中文变成 `?` | console exe 在管道/重定向下 stdout 退化为 locale 编码 | 入口 `reconfigure(encoding="utf-8")`；真实控制台下额外切 CP65001 |
| T5 | GUI 保存的设置下次启动被随包配置覆盖 | 冻结包候选顺序把 `_MEIPASS/config` 放在最前 | 改为「用户 > exe 同级 > 随包」 |

> 把 T3 纳入回归比修 bug 本身更重要：**让源码自检与 exe 自检走同一条参数路径**，否则「源码全绿、exe 崩溃」这类覆盖盲区会反复出现。

**取舍与限制**：

- **onedir 为首选**：零解压、零注册表、免管理员；onefile 虽只有一个文件，但每次启动向 `%TEMP%` 批量释放，在企业终端管控下可能被判为安装行为。
- **未签名**：SmartScreen 可能提示「未知发布者」；附 `SHA256SUMS.txt` 供 IT 加白名单，不引导关闭防护。正式分发需 OV/EV 代码签名。
- **未构建 Win7 版**：需 Python 3.8.10 + PyInstaller 5.13.2 独立构建链并**需目标机实测**；当前 exe 基于 Python 3.14，不支持 Win7。
- **未做 macOS / Linux 二进制**；源码运行方式见第 1、7 节。
- 单文件版的 `--selftest` 输出需重定向查看：`GHAccelerator-portable.exe --selftest > selftest.txt 2>&1`，或直接读 `~/.ghacc/logs/selftest_*.json` 证据文件。

**现场取证三步（exe 版）**：① 在便携目录执行 `GHAccelerator-cli.exe --selftest > selftest.txt 2>&1`；② 取 `~/.ghacc/logs/diagnose.log`；③ 取 GUI 状态截图（含引擎标识）。三个文件一起回传。
