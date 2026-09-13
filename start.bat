@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- Force UTF-8 so the Chinese messages printed by Python show up correctly ----
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem Keep stdout unbuffered so messages appear immediately,
rem even when the output is redirected to a file.
set PYTHONUNBUFFERED=1

rem ---- Find a usable Python: prefer "python", fall back to the "py" launcher ----
set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] Python was not found on this computer.
    echo   Please install Python 3.10 or newer from:
    echo       https://www.python.org/downloads/
    echo   During setup, tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

if not exist "run.py" (
    echo.
    echo   [ERROR] run.py was not found next to start.bat.
    echo   Please keep start.bat inside the project root folder.
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting Offline Todo List ...
echo.

%PYBIN% run.py

echo.
echo   Server stopped. Press any key to close this window.
pause >nul
