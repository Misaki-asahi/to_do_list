# -*- coding: utf-8 -*-
r"""
autostart.py -- 开机自启（模块 M7，Windows）。

【注意这个 docstring 前面的 r】
    它是一个"原始字符串"(raw string)，因为下面写了 Windows 路径
    （%APPDATA%\Microsoft\...）。不写 r 的话，Python 会把 \M、\W 当成
    转义序列，报 SyntaxWarning —— 而且**未来版本的 Python 会直接报错**。
    —— 这条是上线前体检时扫出来的，见 BUG-017。

【实现方式：往"启动"文件夹里放一个 .vbs 文件】

Windows 的"启动"文件夹是一个很朴素但很好用的机制：
放在里面的程序，用户登录后会由系统自动运行。

位置：%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

【为什么用这个方案，而不是别的？】

| 方案 | 需要管理员权限 | 用户能自己发现和删除 | 结论 |
| --- | --- | --- | --- |
| 注册表 HKCU\\...\\Run | 不需要 | ❌ 藏在注册表里，用户找不到 | 不选 |
| 任务计划程序 | 部分操作需要 | 一般 | 过度复杂 |
| **启动文件夹** | **不需要** | ✅ 打开文件夹就能看见、能删 | ✅ 选它 |

【为什么放 .vbs 而不是 .bat？】
    .bat 被系统启动时会弹出一个黑色命令行窗口。
    .vbs 可以通过 WScript.Shell.Run 的第二个参数（窗口样式 0 = 隐藏）让窗口完全不出现。
    用户会以为"什么都没发生"，实际上服务已经在后台跑起来了。
"""

import os
import sys
from pathlib import Path

from app import config

# 放进启动文件夹的文件名（纯 ASCII，避免编码问题）
LAUNCHER_NAME = "OfflineTodoList.vbs"


def is_supported() -> bool:
    """只有 Windows 才有这个机制。"""
    return sys.platform.startswith("win")


def startup_dir() -> Path:
    """
    返回 Windows"启动"文件夹的路径。

    %APPDATA% 一般长这样： C:\\Users\\你的名字\\AppData\\Roaming
    所以启动文件夹是：
        C:\\Users\\你的名字\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def launcher_path() -> Path:
    """启动文件夹里那个 .vbs 文件的完整路径。"""
    return startup_dir() / LAUNCHER_NAME


def is_enabled() -> bool:
    """判断开机自启是否已开启 —— 就是看那个文件在不在。"""
    try:
        return launcher_path().exists()
    except OSError:
        return False


def target_dir() -> Path:
    """
    自启要启动的那个"程序"在哪。

    源码运行   -> 项目目录（里面是 start.bat / start_silent.bat）
    打包成 exe -> exe 所在的目录
    """
    if config.FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(config.BASE_DIR)


def _launcher_content(open_browser: bool, floating: bool = False) -> str:
    r"""
    生成 .vbs 文件的内容。

    ⚠️ 这个文件遵守两条铁律（见「问题记录与解决方案」BUG-001）：
        ① 行尾必须是 CRLF
        ② 内容必须是纯 ASCII（VBScript 读 UTF-8 中文容易乱码）

    【源码运行 和 打包成 exe，启动方式完全不一样】

        源码运行  ：让 cmd 去跑项目里的 start_silent.bat
        打包成 exe：直接运行那个 exe 就行（它本身不带黑窗口），
                    传 --no-browser 表示"后台启动，别弹浏览器"

    【v0.3.0 新增：floating 参数 —— 开机自启时顺便显示桌面悬浮窗】

        加了它之后，命令行末尾会多一个 --floating。
        程序收到这个参数时【不会】再起一个服务（端口只有一个），
        而是去连已经跑起来的服务、把悬浮窗画出来。
        （路径见 app/services/floating_service.py 的 _spawn()。）

        为什么要放在自启脚本里，而不是"让服务自己去开窗口"？
            因为服务本来就有一个守护线程负责"该开就开"。
            这里写进去只是为了：**桌面出现之前就把窗口准备好**，
            用户一登录就能看见待办，而不用等守护线程 3 秒后再检查一次。

    VBScript 的字符串里，一个双引号要写成两个 ""
    （注意：下面注释里用单引号示意，避免和三引号打架）

    拼出来的实际内容是：
        shell.Run  ' 引号 + exe 路径 + 引号 + --no-browser --floating' , 0, False
    """
    lines = [
        "' Offline Todo List - auto start launcher.",
        "' This file was created by the settings page of the app.",
        "' DELETE THIS FILE to disable auto start.",
        "Option Explicit",
        "Dim shell, fso, projectDir",
        'Set shell = CreateObject("WScript.Shell")',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
    ]

    # ★【v0.4.2：出厂默认是"静默启动"（open_browser=False）】
    #   用户原话：「自启动静默启动，不要开启浏览器」，
    #   后面又补充：「不需要拿掉，用户出厂设置打开自启动但不打开浏览器即可」。
    #
    #   所以：能力【保留】（设置页里那个"启动时同时打开浏览器"开关还在，
    #   用户想要可以自己打开），但**默认值一定是 False** ——
    #   开机时程序在后台安静地跑起来，不会弹出浏览器窗口。
    #   真正保证"出厂静默"的地方有两处：
    #     · 本函数的参数默认值 False
    #     · ensure_autostart_by_default() 显式传 False
    #
    # 命令行末尾要加的开关
    suffix = "" if open_browser else " --no-browser"
    if floating:
        suffix += " --floating"

    if config.FROZEN:
        exe = Path(sys.executable).resolve()
        # 路径外面包一层双引号（防止路径里有空格），在 VBS 里双引号要写成两个
        quoted = '""%s""' % str(exe)
        run_line = 'shell.Run "%s%s", 0, False' % (quoted, suffix)
        check_line = 'If Not fso.FileExists("%s") Then WScript.Quit 1' % str(exe)
        work_line = 'shell.CurrentDirectory = "%s"' % str(exe.parent)
        what = "the app executable"
    else:
        if open_browser:
            run_line = 'shell.Run "cmd /c """ & projectDir & "\\start.bat""%s", 0, False' % suffix
        else:
            run_line = 'shell.Run "cmd /c """ & projectDir & "\\start_silent.bat""%s", 0, False' % suffix
        check_line = 'If Not fso.FileExists(projectDir & "\\run.py") Then WScript.Quit 1'
        work_line = "shell.CurrentDirectory = projectDir"
        what = "the project folder"
        lines.append('projectDir = "%s"' % str(config.BASE_DIR))

    lines.append("' If %s was moved or deleted, do nothing." % what)
    lines.append(check_line)
    lines.append(work_line)
    lines.append(run_line)
    return "\r\n".join(lines) + "\r\n"


def enable(open_browser: bool = False, floating: bool = False) -> dict:
    """
    开启开机自启：往启动文件夹写一个 .vbs。

    floating=True 表示"开机自启时顺便把桌面悬浮窗也显示出来"。

    返回状态字典（不管成功失败都返回，让接口层统一处理）。
    """
    if not is_supported():
        return {"ok": False, "message": "开机自启目前只支持 Windows 系统"}

    target = launcher_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _launcher_content(open_browser, floating)
        # newline="" 表示不要做任何换行符转换，直接用我们拼好的 \r\n
        with open(target, "w", encoding="ascii", newline="") as f:
            f.write(content)
        message = "已开启开机自启"
        if floating:
            message += "（并会在登录后显示桌面悬浮窗）"
        return {"ok": True, "message": message}
    except OSError as exc:
        return {"ok": False, "message": "写入失败：%s" % exc}


def disable() -> dict:
    """关闭开机自启：把那个 .vbs 删掉。"""
    if not is_supported():
        return {"ok": False, "message": "开机自启目前只支持 Windows 系统"}

    target = launcher_path()
    try:
        if target.exists():
            target.unlink()
            return {"ok": True, "message": "已关闭开机自启"}
        return {"ok": True, "message": "开机自启本来就是关闭的"}
    except OSError as exc:
        # 文件被占用、权限不足等
        return {"ok": False, "message": "删除失败：%s" % exc}


def open_startup_folder() -> dict:
    """在资源管理器里打开"启动"文件夹，让用户自己看得见。"""
    if not is_supported():
        return {"ok": False, "message": "仅支持 Windows"}
    folder = startup_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))
        return {"ok": True, "message": "已打开启动文件夹"}
    except OSError as exc:
        return {"ok": False, "message": "打开失败：%s" % exc}


def status() -> dict:
    """返回开机自启的完整状态（设置页要显示的信息）。"""
    supported = is_supported()
    enabled = is_enabled() if supported else False
    path = launcher_path() if supported else None

    return {
        "supported": supported,
        "enabled": enabled,
        "launcher_name": LAUNCHER_NAME,
        "launcher_path": str(path) if path else "",
        "startup_dir": str(startup_dir()) if supported else "",
        "project_dir": str(config.BASE_DIR),
        "open_browser": _read_open_browser_flag(),
        # v0.3.0：自启脚本里有没有带 --floating（即"登录后自动显示桌面悬浮窗"）
        "floating": _read_floating_flag(),
        # 给界面用的一句话说明
        "summary": (
            "已开启：下次登录 Windows 会自动在后台启动服务"
            + ("，并显示桌面悬浮窗" if _read_floating_flag() else "")
            if enabled else
            "未开启"
        ) if supported else "当前系统不支持（仅 Windows）",
    }


def launcher_mtime() -> float:
    """
    自启脚本的最后修改时间（不存在则 0）。

    【它有什么用？】
        用来发现"用户在启动文件夹里动了手脚"——
        比如手动改了 .vbs，或者它被别的清理软件删掉了。
        悬浮窗的服务端会记住这个时间戳，一旦对不上就重新写一份自启脚本，
        保证"登录后自动显示悬浮窗"这件事真的会发生（见 floating_service）。
    """
    target = launcher_path()
    try:
        return target.stat().st_mtime if target.exists() else 0.0
    except OSError:
        return 0.0


def _read_open_browser_flag() -> bool:
    """读取 .vbs 里用的是 start.bat（开浏览器）还是 start_silent.bat（不开）。"""
    text = _read_launcher_text()
    return "start.bat" in text or "--no-browser" not in text


def _read_floating_flag() -> bool:
    """
    读取 .vbs 里有没有 --floating（即"登录后显示悬浮窗"）。

    【为什么用"读文件"而不是"读设置"？】
        因为启动文件夹里那个 .vbs 才是【系统真正会执行的东西】——
        它才是事实来源。设置表里记的只是"用户上次的选择"，
        两者万一不一致（用户手改过文件），显示按钮状态就该以文件为准，
        否则设置页会显示一个"其实不会发生"的状态，那是最误导人的。
    """
    return "--floating" in _read_launcher_text()


def _read_launcher_text() -> str:
    """把 .vbs 内容读出来（读不到就返回空字符串）。"""
    target = launcher_path()
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="ascii", errors="ignore")
    except OSError:
        return ""
