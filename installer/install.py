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
import time
from pathlib import Path

APP_NAME = "离线待办清单"
APP_ID = "OfflineTodoList"

# ⚠️ 【发新版本时记得改这里】这个版本号要和 app/config.py 里的 APP_VERSION 一致。
#   它曾经漏改过：应用已经是 0.2.0，安装界面却还显示 0.1.0。
#   安装界面显示的版本，是用户判断"我装的到底是不是新版"的第一依据，
#   写错了会直接误导人，所以每次发版都要核对一遍。
APP_VERSION = "0.4.5"
EXE_NAME = "OfflineTodoList.exe"

# 装完之后在安装目录写一个 VERSION 文件，记录"装的是哪个版本"。
# 下次安装时读它，就能告诉用户"这是在从哪个版本升级"。
VERSION_MARKER = "VERSION"
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

def installed_version():
    """
    读取【当前已安装】的版本号；没装过、或读不到，返回 None。

    为什么要单独存一个 VERSION 文件？
        打包后的程序代码被编译进了 exe，外面看不到源码里的 APP_VERSION。
        与其去"猜"（比如看 exe 的修改时间），不如安装时明确写一个文件下来 ——
        简单、可靠、还能人工查看。

    老版本装的程序没有这个文件，所以会返回 None，
    界面上会显示成"较早的版本"而不是报错。
    """
    marker = install_dir() / VERSION_MARKER
    if not marker.exists():
        return None
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _remove_tree(path: Path, attempts: int = 3):
    """
    删除一个目录，失败会重试几次。返回 (是否成功, 错误说明)。

    【为什么不直接用 shutil.rmtree(ignore_errors=True)？】
        因为那样【删不干净也当成功】。接下来复制文件时会报一个含糊的
        "复制文件失败"，用户完全不知道该怎么办（真实原因往往是"程序还没关掉"）。
        这里宁可多花一秒重试，也要把真实原因说清楚。
    """
    if not path.exists():
        return True, ""

    last = None
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True, ""
        except OSError as exc:
            last = exc
            time.sleep(0.5)      # 给占用文件的进程一点时间退出
    return False, str(last)


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
    执行安装。【已经装过的话，这一步就是"覆盖升级"】。

    返回 (是否成功, 消息, 安装路径)。
    progress 是回调函数，用来在界面上显示进度。

    【为什么采用"先备好、再替换"的步骤？】
        老写法是：先删掉旧目录 -> 再复制新文件。
        问题是这两步之间一旦失败（磁盘满、杀毒软件锁文件、权限不足），
        用户就会处于【既没有旧版、也没有新版】的状态 —— 只能重下重装。

        现在改成：把最耗时、最容易失败的"复制"放在【旧版本还在】的时候做。
        复制成功之后，才做"删旧 + 改名"这两个瞬间动作。
        这样任何一步失败，用户手上至少还有一个能用的版本。

    升级【不会碰】用户的待办数据 ——
        程序装在 %LOCALAPPDATA%\Programs\OfflineTodoList，
        数据存在 %LOCALAPPDATA%\OfflineTodoList\data，两者完全分开。
    """
    def say(text):
        if progress:
            progress(text)

    source = payload_dir()
    if not source.exists():
        return False, "安装包数据异常：找不到程序文件（%s）" % source, None

    target = install_dir()
    staging = target.parent / (target.name + ".new")
    backup = target.parent / (target.name + ".old")

    # 先看看装没装过、装的是哪一版（用于界面提示和最后的升级消息）
    previous = installed_version()
    is_upgrade = target.exists()

    if is_upgrade:
        say("检测到已安装 %s，准备覆盖升级到 v%s…"
            % (("v" + previous) if previous else "较早的版本", APP_VERSION))
    else:
        say("准备安装 v%s…" % APP_VERSION)

    # ---- 1) 关掉正在运行的旧版本 ----
    # 程序在跑的时候，它自己的 exe 和 _internal 里的 dll 都被占用着，
    # 既删不掉也覆盖不了。所以这一步必须最先做。
    say("正在关闭可能正在运行的旧版本…")
    kill_running_app()
    time.sleep(0.8)          # 给进程一点时间真正退出并释放文件句柄

    # ---- 2) 先把新版本复制到【旁边的暂存目录】 ----
    # 这是整个流程里最耗时的一步。放在这里做，一旦失败，旧版本完全没被碰过。
    say("正在准备程序文件…")
    # 顺手清掉上次可能残留的 .new / .old（正常情况下来自失败的安装）
    for leftover in (staging, backup):
        ok, err = _remove_tree(leftover)
        if not ok:
            return False, "无法清理上次遗留的临时目录（%s）：%s" % (leftover.name, err), None

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, staging)
    except OSError as exc:
        _remove_tree(staging)
        return False, "复制程序文件失败（原有版本未被改动）：%s" % exc, None

    if not (staging / EXE_NAME).exists():
        _remove_tree(staging)
        return False, "安装包不完整：里面没有找到 %s" % EXE_NAME, None

    # ---- 3) 把【旧的】安装目录改名让位 ----
    #
    # 【为什么是"改名"而不是"删除"？】
    #   shutil.rmtree 是【逐个删文件】的。如果目录里有个文件被占用，
    #   它会先删掉一大半、再撞上那个文件报错 —— 于是旧安装被毁了一半。
    #   （这个坑是实测出来的：锁住 exe 再安装，111 个文件只剩 3 个。）
    #
    #   而【重命名目录】在 Windows 上是原子操作：
    #   要么整个成功，要么整个失败，不会留下"删了一半"的残骸。
    #   所以这里先把它挪到 .old，失败的话旧版本仍然完好无损。
    moved_backup = False
    if target.exists():
        say("正在替换旧版本…")
        try:
            backup_ok, backup_err = _remove_tree(backup)
            if not backup_ok:
                _remove_tree(staging)
                return False, "无法清理上次遗留的备份目录：%s" % backup_err, None
            target.rename(backup)
            moved_backup = True
        except OSError as exc:
            _remove_tree(staging)
            return False, (
                "无法替换旧版本（原有安装【完好无损】）。\n"
                "多半是程序还在运行 —— 请先在设置页点「退出程序」，"
                "或用任务管理器结束 %s 后重试。\n"
                "技术细节：%s"
            ) % (EXE_NAME, exc), None

    # ---- 4) 把暂存目录改名成正式目录 ----
    # 同一磁盘内的重命名，瞬间完成 —— 不存在"改到一半"的中间状态。
    say("正在完成安装…")
    try:
        staging.rename(target)
    except OSError as exc:
        # ★ 回滚：把旧版本改回来，让用户至少还有一个能用的程序
        if moved_backup:
            try:
                backup.rename(target)
            except OSError:
                pass
        _remove_tree(staging)
        return False, "最终替换失败，已回滚到原有版本：%s" % exc, None

    # ---- 4.5) 尽力删掉旧版本目录 ----
    # 删不掉也无所谓：它只是占点磁盘空间，不影响程序运行。
    # 下次安装时会自动清理掉。
    if moved_backup:
        _remove_tree(backup, attempts=1)

    # ---- 5) 记录版本号，供下次安装判断"从哪个版本升级" ----
    try:
        (target / VERSION_MARKER).write_text(APP_VERSION + "\n", encoding="utf-8")
    except OSError:
        # 这个文件只是给下次安装看的，写不进去不影响程序运行，
        # 所以【不应该】因为它让整个安装失败。
        pass

    if not (target / EXE_NAME).exists():
        return False, "安装不完整：没有找到 %s" % EXE_NAME, None

    say("正在生成卸载程序…")
    uninstaller = write_uninstaller(target)

    say("正在创建快捷方式…")
    create_shortcut(start_menu_dir() / (APP_NAME + ".lnk"), target / EXE_NAME, target, APP_NAME)
    create_shortcut(desktop_dir() / (APP_NAME + ".lnk"), target / EXE_NAME, target, APP_NAME)

    say("正在登记卸载信息…")
    register_uninstall_entry(target, uninstaller)

    # 消息区分"首次安装"和"覆盖升级"，让用户明确知道刚才发生了什么
    if is_upgrade:
        if previous and previous != APP_VERSION:
            return True, "升级完成：v%s → v%s（待办数据已保留）" % (previous, APP_VERSION), target
        return True, "安装完成（已覆盖原有版本，待办数据已保留）", target
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
             wraplength=480, justify="left").pack(pady=(14, 4))

    # ---- 检测已安装的版本，给出明确的升级提示 ----
    # 【为什么值得单独做这一段？】
    #   对不懂的人来说，"覆盖安装"是件让人紧张的事：会不会把我的数据弄丢？
    #   与其让他猜，不如直接告诉他三件事：
    #     1. 我检测到了你装过哪个版本
    #     2. 这次会升级到什么版本
    #     3. 你的待办数据放在别处，不会被碰
    previous = installed_version() if install_dir().exists() else None
    if install_dir().exists():
        banner = "检测到已安装 %s" % (("v" + previous) if previous else "较早的版本")
        banner += "，将覆盖升级到 v%s" % APP_VERSION
        banner_color = "#b45309"      # 琥珀色：提醒但不吓人
    else:
        banner = "全新安装 v%s" % APP_VERSION
        banner_color = "#047857"      # 绿色：一切照旧

    tk.Label(root, text=banner, font=("Microsoft YaHei", 10, "bold"),
             fg=banner_color, wraplength=480).pack(pady=(8, 0))

    # 数据安全说明 —— 这是用户最关心、也最容易误解的一点
    tk.Label(root,
             text="你的待办数据保存在 %s\n升级只替换程序文件，不会影响已有数据。" % data_dir(),
             font=("Microsoft YaHei", 8), fg="#6b7280",
             wraplength=480, justify="center").pack(pady=(4, 0))

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
            "this_version": APP_VERSION,
            "installed": install_dir().exists(),
            "installed_version": installed_version(),
            "payload": str(payload_dir()),
            "payload_exists": payload_dir().exists(),
            "install_dir": str(install_dir()),
            "data_dir": str(data_dir()),
        }, 0)

    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
