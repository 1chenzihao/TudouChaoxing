@echo off
rem ASCII only - see the note in 一键配置.bat
cd /d "%~dp0"
title Tudou Chaoxing - Install Dependencies
chcp 65001 >nul

call :find_py python
if not defined PYEXE goto no_python

python "%~dp0env_setup.py" --check
echo.
echo   ------------------------------------------------------------
echo   Installing dependencies from the fastest China mirror...
echo.
python "%~dp0env_setup.py" --install
set RC=%errorlevel%
echo.
if %RC%==0 (
    echo   Done. Dependencies are installed.
) else (
    echo   Failed. Try another mirror:
    echo       python env_setup.py --install --mirror tsinghua
)
echo.
pause
exit /b %RC%

:no_python
echo.
echo   No usable Python found.
echo   Double-click the config tool first - it installs Python from a China mirror.
echo.
pause
exit /b 1

:find_py
set "PYEXE="
for /f "delims=" %%i in ('where %~1 2^>nul') do call :accept_exe "%%i"
exit /b 0

:accept_exe
if defined PYEXE exit /b 0
echo %~1 | findstr /i "WindowsApps" >nul
if errorlevel 1 set "PYEXE=%~1"
exit /b 0
