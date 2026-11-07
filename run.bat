@echo off
chcp 65001 >nul 2>&1
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python。请安装 Python 3.8+ 并勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)
python -m ghacc %*
if errorlevel 1 pause
endlocal
