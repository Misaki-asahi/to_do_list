# -*- coding: utf-8 -*-
"""
setting_service.py -- 设置的业务逻辑（模块 M7 的一部分）。

【这一层负责什么】
    把"设置页需要的一切信息"组装成一个响应，
    并且把"开机自启""打开目录"这类【会操作系统】的动作包起来，
    让路由层只负责收请求、发响应。
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

from app import config
from app.core import (autostart, backup as backup_core, logging_setup, notifier,
                      scheduler, timeutil)
from app.repositories import floating_repo, setting_repo, task_repo

from app.core import logging_setup

logger = logging_setup.get_logger("app.services.setting_service")

# 允许打开的目录白名单
# 【为什么要有白名单？】
#   这个接口接收一个 target 参数，然后去打开对应的目录。
#   如果不限制取值，别人就能传任意路径，让服务器打开系统任意位置 ——
#   这是一个典型的"路径穿越"安全隐患。
#   只允许打开固定的几个目录，风险就为零。
FOLDER_MAP = {
    "project": lambda: config.BASE_DIR,
    "data": lambda: config.DATA_DIR,
    "docs": lambda: config.BASE_DIR / "docs",
    "exports": lambda: config.EXPORT_DIR,
    "backups": lambda: config.BACKUP_DIR,
    "logs": lambda: config.LOG_DIR,
    "test": lambda: config.TEST_DIR,
    "startup": lambda: autostart.startup_dir(),
}


def open_folder(target: str) -> dict:
    """在系统文件管理器里打开一个目录。"""
    if target not in FOLDER_MAP:
        return {"ok": False, "message": "不支持的目录：%s（可选：%s）" % (
            target, ", ".join(sorted(FOLDER_MAP)))}

    folder = Path(FOLDER_MAP[target]())
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)


    if not folder.exists():
        return {"ok": False, "message": "目录不存在：%s" % folder}

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return {"ok": True, "message": "已打开：%s" % folder}
    except OSError as exc:
        return {"ok": False, "message": "打开失败：%s（路径：%s）" % (exc, folder)}


def _is_default_data_dir() -> bool:
    """
    当前用的是不是"正常的数据目录"（而不是测试/隔离用的临时目录）。

    【为什么要判断它？】
        见 ensure_autostart_by_default() 里的说明：
        测试用的 TestClient 也会跑启动钩子，如果不拦住，
        跑一次测试就会往用户**真实的** Windows 启动文件夹里写文件。
        （数据可以靠 TODO_DATA_DIR 隔离，"启动文件夹"却不受它影响。）
    """
    if config.FROZEN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OfflineTodoList"
    else:
        base = config.BASE_DIR
    try:
        return Path(config.DATA_DIR).resolve() == (base / "data").resolve()
    except OSError as exc:
        logger.warning("判断数据目录时出错，按「非默认」处理：%s", exc, exc_info=True)
        return False


def ensure_autostart_by_default() -> dict:
    r"""
    按"开机自启默认开启"的约定，在需要时补建启动项（v0.4.2 新增）。

    用户原话：「app默认开机静默自启」。

    【判断顺序（三选一，顺序不能换）】
        ① 用户主动关过（autostart_user_disabled == "1"）-> 什么都不做，尊重他的选择
        ② 启动项已经存在                                -> 什么都不做
        ③ 以上都不是                                    -> 建一个（静默：不带浏览器）

    【为什么第 ① 条必须排在最前面？】
        因为它代表"用户的明确意愿"。顺序写反的话，
        「关闭开机自启」就变成关不掉了 —— 下次启动又被 ③ 补回来。

    【"静默"是什么意思？】
        open_browser=False —— 开机时不弹出浏览器窗口，
        程序在后台跑着（提醒照常），桌面上只多一个悬浮窗便签。

    返回一个说明字典，方便启动时打印一行日志说明它做了什么。
    """
    if not config.AUTOSTART_BY_DEFAULT:
        return {"changed": False, "reason": "配置里关掉了「默认开启」"}

    # ★★【只有"正常数据目录"才允许动系统启动项（v0.4.2 补的安全闸门）】
    #
    #   【为什么必须加这一条？—— 一次真实的误操作】
    #     这个函数是在服务启动钩子里跑的。
    #     而测试用的 TestClient、或者任何带着 TODO_DATA_DIR 跑的隔离实例，
    #     同样会触发启动钩子 —— 于是**跑一次测试就往用户真实的启动文件夹里
    #     写了一个 .vbs**。
    #     数据能隔离（TODO_DATA_DIR），但"启动文件夹"是操作系统的东西，
    #     它不认这个环境变量。
    #
    #   判断标准：数据目录是不是"默认那个"。
    #     是   -> 正常使用，可以动系统启动项
    #     不是 -> 测试 / 隔离运行，一律不碰系统
    if not _is_default_data_dir():
        return {"changed": False,
                "reason": "当前不是默认数据目录（测试或隔离运行），不碰系统启动项"}

    if setting_repo.get(config.AUTOSTART_USER_DISABLED_KEY) == "1":
        return {"changed": False, "reason": "用户主动关过开机自启，不再自动打开"}

    if autostart.is_enabled():
        return {"changed": False, "reason": "启动项已经存在"}

    # 静默：不开浏览器，但登录后把悬浮窗显示出来
    result = autostart.enable(open_browser=False, floating=True)
    if result.get("ok"):
        setting_repo.set_value("autostart_enabled", "1")
        setting_repo.set_value("autostart_open_browser", "0")
        setting_repo.set_value("autostart_floating", "1")
        floating_repo.set_internal(launcher_mtime=autostart.launcher_mtime())
        return {"changed": True, "reason": "已按默认建立开机自启（静默启动 + 显示悬浮窗）"}
    return {"changed": False, "reason": result.get("message") or "建立开机自启失败"}


def system_stats() -> dict:
    """收集设置页要显示的各种统计信息。"""
    counts = task_repo.count_by_status()

    # 数据库文件大小（含 WAL 和 SHM）
    db_size = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suffix)
        if p.exists():
            try:
                db_size += p.stat().st_size
            except OSError as exc:
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)


    # 提醒统计
    all_todo = task_repo.list_tasks(status=config.STATUS_TODO)
    remind_on = [t for t in all_todo if t.remind_enabled]
    remind_pending = [t for t in remind_on if not t.reminded_at]

    return {
        "tasks": counts,
        "db": {
            "path": str(config.DB_PATH),
            "exists": config.DB_PATH.exists(),
            "size_kb": round(db_size / 1024, 1),
        },
        "reminders": {
            "enabled": len(remind_on),
            "pending": len(remind_pending),
            "poll_seconds": config.REMINDER_SCAN_SECONDS,
            "snooze_minutes": config.REMINDER_SNOOZE_MINUTES,
            "max_batch": config.REMINDER_MAX_BATCH,
        },
        # 日志情况（第三阶段新增：打包版没有控制台，日志是唯一的线索）
        "logs": logging_setup.log_dir_info(),
        # 系统通知与后台线程状态（用户反馈后新增：提醒改成系统级通知）
        "notify": notifier.status(),
        "scheduler": {
            "running": scheduler.is_running(),
            "last_run": scheduler.last_run,
        },
        "storage": {
            "backups": _count_files(config.BACKUP_DIR),
            "test_db": _count_files(config.TEST_DIR),
            "exports": _count_files(config.EXPORT_DIR),
        },
    }


def _count_files(folder: Path) -> int:
    """数一数某个目录下有多少个文件（目录不存在就返回 0）。"""
    if not folder.exists():
        return 0
    try:
        return sum(1 for p in folder.rglob("*") if p.is_file())
    except OSError:
        return 0


def about() -> dict:
    """关于本软件的信息。"""
    return {
        "name": config.APP_NAME,
        "version": config.APP_VERSION,
        "python": platform.python_version(),
        "platform": "%s %s" % (platform.system(), platform.release()),
        "project_dir": str(config.BASE_DIR),
        "server": "%s:%d" % (config.HOST, config.PORT),
        "time_format": config.DATETIME_FORMAT,
        "now": timeutil.now_str(),
        "api_docs": "/docs",
    }


def backups() -> dict:
    """备份的当前情况（设置页的"备份"卡片用）。"""
    items = backup_core.list_backups()
    age = backup_core.newest_backup_age_minutes()
    return {
        "folder": str(config.BACKUP_DIR),
        "keep": config.BACKUP_KEEP,
        "interval_minutes": config.BACKUP_MIN_INTERVAL_MINUTES,
        "on_startup": config.BACKUP_ON_STARTUP,
        "count": len(items),
        "newest": items[0] if items else None,
        "newest_age_minutes": round(age) if age is not None else None,
        "items": items,
    }


def full_settings() -> dict:
    """设置页一次性需要的全部数据。"""
    return {
        "autostart": autostart.status(),
        "stats": system_stats(),
        "about": about(),
        "backups": backups(),
        "settings": setting_repo.all_settings(),
    }


def set_autostart(enabled: bool, open_browser: bool = False,
                  floating: bool = False) -> dict:
    """
    开启 / 关闭开机自启。

    floating=True 表示"登录后顺便把桌面悬浮窗显示出来"（v0.3.0 新增）——
    它只是在自启脚本的命令行末尾加一个 --floating，
    真正的开窗逻辑仍在 floating_service 里，这里不重复实现。

    注意：除了写文件，还要把状态【同时记进 settings 表】。
    为什么两处都要？
        - 启动文件夹里的 .vbs 是"系统实际会执行的东西"，是唯一的事实来源；
        - settings 表记录的是"用户上次的偏好"，比如"要不要顺便开浏览器"，
          下次用户点开启时能带出上次的选择。
    """
    # ★【v0.4.2】这里不再强制覆盖 open_browser ——
    #   用户确认过"不需要拿掉"这个开关，只是出厂默认要是静默的。
    #   默认值在 schemas.AutostartRequest（False）和
    #   ensure_autostart_by_default()（显式传 False）里保证。
    #
    # ★ 记住"这是用户主动打开 / 主动关闭的"（v0.4.2）
    #   为什么需要它？因为开机自启现在【默认是开的】——
    #   程序每次启动都会检查有没有启动项，没有就补一个。
    #   如果不记下"用户主动关过"，他关掉开关、下次启动又被自动打开，
    #   那个「关闭开机自启」按钮就成了摆设。
    setting_repo.set_value(config.AUTOSTART_USER_DISABLED_KEY, "0" if enabled else "1")

    if enabled:
        result = autostart.enable(open_browser=open_browser, floating=floating)
        if result.get("ok"):
            setting_repo.set_value("autostart_enabled", "1")
            setting_repo.set_value("autostart_open_browser", "1" if open_browser else "0")
            setting_repo.set_value("autostart_floating", "1" if floating else "0")
            # 记下自启脚本的修改时间：这样悬浮窗服务端以后能发现
            # "文件被用户改了/被清理软件删了"，并及时补写（见 floating_service.sync_autostart）
            floating_repo.set_internal(launcher_mtime=autostart.launcher_mtime())
    else:
        result = autostart.disable()
        if result.get("ok"):
            setting_repo.set_value("autostart_enabled", "0")

    result["status"] = autostart.status()
    return result
