@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] Python was not found on this computer.
    echo.
    pause
    exit /b 1
)

echo.
echo   This will remove auto-generated files only:
echo     - __pycache__ folders
echo     - .pytest_cache
echo     - data	est temporary database
echo.
echo   Your real todo data in data	odo.db is NOT touched.
echo.
pause

%PYBIN% scripts\clean.py

echo.
echo   ----------------------------------------------------------
echo     Done.  Press any key to close this window.
echo   ----------------------------------------------------------
pause >nul
