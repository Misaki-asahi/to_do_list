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
from app.repositories import setting_repo, task_repo

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
    except OSError:
        pass

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
            except OSError:
                pass

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


def set_autostart(enabled: bool, open_browser: bool = False) -> dict:
    """
    开启 / 关闭开机自启。

    注意：除了写文件，还要把状态【同时记进 settings 表】。
    为什么两处都要？
        - 启动文件夹里的 .vbs 是"系统实际会执行的东西"，是唯一的事实来源；
        - settings 表记录的是"用户上次的偏好"，比如"要不要顺便开浏览器"，
          下次用户点开启时能带出上次的选择。
    """
    if enabled:
        result = autostart.enable(open_browser=open_browser)
        if result.get("ok"):
            setting_repo.set_value("autostart_enabled", "1")
            setting_repo.set_value("autostart_open_browser", "1" if open_browser else "0")
    else:
        result = autostart.disable()
        if result.get("ok"):
            setting_repo.set_value("autostart_enabled", "0")

    result["status"] = autostart.status()
    return result
