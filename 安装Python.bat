@echo off
rem ASCII only - see the note in 一键配置.bat
cd /d "%~dp0"
title Tudou Chaoxing - Install Python
echo.
echo   Downloading Python from a China mirror (huaweicloud / npmmirror).
echo   No python.org, no VPN needed.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\get_python.ps1"
if errorlevel 1 (
    echo.
    echo   Install failed - see the message above.
    pause
    exit /b 1
)
echo.
echo   Done. Double-click the config tool again.
pause
exit /b 0
