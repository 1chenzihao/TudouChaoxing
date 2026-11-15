@echo off
rem ============================================================
rem  Tudou Chaoxing - launcher (ASCII only on purpose)
rem  Chinese text lives in the .ps1 / .py files: cmd.exe mis-parses
rem  UTF-8 multi-byte lines inside batch files, so this file stays ASCII.
rem ============================================================
cd /d "%~dp0"
title Tudou Chaoxing - Config Tool

where py >nul 2>nul
if not errorlevel 1 goto use_pyl

call :find_py pythonw
if defined PYEXE goto run_gui

call :find_py python
if defined PYEXE goto run_gui

goto no_python

:use_pyl
start "" pyw -3 "配置工具.pyw"
exit /b 0

:run_gui
start "" "%PYEXE%" "配置工具.pyw"
exit /b 0

:no_python
echo.
echo   No usable Python found on this machine.
echo   Downloading Python from a China mirror now (no python.org needed).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\get_python.ps1"
if errorlevel 1 (
    echo.
    echo   Install failed - see the message above.
    pause
    exit /b 1
)
echo.
echo   Python installed. Starting the config tool...
where py >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 "配置工具.pyw"
) else (
    call :find_py pythonw
    if defined PYEXE start "" "%PYEXE%" "配置工具.pyw"
)
echo.
echo   If nothing opened, close this window and double-click Run Config Tool again.
pause
exit /b 0

rem :find_py <exe name> - first match that is NOT the Store stub
:find_py
set "PYEXE="
for /f "delims=" %%i in ('where %~1 2^>nul') do call :accept_exe "%%i"
exit /b 0

:accept_exe
if defined PYEXE exit /b 0
echo %~1 | findstr /i "WindowsApps" >nul
if errorlevel 1 set "PYEXE=%~1"
exit /b 0
