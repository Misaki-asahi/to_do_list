# -*- coding: utf-8 -*-
"""
backup.py -- 数据库自动备份。

【为什么需要它？（这个模块是被一次真实事故"逼"出来的）】

    开发过程中，我在临时测试脚本里对【真实数据库】执行了"删除全部任务"，
    导致用户手动创建的待办全部丢失，且无法恢复 —— 因为当时还没有备份。

    详见证「问题记录与解决方案」BUG-011。

    结论：**"不做可能丢数据"的功能，优先级必须是最高的。**
    所以这个模块从第三阶段提前到这里实现。

【为什么用 SQLite 的官方备份 API，而不是直接复制文件？】

    直接复制（shutil.copy）在数据库正在被写入时，可能复制到一个
    "写了一半"的状态，甚至复制出来的文件根本打不开。

    sqlite3 的 backup() 是官方提供的"在线备份"接口：
    它会在保证一致性的前提下把数据一页一页搬过去，
    即使程序正在运行、有别的连接正在写，也是安全的。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from app import config


def _timestamp() -> str:
    """给备份文件用的时间戳，例如 20260912_233045。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def list_backups() -> list:
    """
    列出所有备份文件，最新的排在最前面。

    每一项包含：文件名、完整路径、大小、创建时间（从文件名解析）。
    """
    folder = config.BACKUP_DIR
    if not folder.exists():
        return []

    items = []
    for path in sorted(folder.glob("todo_*.db"), reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue

        # 从文件名 todo_20260912_233045.db 里还原出可读时间
        stamp = path.stem.replace("todo_", "")
        try:
            created = datetime.strptime(stamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            created = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        items.append({
            "name": path.name,
            "path": str(path),
            "size_kb": round(stat.st_size / 1024, 1),
            "created": created,
        })
    return items


def newest_backup_age_minutes():
    """最近一次备份是多少分钟以前；从来没有备份过则返回 None。"""
    backups = list_backups()
    if not backups:
        return None
    try:
        stat = Path(backups[0]["path"]).stat()
    except OSError:
        return None
    return (datetime.now().timestamp() - stat.st_mtime) / 60.0


def make_backup(reason: str = "manual") -> dict:
    """
    立刻做一次备份。

    返回 {"ok": bool, "path": str, "message": str}
    """
    if not config.DB_PATH.exists():
        return {
            "ok": False,
            "path": "",
            "message": "数据库文件还不存在，不需要备份（第一次运行时会出现这种情况）",
        }

    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = config.BACKUP_DIR / ("todo_%s.db" % _timestamp())

    if target.exists():
        # 同一秒内备份两次，加个后缀避免覆盖
        target = config.BACKUP_DIR / ("todo_%s_1.db" % _timestamp())

    try:
        # 官方在线备份：即使数据库正在被写入也安全
        source = sqlite3.connect(str(config.DB_PATH))
        dest = sqlite3.connect(str(target))
        try:
            with dest:
                source.backup(dest)
        finally:
            source.close()
            dest.close()
    except sqlite3.Error as exc:
        return {"ok": False, "path": str(target), "message": "备份失败：%s" % exc}

    removed = prune()
    size_kb = round(target.stat().st_size / 1024, 1)

    msg = "备份成功（%s KB）" % size_kb
    if removed:
        msg += "，已清理 %d 份旧备份" % removed

    return {"ok": True, "path": str(target), "message": msg, "reason": reason}


def prune(keep: int = None) -> int:
    """
    只保留最近 keep 份备份，多的删掉。返回删除的份数。

    为什么要限制份数？
        待办数据虽然不大，但每次启动都备份的话，一年下来会堆几百个文件。
        保留最近 7 份，足够应付"昨天还好好的，今天发现数据坏了"这类情况。
    """
    if keep is None:
        keep = config.BACKUP_KEEP

    backups = list_backups()          # 已按文件名倒序 = 最新的在前
    removed = 0
    for item in backups[keep:]:
        try:
            Path(item["path"]).unlink()
            removed += 1
        except OSError:
            pass
    return removed


def backup_on_startup() -> dict:
    """
    程序启动时调用：满足条件才真的备份。

    条件：
        1. config.BACKUP_ON_STARTUP 为 True
        2. 距离上一次备份已经超过 BACKUP_MIN_INTERVAL_MINUTES 分钟

    为什么要限制频率？
        调试时你可能一分钟重启十次程序。每次都备份会刷出一堆没用的文件，
        把有用的旧备份挤掉 —— 那就本末倒置了。
    """
    if not config.BACKUP_ON_STARTUP:
        return {"ok": False, "message": "启动备份已关闭", "skipped": True}

    age = newest_backup_age_minutes()
    if age is not None and age < config.BACKUP_MIN_INTERVAL_MINUTES:
        return {
            "ok": False,
            "skipped": True,
            "message": "距上次备份只有 %.0f 分钟，跳过" % age,
        }

    result = make_backup(reason="startup")
    result["skipped"] = False
    return result
