# 发布、哈希校验与代码签名

## 当前状态（v1.0.0）

Release `v1.0.0` 资产：

| 资产 | 说明 |
|---|---|
| `GHAccelerator-1.0.0-win64.zip` | onedir 便携目录（GUI + CLI 双 exe）、单文件 exe、README、完整 `SHA256SUMS.txt` |
| `GHAccelerator-portable.exe` | onefile 单文件，启动时解压到 `%TEMP%` |
| `SHA256SUMS-release.txt` | 上述两个资产的 SHA256 |

**exe 当前未签名**。没有代码签名证书时不能签名，也不应伪造签名；用「哈希 + 版本 + 用途说明」走 IT 白名单流程。

## 校验下载

PowerShell：

```powershell
Get-FileHash .\GHAccelerator-portable.exe -Algorithm SHA256
Get-FileHash .\GHAccelerator-1.0.0-win64.zip -Algorithm SHA256
# 与 SHA256SUMS-release.txt 比对
```

Git Bash / Linux：

```bash
sha256sum -c SHA256SUMS-release.txt
```

便携包内另有 `GHAccelerator/SHA256SUMS.txt`，覆盖目录内全部 996 个文件，可用 `python scripts/verify_artifacts.py --dir GHAccelerator --frozen` 复验。

## 重建产物

```cmd
scripts\build_windows.bat
```

脚本会先给旧产物改名让位，构建 onedir 双 exe 与 onefile，逐个 `Test-Path`，最后直接调用 `GHAccelerator-cli.exe --selftest` 校验产物（不看构建日志）。

## 正式代码签名（Windows）

**前置条件**：从受信任 CA 取得 OV 或 EV 代码签名证书。EV 可即时获得 SmartScreen 信誉；自 2023 年起 OV/EV 私钥必须存放在 FIPS 140-2 Level 2 硬件或云 HSM，多数 CA 提供 USB token 或云签名服务（DigiCert KeyLocker、Azure Key Vault 等）。

本地签名（Windows SDK 的 `signtool`）：

```cmd
scripts\sign_windows.bat dist\GHAccelerator\GHAccelerator.exe
:: 或指定 pfx
scripts\sign_windows.bat dist\GHAccelerator-portable.exe cert.pfx <password>
:: 或直接用证书存储
signtool sign /a /fd sha256 /tr http://timestamp.digicert.com /td sha256 <exe>
signtool verify /pa /v <exe>
```

CI 签名（`.github/workflows/sign-release.yml`，`workflow_dispatch` 手动触发）需要仓库 Secrets：

| Secret | 内容 |
|---|---|
| `WINDOWS_CERT_PFX_BASE64` | PFX 证书的 Base64（或使用云签名服务时改为对应 API 凭据） |
| `WINDOWS_CERT_PASSWORD` | PFX 密码 |

未配置时工作流第一步会**明确失败并给出指引**，不会静默产出未签名产物。要点：

1. 签名必须在**最终产物生成之后**做，签名后文件哈希会改变，必须重新计算 `SHA256SUMS.txt` 并同步更新 Release 资产与说明。
2. RFC3161 时间戳服务器必须可达，否则证书过期后签名失效。
3. 签名后运行 `signtool verify /pa /v`，并把输出作为发版证据留存。
4. 若要覆盖 onefile 与 onedir 两个 exe，分别签名；ZIP 需在签名后重新打包。

macOS（若未来分发 dmg）：

```bash
codesign --force --deep --options runtime --sign "Developer ID Application: ..." --entitlements app.entitlements App.app
xcrun notarytool submit App.zip --keychain-profile AC_PASSWORD --wait
xcrun stapler staple App.app
spctl -a -vv App.app
```

## 边界

不协助绕过 SmartScreen / Gatekeeper / 签名校验 / 许可证机制。未签名产物只提供哈希与用途说明，由 IT 走白名单流程；不教用户关闭安全防护。
