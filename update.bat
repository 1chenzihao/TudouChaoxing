@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  土豆刷学习通 - 一键更新
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
echo.
pause