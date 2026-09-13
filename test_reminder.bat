@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- Force UTF-8 so Chinese output from Python shows correctly ----
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

rem ---- Find a usable Python ----
set "PYBIN=python"
where python >nul 2>nul || set "PYBIN=py"

%PYBIN% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] Python was not found on this computer.
    echo   Please install Python 3.10+ and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

if not exist "scripts\test_reminder.py" (
    echo.
    echo   [ERROR] scripts\test_reminder.py was not found.
    echo   Please keep this file inside the project root folder.
    echo.
    pause
    exit /b 1
)

echo.
echo   ====================================================
echo     Reminder test tool
echo   ====================================================
echo.
echo      1   Create a test reminder
echo          It fires within about 20 seconds. No need to wait.
echo.
echo      2   List current reminders
echo.
echo      3   Clear all test reminders
echo.
echo      0   Exit
echo.
set "CHOICE="
set /p CHOICE=  Choose [1]: 

rem Pressing Enter with no input means "1"
if "%CHOICE%"=="" set "CHOICE=1"

rem ---- Run the chosen action, then EXIT ----
rem This file used to loop back to the menu. That was a trap: when its input
rem came from a pipe or a redirect, "set /p" hit end-of-input, the variable
rem stayed empty, and the file kept re-running "create" in an endless loop.
rem One menu, one action, then quit. Double-click again for another action.
if "%CHOICE%"=="1" goto RUN_CREATE
if "%CHOICE%"=="2" goto RUN_LIST
if "%CHOICE%"=="3" goto RUN_CLEAR
goto THE_END

:RUN_CREATE
%PYBIN% scripts\test_reminder.py
goto THE_END

:RUN_LIST
%PYBIN% scripts\test_reminder.py --list
goto THE_END

:RUN_CLEAR
%PYBIN% scripts\test_reminder.py --clear
goto THE_END

:THE_END
echo.
echo   ----------------------------------------------------
echo     Done. Press any key to close this window.
echo   ----------------------------------------------------
pause >nul
exit /b 0
