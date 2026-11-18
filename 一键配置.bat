@echo off
rem ============================================================
rem  Tudou Chaoxing - launcher
rem  ASCII only on purpose: cmd.exe mis-parses UTF-8 multi-byte
rem  lines inside batch files. Chinese messages live in the .py.
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Tudou Chaoxing - Config Tool

rem ---------- 0) 目录完整性（在 ZIP 里直接双击会走到这里） ----------
if not exist "配置工具.pyw" goto missing_files
if not exist "main.py" goto missing_files

rem ---------- 1) 找可用的 Python（跳过 WindowsApps 占位版） ----------
call :find_py python.exe
if not defined PYEXE (
    rem PATH 里没有 python.exe 时，用 py 启动器问出真实路径
    rem （装 Python 忘了勾 Add to PATH 是最常见的情况，py.exe 在 C:\Windows 下一定有）
    set "PYTMP=%TEMP%\td_py_path.txt"
    if exist "%PYTMP%" del "%PYTMP%" >nul 2>nul
    py -3 -c "import sys;print(sys.executable)" >"%PYTMP%" 2>nul
    if not errorlevel 1 set /p PYEXE=<"%PYTMP%"
    if exist "%PYTMP%" del "%PYTMP%" >nul 2>nul
)
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
if not defined PYEXE goto no_python

rem 同目录下的 pythonw.exe，没有就退回 python.exe（会带一个黑框）
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"

rem ---------- 2) 自检：GUI 的两个前提 ----------
"%PYEXE%" -c "import tkinter" >nul 2>nul
if errorlevel 1 goto no_tk

"%PYEXE%" -m py_compile "配置工具.pyw" >nul 2>nul
if errorlevel 1 goto bad_syntax

rem ---------- 3) 启动，并用 gui_error.log 探测是否秒崩 ----------
if exist "gui_error.log" del "gui_error.log" >nul 2>nul
start "" "%PYW%" "配置工具.pyw"
ping -n 5 127.0.0.1 >nul
if exist "gui_error.log" goto launch_failed
exit /b 0

rem ------------------------------------------------------------
:missing_files
echo.
echo   [X] Cannot find the GUI file (look for these two in this folder):
echo.
echo   Two common reasons:
echo     1) You double-clicked the .bat INSIDE the zip file. Unzip first,
echo        then run it from the unzipped folder.
echo     2) The download was incomplete - re-download the ZIP.
echo.
echo   Need these here:  配置工具.pyw  +  main.py
echo.
echo   Current folder: %CD%
echo.
pause
exit /b 1

:no_python
echo.
echo   No usable Python found.
echo   Downloading Python from a China mirror (no python.org needed)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\get_python.ps1"
if errorlevel 1 goto python_failed
echo.
echo   Python installed. Starting the config tool...
call :find_py python.exe
if not defined PYEXE goto python_failed
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"
start "" "%PYW%" "配置工具.pyw"
ping -n 5 127.0.0.1 >nul
if exist "gui_error.log" goto launch_failed
echo.
echo   If nothing opened, close this window and run 一键配置.bat again.
pause
exit /b 0

:python_failed
echo.
echo   [X] Python install failed - see the message above.
echo   You can also run 安装Python.bat manually, then try again.
echo.
pause
exit /b 1

:no_tk
echo.
echo   [X] This Python has no tkinter (the GUI toolkit).
echo.
echo   That means it was installed WITHOUT tcl/tk. Fix it by running:
echo       "%~dp0安装Python.bat"
echo   (it installs a complete Python 3.12 from a China mirror)
echo.
pause
exit /b 1

:bad_syntax
echo.
echo   [X] 配置工具.pyw is broken (syntax error).
echo   The download is probably corrupted - re-download the ZIP.
echo.
pause
exit /b 1

:launch_failed
echo.
echo   [X] The GUI started but crashed immediately.
echo   Error details were written to:
echo       %CD%\gui_error.log
echo.
type "gui_error.log" 2>nul
echo.
echo   Send that file to the author, or try 安装环境.bat first.
echo.
pause
exit /b 1

rem :find_py - first match that is NOT the Microsoft Store stub
:find_py
set "PYEXE="
for /f "delims=" %%i in ('where %~1 2^>nul') do call :accept_exe "%%i"
exit /b 0

:accept_exe
if defined PYEXE exit /b 0
echo %~1 | findstr /i "WindowsApps" >nul
if errorlevel 1 set "PYEXE=%~1"
exit /b 0
