@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ==================================================================
rem  Build the Windows installer.
rem
rem  Run build.bat FIRST (it produces dist\OfflineTodoList),
rem  then run this file.
rem
rem  Output:  dist\OfflineTodoList-Setup.exe
rem ==================================================================

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] PyInstaller is not installed. Run: pip install pyinstaller
    echo.
    pause
    exit /b 1
)

if not exist "dist\OfflineTodoList\OfflineTodoList.exe" (
    echo.
    echo   [ERROR] dist\OfflineTodoList was not found.
    echo   Please run build.bat first.
    echo.
    pause
    exit /b 1
)

echo.
echo   ==================================================
echo     Building OfflineTodoList-Setup.exe ...
echo   ==================================================
echo.

%PYBIN% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --noconsole ^
    --name OfflineTodoList-Setup ^
    --add-data "dist/OfflineTodoList;payload" ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module pytest ^
    installer/install.py

if errorlevel 1 (
    echo.
    echo   [ERROR] Build failed.
    echo.
    pause
    exit /b 1
)

echo.
echo   ==================================================
echo     Done!  Output: dist\OfflineTodoList-Setup.exe
echo   ==================================================
echo.
pause
