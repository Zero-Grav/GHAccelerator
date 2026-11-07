@echo off
chcp 65001 >nul 2>&1
setlocal
REM 正式代码签名模板。需要 OV / EV 代码签名证书；本仓库交付的 exe 默认未签名。
REM 用法: scripts\sign_windows.bat ^<exe路径^> [证书.pfx] [pfx密码]
REM 省略 pfx 时使用 Windows 证书存储中自动匹配的证书 ^(/a^)。
set "TARGET=%~1"
set "PFX=%~2"
set "PFXPASS=%~3"
if "%TARGET%"=="" (
  echo 用法: scripts\sign_windows.bat ^<exe路径^> [证书.pfx] [pfx密码]
  echo 说明: 需要先取得 OV/EV 代码签名证书，详见 docs\RELEASE.md
  exit /b 1
)
where signtool >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 signtool。请安装 Windows SDK，或在 Developer Command Prompt 中运行。
  exit /b 1
)
if "%PFX%"=="" (
  echo [信息] 使用证书存储中的证书 ^(/a 自动选择^)
  signtool sign /a /fd sha256 /tr http://timestamp.digicert.com /td sha256 "%TARGET%"
) else (
  signtool sign /f "%PFX%" /p "%PFXPASS%" /fd sha256 /tr http://timestamp.digicert.com /td sha256 "%TARGET%"
)
if errorlevel 1 (
  echo [失败] 签名失败，未产出已签名产物
  exit /b 1
)
signtool verify /pa /v "%TARGET%"
if errorlevel 1 (
  echo [失败] 签名校验未通过
  exit /b 1
)
echo [完成] 已签名并验证: %TARGET%
echo [注意] 签名后文件哈希改变，请重新生成并发布 SHA256SUMS.txt
endlocal
exit /b 0
