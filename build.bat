@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ==================================================================
rem  Build the Windows executable with PyInstaller.
rem
rem  Output:  dist\OfflineTodoList\OfflineTodoList.exe
rem
rem  Double-click this file to build. Requires:  pip install pyinstaller
rem ==================================================================

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] PyInstaller is not installed.
    echo   Please run:   pip install pyinstaller
    echo.
    pause
    exit /b 1
)

echo.
echo   ==================================================
echo     Building OfflineTodoList.exe ...
echo   ==================================================
echo.

%PYBIN% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --noconsole ^
    --name OfflineTodoList ^
    --add-data "app/static;app/static" ^
    --add-data "app/templates;app/templates" ^
    --hidden-import app.main ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module PIL ^
    --exclude-module pytest ^
    run.py

if errorlevel 1 (
    echo.
    echo   [ERROR] Build failed. See the messages above.
    echo.
    pause
    exit /b 1
)

echo.
echo   ==================================================
echo     Build finished!
echo     Output: dist\OfflineTodoList\OfflineTodoList.exe
echo   ==================================================
echo.
pause
