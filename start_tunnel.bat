@echo off
chcp 65001 >nul
cd /d %~dp0
title WorldCup Bet Arena - Public Tunnel
set "PY=D:\Program Files\Tencent\Marvis\MarvisAgent\1.60.3001.20\runtime\python311\python.exe"
if not exist "%PY%" set "PY=python"
set LOG=%~dp0data\tunnel.log

if not exist tools\cloudflared.exe (
  echo [错误] 缺少 tools\cloudflared.exe
  echo 下载地址: https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
  pause & exit /b 1
)

netstat -ano | findstr ":8848" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo [提示] 8848 端口未监听，请先运行 start.bat 启动站点
  pause & exit /b 1
)

if exist "%LOG%" del /f /q "%LOG%" >nul 2>nul
echo 正在建立 Cloudflare 公网隧道，请稍候...
start "cloudflared" /min tools\cloudflared.exe tunnel --url http://127.0.0.1:8848 --no-autoupdate --logfile "%LOG%"
timeout /t 12 >nul

echo.
echo --- 地址入库（真实域名不会在本窗口显示）---
"%PY%" "%~dp0tools\set_public.py" "%LOG%"
echo.
echo 查看真实地址: 管理端 - 访问地址 - 点「显示真实地址」（需管理员登录）
echo 停止隧道: taskkill /IM cloudflared.exe /F
echo 关闭本窗口不会断开隧道。
echo.
pause
