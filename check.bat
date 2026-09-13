@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- Force UTF-8 so Chinese output from Python shows correctly ----
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem ---- Find a usable Python ----
set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] Python was not found on this computer.
    echo   Please install Python 3.10 or newer from:
    echo       https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

if not exist "scripts\check_repo.py" (
    echo.
    echo   [ERROR] scripts\check_repo.py was not found.
    echo   Please keep this file inside the project root folder.
    echo.
    pause
    exit /b 1
)

echo.
echo   ==========================================================
echo     Check 1 of 2  --  data layer self test, 34 items
echo   ==========================================================
echo.

%PYBIN% scripts\check_repo.py

echo.
echo   ==========================================================
echo     Check 2 of 2  --  what is stored in your database
echo   ==========================================================
echo.

%PYBIN% scripts\demo_db.py --list

echo.
echo   ----------------------------------------------------------
echo     All done.  Press any key to close this window.
echo   ----------------------------------------------------------
pause >nul
