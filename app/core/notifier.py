# -*- coding: utf-8 -*-
"""
notifier.py -- 发送 Windows 原生通知（右下角弹窗）。

【为什么要有这个模块？】
    最早版本的提醒是"页面内居中弹窗"。用户反馈：
        提示的优先级不高，能不能做成系统级的，不点取消就一直挂在右下角
    页面内弹窗有三个硬伤：只在浏览器里可见、居中挡内容、浏览器关掉就没提醒。
    Windows 原生通知能同时解决这三个问题。

【技术要点一：scenario="reminder"】
    通知 XML 里的这个属性表示"这是一条提醒"，效果是
    **通知会一直停留在屏幕上，直到用户点掉它**。

【技术要点二：AppUserModelID（应用身份）—— 本项目最难缠的一个坑】

    Windows 对通知的显示有一套【准入规则】：

        ① 应用必须有一个"身份"(AppUserModelID)
        ② 这个身份必须被系统【认可】
        ③ 否则通知会被【静默丢弃】—— 不报错、不提示，就是不显示

    我们用 PowerShell 调用通知 API 时，所有环节都返回"成功"
    （返回码 0、打印 TOAST_OK），但屏幕上什么都没有。

    实测（2026-09-13，同一台 Windows 11）的结果：

        | 用的身份 | 是否显示 |
        | --- | --- |
        | 随便一个自定义字符串 | ❌ |
        | 自己往注册表写的身份 | ❌ |
        | **系统已注册的 PowerShell 身份** | ✅ **稳定显示** |

    结论：**未打包应用（我们的情况）最可靠的做法，是借用系统已注册的身份。**
    代价是通知顶部会显示"Windows PowerShell"而不是"离线待办清单"。

    所以本项目提供两种模式，可以在设置页切换：
        "system"（默认）借用系统身份 —— 一定能用
        "own"           自有身份     —— 名字好看，但系统不一定认

【技术要点三：必须用 powershell.exe（5.1），不能用 pwsh（7）】
    Windows 通知 API 是 WinRT 组件，**只有 Windows PowerShell 5.1 有类型投影**。
    PowerShell 7 里没有这套类型，会直接报错。
"""

import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from app import config

from app.core import logging_setup

logger = logging_setup.get_logger("app.core.notifier")

# 运行时生成的文件统一放在 data/ 下（项目自包含原则，见 AGENTS.md 3.1）
NOTIFY_DIR = config.DATA_DIR / "notify"
PS_SCRIPT = NOTIFY_DIR / "show_toast.ps1"
XML_FILE = NOTIFY_DIR / "toast.xml"

# PowerShell 脚本。
# 【注意】应用身份是作为【参数】传进来的，不是写死在脚本里 ——
# 这样切换模式时不用重新生成脚本（少了"脚本和配置不同步"这类 Bug）。
PS_TEMPLATE = """param(
    [Parameter(Mandatory=$true)][string]$AppId,
    [Parameter(Mandatory=$true)][string]$XmlPath
)
$ErrorActionPreference = 'Stop'

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml([System.IO.File]::ReadAllText($XmlPath, [System.Text.Encoding]::UTF8))

$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId)
$notifier.Show($toast)

Write-Output 'TOAST_OK'
"""

# 注册表里应用身份的路径前缀（用 chr(92) 写反斜杠，避免转义混乱）
REG_PATH_PREFIX = "Software" + chr(92) + "Classes" + chr(92) + "AppUserModelId" + chr(92)

APP_ID_MODES = {
    "system": config.NOTIFY_APP_ID_SYSTEM,
    "own": config.NOTIFY_APP_ID_OWN,
}

# 【自动模式的关键状态】本次启动时，自有身份【是否已经注册过】。
#   True  -> 说明不是刚注册的，Windows 早就认得它了，可以放心用
#   False -> 说明本次才注册（或者压根没有），先用兼容模式保证能用
_registered_before_start = None


def init():
    """
    程序启动时调用一次：准备应用身份。

    为什么要在这里做，而不是"用到时才注册"？
        因为注册后 Windows 需要时间刷新缓存。
        **启动时就注册**，等第一条提醒真的到点时（至少 20 秒后，通常更久），
        身份多半已经被系统认下了 —— 比"要弹通知的那一刻才注册"成功率高得多。
    """
    global _registered_before_start
    if not is_supported():
        _registered_before_start = False
        return {"ok": False, "message": "非 Windows 系统"}

    _registered_before_start = app_id_registered()
    if _registered_before_start:
        return {"ok": True, "message": "自有身份已注册（可直接使用）",
                "registered_before_start": True}

    result = register_app_id()
    result["registered_before_start"] = False
    return result


def is_supported() -> bool:
    """只有 Windows 才有这套通知 API。"""
    return sys.platform.startswith("win")


def active_mode() -> str:
    """
    当前实际使用哪种身份模式（"auto" 会被解析成 "system" 或 "own"）。

    优先读设置表（用户在设置页选的），读不到就用 config 里的默认值。
    """
    try:
        from app.repositories import setting_repo
        mode = setting_repo.get("notify_app_id_mode", config.NOTIFY_APP_ID_MODE)
    except Exception:                      # noqa: BLE001 - 读设置失败不该让通知发不出去
        mode = config.NOTIFY_APP_ID_MODE

    if mode not in ("auto", "system", "own"):
        mode = config.NOTIFY_APP_ID_MODE

    if mode == "auto":
        # 本次启动时就已经注册过 -> 用自有身份（名字好看）
        # 否则                    -> 用兼容模式（保证立刻能用）
        return "own" if _registered_before_start else "system"

    return mode


def active_app_id() -> str:
    """当前实际用来发通知的应用身份。"""
    return APP_ID_MODES[active_mode()]


def app_id_registered() -> bool:
    """检查注册表里有没有我们自己注册的应用身份（只对 "own" 模式有意义）。"""
    if not is_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH_PREFIX + config.NOTIFY_APP_ID_OWN):
            return True
    except (ImportError, OSError):
        return False


def register_app_id() -> dict:
    """注册自有应用身份（只写 HKCU，不需要管理员权限，随时可以删掉）。"""
    if not is_supported():
        return {"ok": False, "message": "仅支持 Windows"}
    if not config.NOTIFY_REGISTER_APP_ID:
        return {"ok": False, "message": "配置里关闭了自动注册"}

    try:
        import winreg
    except ImportError:
        return {"ok": False, "message": "当前 Python 没有 winreg 模块"}

    path = REG_PATH_PREFIX + config.NOTIFY_APP_ID_OWN
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE)
        try:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, config.APP_NAME)
        finally:
            winreg.CloseKey(key)
        return {"ok": True, "message": "已注册应用身份：%s" % config.NOTIFY_APP_ID_OWN}
    except OSError as exc:
        return {"ok": False, "message": "注册失败：%s" % exc}


def _ensure_script() -> Path:
    """
    生成（或更新）PowerShell 脚本。

    【为什么要比较内容，而不是"不存在才写"？】
        脚本是"运行时生成的文件"，会随着代码升级而过期。
        我刚才就踩了这个坑：把应用身份从"写死在脚本里"改成"用参数传"之后，
        磁盘上那份旧脚本还在 —— 它没有 -AppId 参数，
        用新方式调用它会直接失败。而"文件已存在"的检查恰好会跳过重新生成。

        所以判断标准改成"内容是否和模板一致"，不一致就覆盖。
        这类"生成物过期"问题很隐蔽，凡是运行时生成的文件都要考虑。

    脚本用 utf-8-sig（带 BOM）保存 —— Windows PowerShell 5.1 靠 BOM
    判断"这是 UTF-8 文件"，没有 BOM 就会按本地代码页读，中文会乱码。
    """
    NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if PS_SCRIPT.exists() and PS_SCRIPT.read_text(encoding="utf-8-sig") == PS_TEMPLATE:
            return PS_SCRIPT
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)

    PS_SCRIPT.write_text(PS_TEMPLATE, encoding="utf-8-sig")
    return PS_SCRIPT


def build_xml(title, body, lines=None, scenario="reminder",
              sound="ms-winsoundevent:Notification.Reminder") -> str:
    """
    拼出通知的 XML。

    ToastGeneric 是 Windows 10/11 的通用模板，最多三行文字。
    escape() 必须有：标题里的 & < > 会破坏 XML 结构
    （和"添加待办"那步讲的 XSS 是同一类问题）。
    """
    texts = ['<text>%s</text>' % escape(title), '<text>%s</text>' % escape(body)]
    for line in (lines or [])[:1]:          # 标题和正文已占两行，只再加一行
        texts.append('<text>%s</text>' % escape(line))

    attr = 'scenario="%s" ' % scenario if scenario else ""
    audio = '<audio src="%s" />' % sound if sound else ""

    return ('<toast %sduration="long">'
            '<visual><binding template="ToastGeneric">%s</binding></visual>'
            '%s</toast>') % (attr, "".join(texts), audio)


def show(title, body, lines=None, timeout=25) -> dict:
    """
    发送一条 Windows 原生通知。返回 {ok, message}，不抛异常。

    ⚠️ ok=True 只代表"Windows 接受了这条通知"，不代表用户真的看到了
       （可能被专注助手拦掉、也可能因身份问题被丢弃）。
       所以设置页提供了"发一条测试通知"按钮，让用户自己确认。
    """
    if not is_supported():
        return {"ok": False, "message": "当前系统不支持 Windows 通知"}

    mode = active_mode()
    # 兜底：万一 init() 没被调用过（比如脚本里直接调用），这里补一次
    if mode == "own" and not app_id_registered():
        register_app_id()

    try:
        _ensure_script()
        XML_FILE.write_text(build_xml(title, body, lines), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "message": "写入通知文件失败：%s" % exc}

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(PS_SCRIPT),
             "-AppId", active_app_id(),
             "-XmlPath", str(XML_FILE.resolve())],
            # ★ 【v0.4.4】errors="replace"：打包版/无控制台运行时，
            #   PowerShell 的报错信息是本地化中文（GBK），按 UTF-8 解码会失败，
            #   一旦失败 stdout 就是 None —— 于是"通知其实发出去了"也会被
            #   判断成"没发出去"（因为我们靠 stdout 里有没有 TOAST_OK 来判断）。
            capture_output=True, text=True, errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return {"ok": False, "message": "找不到 powershell.exe"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "发送通知超时"}
    except OSError as exc:
        return {"ok": False, "message": "调用 PowerShell 失败：%s" % exc}

    if "TOAST_OK" in (result.stdout or ""):
        return {"ok": True, "message": "通知已提交给 Windows（身份：%s）" % mode}

    detail = (result.stderr or result.stdout or "").strip().replace(chr(10), " ")[:200]
    return {"ok": False, "message": "通知发送失败：%s" % (detail or "未知原因")}


def status() -> dict:
    """给设置页用的状态信息。"""
    mode = active_mode()
    try:
        from app.repositories import setting_repo
        saved_mode = setting_repo.get("notify_app_id_mode", config.NOTIFY_APP_ID_MODE)
    except Exception:                      # noqa: BLE001
        saved_mode = config.NOTIFY_APP_ID_MODE

    hints = {
        "system": "兼容模式：借用系统身份，通知顶部显示 Windows PowerShell，但一定能用",
        "own": "自有身份：通知里显示「离线待办清单」；若通知不出现请切回兼容模式",
    }
    hint = hints.get(mode, "")
    if saved_mode == "auto":
        hint = ("自动模式：本次用%s。%s" % (
            "自有身份（已在系统里注册过）" if mode == "own" else "兼容模式（自有身份本次才注册，需要时间生效）",
            "下次启动会自动升级成自有身份。" if mode == "system" else ""))

    return {
        "supported": is_supported(),
        "mode": mode,
        "saved_mode": saved_mode,
        "registered_before_start": bool(_registered_before_start),
        "app_id": active_app_id(),
        "own_app_id": config.NOTIFY_APP_ID_OWN,
        "app_id_registered": app_id_registered(),
        "script": str(PS_SCRIPT),
        "summary": ("可用（右下角显示，不点掉不会消失）"
                    if is_supported() else "当前系统不支持"),
        "hint": hint,
    }
