@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- Silent start: the server runs but no browser window pops up ----
rem Used by auto start, so booting Windows does not open a browser tab.

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem Keep stdout unbuffered. When output is redirected to a file or a pipe,
rem Python buffers it by default and the startup banner may not appear
rem until the program exits. Unbuffered makes logs show up immediately.
set PYTHONUNBUFFERED=1

set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% --version >nul 2>nul
if errorlevel 1 exit /b 1

%PYBIN% run.py --no-browser
exit /b 0
