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


def _launcher_content(open_browser: bool) -> str:
    r"""
    生成 .vbs 文件的内容。

    ⚠️ 这个文件遵守两条铁律（见「问题记录与解决方案」BUG-001）：
        ① 行尾必须是 CRLF
        ② 内容必须是纯 ASCII（VBScript 读 UTF-8 中文容易乱码）

    【源码运行 和 打包成 exe，启动方式完全不一样】

        源码运行  ：让 cmd 去跑项目里的 start_silent.bat
        打包成 exe：直接运行那个 exe 就行（它本身不带黑窗口），
                    传 --no-browser 表示"后台启动，别弹浏览器"

    VBScript 的字符串里，一个双引号要写成两个 ""
    （注意：下面注释里用单引号示意，避免和三引号打架）

    拼出来的实际内容是：
        shell.Run  ' 引号 + exe 路径 + 引号 + --no-browser' , 0, False
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

    if config.FROZEN:
        exe = Path(sys.executable).resolve()
        # 路径外面包一层双引号（防止路径里有空格），在 VBS 里双引号要写成两个
        quoted = '""%s""' % str(exe)
        suffix = "" if open_browser else " --no-browser"
        run_line = 'shell.Run "%s%s", 0, False' % (quoted, suffix)
        check_line = 'If Not fso.FileExists("%s") Then WScript.Quit 1' % str(exe)
        work_line = 'shell.CurrentDirectory = "%s"' % str(exe.parent)
        what = "the app executable"
    else:
        if open_browser:
            run_line = 'shell.Run "cmd /c """ & projectDir & "\\start.bat""", 0, False'
        else:
            run_line = 'shell.Run "cmd /c """ & projectDir & "\\start_silent.bat""", 0, False'
        check_line = 'If Not fso.FileExists(projectDir & "\\run.py") Then WScript.Quit 1'
        work_line = "shell.CurrentDirectory = projectDir"
        what = "the project folder"
        lines.append('projectDir = "%s"' % str(config.BASE_DIR))

    lines.append("' If %s was moved or deleted, do nothing." % what)
    lines.append(check_line)
    lines.append(work_line)
    lines.append(run_line)
    return "\r\n".join(lines) + "\r\n"


def enable(open_browser: bool = False) -> dict:
    """
    开启开机自启：往启动文件夹写一个 .vbs。

    返回状态字典（不管成功失败都返回，让接口层统一处理）。
    """
    if not is_supported():
        return {"ok": False, "message": "开机自启目前只支持 Windows 系统"}

    target = launcher_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _launcher_content(open_browser)
        # newline="" 表示不要做任何换行符转换，直接用我们拼好的 \r\n
        with open(target, "w", encoding="ascii", newline="") as f:
            f.write(content)
        return {"ok": True, "message": "已开启开机自启"}
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
        # 给界面用的一句话说明
        "summary": (
            "已开启：下次登录 Windows 会自动在后台启动服务"
            if enabled else
            "未开启"
        ) if supported else "当前系统不支持（仅 Windows）",
    }


def _read_open_browser_flag() -> bool:
    """读取 .vbs 里用的是 start.bat（开浏览器）还是 start_silent.bat（不开）。"""
    target = launcher_path()
    if not target.exists():
        return False
    try:
        text = target.read_text(encoding="ascii", errors="ignore")
        return "start.bat" in text
    except OSError:
        return False
