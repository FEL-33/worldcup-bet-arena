@echo off
cd /d "%~dp0"
title WorldCup Bet Arena
set "PY=D:\Program Files\Tencent\Marvis\MarvisAgent\1.60.3001.20\runtime\python311\python.exe"
if not exist "%PY%" set "PY=python"
echo ==========================================================
echo   WorldCup Bet Arena  (virtual coins only)
echo   User : http://127.0.0.1:8848
echo   Admin: http://127.0.0.1:8848/admin  (admin / admin123)
echo          First login: use "Change password" at top right to replace it.
echo   Data : %~dp0data   [D drive, never writes to C]
echo ==========================================================
"%PY%" "%~dp0app.py"
echo.
echo Server stopped. Press any key to exit...
pause >nul
