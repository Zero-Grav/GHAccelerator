@echo off
chcp 65001 >nul 2>&1
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 PyInstaller。构建机执行: python -m pip install "pyinstaller>=6.22"
  exit /b 1
)
REM 旧产物改名让位：Windows 允许重命名运行中的映像，但不允许覆盖
if exist "dist\GHAccelerator" ren "dist\GHAccelerator" "GHAccelerator_old_%RANDOM%"
if exist "dist\GHAccelerator-portable.exe" ren "dist\GHAccelerator-portable.exe" "GHAccelerator-portable_old_%RANDOM%.exe"
echo [1/3] 构建 onedir 双入口 ...
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\onedir scripts\app.spec
if errorlevel 1 goto :fail
echo [2/3] 构建 onefile 单文件 ...
python -m PyInstaller --noconfirm --distpath dist --workpath build\onefile scripts\app-onefile.spec
if errorlevel 1 goto :fail
REM 逐个 Test-Path：打包日志显示成功不等于产物可用
for %%F in ("dist\GHAccelerator\GHAccelerator.exe" "dist\GHAccelerator\GHAccelerator-cli.exe" "dist\GHAccelerator-portable.exe") do (
  if not exist %%F (
    echo [失败] 产物缺失: %%F
    exit /b 1
  )
)
if not exist "dist\GHAccelerator\config" mkdir "dist\GHAccelerator\config"
copy /Y "config\settings.json" "dist\GHAccelerator\config\" >nul
copy /Y "config\rules.json" "dist\GHAccelerator\config\" >nul
copy /Y "README.md" "dist\GHAccelerator\" >nul
echo [3/3] 产物校验（直接调用 exe 自检）...
python scripts\verify_artifacts.py --dir "dist\GHAccelerator" --frozen
if errorlevel 1 goto :fail
echo.
echo 构建完成:
echo   GUI      dist\GHAccelerator\GHAccelerator.exe
echo   CLI      dist\GHAccelerator\GHAccelerator-cli.exe  ^(--selftest / --status / --restore^)
echo   单文件   dist\GHAccelerator-portable.exe
endlocal
exit /b 0
:fail
echo [失败] 构建或产物校验未通过，禁止发版
endlocal
exit /b 1
