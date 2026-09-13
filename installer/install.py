# -*- coding: utf-8 -*-
r"""
install.py -- 安装程序（它自己会被打包成 OfflineTodoList-Setup.exe）。

【为什么要自己写安装程序？】

    原本打算用 Inno Setup（Windows 上最常用的安装包制作工具），
    但它是第三方软件，需要额外下载安装（官网给的是下载页，自动化获取很麻烦）。
    而这个项目一直坚持"依赖越少越好、别人 clone 下来就能用"，
    所以干脆用 PyInstaller 自己做一个 —— 不引入任何新工具。

【这个安装程序做了什么】

    1. 把程序文件复制到 %LOCALAPPDATA%\Programs\OfflineTodoList
       装在自己的用户目录里，【不需要管理员权限】
    2. 创建开始菜单快捷方式 和 桌面快捷方式
    3. 在"应用和功能"里登记一条卸载信息
    4. 生成一个可用的卸载程序

【打包方式】

    用 PyInstaller 把本文件打包，并把已经打好的 app 目录作为数据塞进去：

        pyinstaller --onefile --noconsole --name OfflineTodoList-Setup ^
            --add-data "dist/OfflineTodoList;payload" ^
            installer/install.py

    运行时 PyInstaller 会把 payload 解压到临时目录，我们再把它复制到安装位置。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "离线待办清单"
APP_ID = "OfflineTodoList"
APP_VERSION = "0.1.0"
EXE_NAME = "OfflineTodoList.exe"
SHORTCUT_MATCH = "OfflineTodoList"     # 用来识别"哪些快捷方式是我们建的"


# ===========================================================================
# 路径
# ===========================================================================

def payload_dir() -> Path:
    """
    找到被打包进来的程序本体。

    打包后 PyInstaller 会把数据解压到 sys._MEIPASS 指向的临时目录。
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "payload"


def install_dir() -> Path:
    r"""
    安装到哪里。

    %LOCALAPPDATA%\Programs\ 是 Windows 上放"当前用户安装的程序"的标准位置。
    好处：**不需要管理员权限**，也不会因为 Program Files 只读而写不了文件。
    """
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "Programs" / APP_ID


def start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / APP_ID


# ===========================================================================
# 快捷方式
# ===========================================================================

def _ps_quote(text) -> str:
    """把字符串安全地嵌进 PowerShell 单引号字符串里（单引号要写两遍）。"""
    return str(text).replace("'", "''")


def create_shortcut(link_path: Path, target: Path, work_dir: Path, description: str) -> bool:
    r"""
    用 PowerShell 创建 .lnk 快捷方式。

    原因：直接用 Python 建快捷方式需要第三方库（pywin32），
    而系统自带的 PowerShell 一行就能搞定 —— 少一个依赖。
    """
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$sc = $ws.CreateShortcut('%s'); "
        "$sc.TargetPath = '%s'; "
        "$sc.WorkingDirectory = '%s'; "
        "$sc.Description = '%s'; "
        "$sc.Save()"
    ) % (_ps_quote(link_path), _ps_quote(target), _ps_quote(work_dir), _ps_quote(description))

    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def remove_shortcuts() -> None:
    r"""
    删除所有指向本程序的快捷方式。

    为什么"按指向的目标找"而不是"按文件名删"？
        因为快捷方式的名字是中文（离线待办清单.lnk），
        而卸载用的 .bat 文件必须是纯 ASCII（见 BUG-001），
        没法把中文写进去。

        改成"遍历 .lnk，看它的目标是不是我们的 exe"就没有这个问题了 ——
        顺便还能清理掉用户改过名字的快捷方式。
    """
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "@(($env:APPDATA + '\\Microsoft\\Windows\\Start Menu\\Programs'), "
        "  ($env:USERPROFILE + '\\Desktop')) | ForEach-Object { "
        "    Get-ChildItem (Join-Path $_ '*.lnk') -ErrorAction SilentlyContinue | ForEach-Object { "
        "      $sc = $ws.CreateShortcut($_.FullName); "
        "      if ($sc.TargetPath -like '*%s*') { "
        "        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue } } }"
    ) % SHORTCUT_MATCH
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


# ===========================================================================
# 注册表：让程序出现在"应用和功能"里
# ===========================================================================

def _uninstall_key_path() -> str:
    bs = chr(92)
    return (bs.join(["Software", "Microsoft", "Windows", "CurrentVersion", "Uninstall", APP_ID]))


def register_uninstall_entry(target: Path, uninstaller: Path) -> bool:
    r"""
    在 HKCU\...\Uninstall 下登记卸载信息。

    这样用户在 Windows「设置 → 应用 → 已安装的应用」里能看到本程序并卸载。
    只写 HKCU（当前用户），不需要管理员权限。
    """
    try:
        import winreg
    except ImportError:
        return False

    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _uninstall_key_path(),
                                 0, winreg.KEY_WRITE)
        try:
            values = [
                ("DisplayName", APP_NAME),
                ("DisplayVersion", APP_VERSION),
                ("Publisher", APP_NAME),
                ("InstallLocation", str(target)),
                ("UninstallString", '"%s"' % uninstaller),
                ("DisplayIcon", str(target / EXE_NAME)),
                ("NoModify", 1),
                ("NoRepair", 1),
            ]
            for name, value in values:
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, kind, value)
        finally:
            winreg.CloseKey(key)
        return True
    except OSError:
        return False


def unregister_uninstall_entry() -> None:
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _uninstall_key_path())
    except (ImportError, OSError):
        pass


# ===========================================================================
# 卸载程序
# ===========================================================================

# 注意：这个 .bat 的占位符在生成时会被替换掉：
#     __APP_DIR__  -> 安装目录
# 文件必须是纯 ASCII、CRLF 换行（见 BUG-001），所以里面不写中文。
UNINSTALL_BAT = r"""@echo off
setlocal
title Offline Todo List - Uninstaller

rem ---------------------------------------------------------------
rem  Copy this script to TEMP and re-run it from there.
rem  A running .bat cannot reliably delete the folder it lives in.
rem ---------------------------------------------------------------
if not "%~1"=="GO" (
    copy /y "%~f0" "%TEMP%\otl_uninstall.bat" >nul 2>nul
    start "" /min "%TEMP%\otl_uninstall.bat" GO
    exit /b 0
)

set "APPDIR=__APP_DIR__"

echo.
echo   Uninstalling Offline Todo List ...
echo.

echo   [1/3] Stopping the app if it is running ...
taskkill /IM OfflineTodoList.exe /F >nul 2>nul

echo   [2/3] Removing shortcuts and registry entry ...
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; @(($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs'), ($env:USERPROFILE + '\Desktop')) | ForEach-Object { Get-ChildItem (Join-Path $_ '*.lnk') -ErrorAction SilentlyContinue | ForEach-Object { $sc = $ws.CreateShortcut($_.FullName); if ($sc.TargetPath -like '*OfflineTodoList*') { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue } } }"
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\OfflineTodoList" /f >nul 2>nul

echo   [3/3] Removing program files ...
cd /d "%TEMP%"
rmdir /s /q "%APPDIR%"

echo.
echo   Uninstall finished.
echo.
echo   NOTE: your todo data was kept here:
echo     %LOCALAPPDATA%\OfflineTodoList
echo   Delete that folder yourself for a full cleanup.
echo.
pause
del /f /q "%TEMP%\otl_uninstall.bat" >nul 2>nul
"""


def write_uninstaller(target: Path) -> Path:
    r"""
    生成卸载程序。

    .bat 文件有两条铁律（见 BUG-001）：CRLF 换行 + 纯 ASCII。
    这里显式处理：先统一成 \n，再整体换成 \r\n，最后按 ascii 编码写出。
    """
    bat = target / "Uninstall.bat"
    content = UNINSTALL_BAT.replace("__APP_DIR__", str(target))
    content = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    bat.write_text(content, encoding="ascii", newline="")
    return bat


# ===========================================================================
# 安装主流程
# ===========================================================================

def kill_running_app() -> None:
    """关掉正在运行的旧版本（否则文件被占用，复制会失败）。"""
    try:
        subprocess.run(["taskkill", "/IM", EXE_NAME, "/F"],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def do_install(progress=None):
    """
    执行安装。返回 (是否成功, 消息, 安装路径)。
    progress 是回调函数，用来在界面上显示进度。
    """
    def say(text):
        if progress:
            progress(text)

    source = payload_dir()
    if not source.exists():
        return False, "安装包数据异常：找不到程序文件（%s）" % source, None

    target = install_dir()

    say("正在关闭可能正在运行的旧版本…")
    kill_running_app()
    import time
    time.sleep(0.6)

    say("正在复制程序文件…")
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    except OSError as exc:
        return False, "复制文件失败：%s" % exc, None

    if not (target / EXE_NAME).exists():
        return False, "安装不完整：没有找到 %s" % EXE_NAME, None

    say("正在生成卸载程序…")
    uninstaller = write_uninstaller(target)

    say("正在创建快捷方式…")
    create_shortcut(start_menu_dir() / (APP_NAME + ".lnk"), target / EXE_NAME, target, APP_NAME)
    create_shortcut(desktop_dir() / (APP_NAME + ".lnk"), target / EXE_NAME, target, APP_NAME)

    say("正在登记卸载信息…")
    register_uninstall_entry(target, uninstaller)

    return True, "安装完成", target


def do_uninstall() -> tuple:
    """静默卸载（供自动化测试使用）。"""
    target = install_dir()
    kill_running_app()
    remove_shortcuts()
    unregister_uninstall_entry()
    shutil.rmtree(target, ignore_errors=True)
    return True, "已卸载（数据目录保留在 %s）" % data_dir()


# ===========================================================================
# 界面
# ===========================================================================

def run_gui():
    """一个简单的图形安装界面（tkinter 是 Python 自带的，不用装东西）。"""
    import tkinter as tk

    root = tk.Tk()
    root.title("%s 安装程序" % APP_NAME)
    root.geometry("540x320")
    root.resizable(False, False)

    tk.Label(root, text=APP_NAME, font=("Microsoft YaHei", 18, "bold")).pack(pady=(26, 4))
    tk.Label(root, text="版本 %s   ·   本地离线待办与提醒工具" % APP_VERSION,
             font=("Microsoft YaHei", 10), fg="#6b7280").pack()

    tk.Label(root, text="安装位置：" + str(install_dir()),
             font=("Microsoft YaHei", 9), fg="#6b7280",
             wraplength=480, justify="left").pack(pady=(20, 6))

    status = tk.StringVar(value="点下面的按钮开始安装")
    tk.Label(root, textvariable=status, font=("Microsoft YaHei", 11),
             fg="#2563eb", wraplength=480).pack(pady=12)

    box = tk.Frame(root)
    box.pack(pady=16)

    open_btn = tk.Button(box, text="打开安装目录", font=("Microsoft YaHei", 11),
                         width=14, height=2, state="disabled")

    def on_install():
        install_btn.configure(state="disabled")
        root.update()
        ok, message, target = do_install(lambda t: (status.set(t), root.update()))
        if ok:
            status.set("安装完成！可以从开始菜单或桌面启动了")
            open_btn.configure(state="normal",
                               command=lambda: os.startfile(str(target)))
            open_btn.pack(side="left", padx=6)
        else:
            status.set("安装失败：" + message)
            install_btn.configure(state="normal")

    install_btn = tk.Button(box, text="开始安装", font=("Microsoft YaHei", 11, "bold"),
                            width=14, height=2, command=on_install)
    install_btn.pack(side="left", padx=6)

    tk.Label(root, text="卸载：Windows 设置 → 应用 → 已安装的应用 → " + APP_NAME,
             font=("Microsoft YaHei", 8), fg="#9ca3af").pack(side="bottom", pady=12)

    root.mainloop()


RESULT_FILE = "otl_setup_result.json"


def report(payload: dict, code: int) -> int:
    r"""
    输出结果。

    【为什么要同时写文件？】
        安装包是用 --noconsole 打包的（不弹黑窗口），
        这种情况下 print() 的内容【谁都看不到】——
        包括想自动化测试它的人。

        所以除了 print，再往 %TEMP% 写一份结果文件，
        让脚本可以读到"到底装成功了没有"。
    """
    text = json.dumps(payload, ensure_ascii=False)
    print(text)
    try:
        temp = Path(os.environ.get("TEMP", "."))
        (temp / RESULT_FILE).write_text(text, encoding="utf-8")
    except OSError:
        pass
    return code


def main():
    args = sys.argv[1:]

    if "--silent" in args:
        ok, message, target = do_install()
        return report({"action": "install", "ok": ok, "message": message,
                       "target": str(target) if target else ""}, 0 if ok else 1)

    if "--uninstall" in args:
        ok, message = do_uninstall()
        return report({"action": "uninstall", "ok": ok, "message": message}, 0 if ok else 1)

    if "--info" in args:
        return report({
            "action": "info",
            "payload": str(payload_dir()),
            "payload_exists": payload_dir().exists(),
            "install_dir": str(install_dir()),
            "data_dir": str(data_dir()),
        }, 0)

    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
