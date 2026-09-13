# -*- coding: utf-8 -*-
"""
reminder_service.py -- 提醒的业务规则（模块 M5）。

【这个模块的核心职责】
    1. 扫描"到点该提醒"的任务
    2. 发 Windows 系统通知（右下角，不点不走）
    3. 标记已提醒（防止重复）
    4. 把它们放进"UI 收件箱"，供页面显示带按钮的提示

【两种通知的关系】
    系统通知（后端发）  ：保证"你一定能看到"，哪怕浏览器关着
    页面内提示（前端发）：提供操作按钮（标记完成 / 稍后提醒）

    这不是重复劳动，而是各司其职。
"""

import threading

from app import config
from app.core import notifier, timeutil
from app.repositories import setting_repo, task_repo

# ---------------------------------------------------------------------------
# UI 收件箱
#
# 【为什么放在内存里而不是数据库？】
#     这里存的是"已经弹过系统通知、但页面还没显示过"的临时状态。
#     它的生命周期只有几秒到几分钟，属于典型的"进程内临时数据"。
#     放进数据库反而要加字段、写迁移，不划算。
#
# 【缺点】程序重启后收件箱清空。
#     但这时系统通知早就发过了，用户不会因此漏掉提醒，所以可以接受。
# ---------------------------------------------------------------------------
_inbox = {}
_inbox_lock = threading.Lock()


def fetch_due(limit: int = None):
    """
    取出"到点该提醒"的任务（不标记）。

    保留这个函数是为了手动测试和排查；
    正式的提醒流程走 scan_and_fire()。
    """
    if limit is None:
        limit = config.REMINDER_MAX_BATCH
    return task_repo.due_for_reminder()[:limit]


def scan_and_fire() -> dict:
    """
    【提醒的主流程】扫描 -> 发系统通知 -> 标记 -> 放进收件箱。

    返回统计信息，方便后台线程记录和设置页展示：
        {"fired": n, "native_ok": m, "notified": [...]}
    """
    tasks = task_repo.due_for_reminder()
    batch = tasks[:config.REMINDER_MAX_BATCH]

    fired = []
    native_ok = 0

    for task in batch:
        # ---- 1. 发系统通知 ----
        lines = []
        if task.due_at:
            lines.append("截止：%s" % task.due_at)
        elif task.remind_at:
            lines.append("提醒时间：%s" % task.remind_at)

        result = notifier.show(
            title="待办提醒",
            body=task.title,
            lines=lines,
        )

        # ---- 2. ★ 只有通知真的发出去了，才标记"已提醒" ----
        # 为什么这么谨慎？
        #   如果通知失败（比如被系统策略拦了、找不到 PowerShell），
        #   却把任务标记成"已提醒"，这条提醒就【永远消失了】。
        #   所以：宁可下次再试一遍，也不能把没送达的提醒吃掉。
        if result.get("ok"):
            task_repo.mark_reminded(task.id, timeutil.now_str())
            native_ok += 1

        # ---- 3. 放进 UI 收件箱（无论系统通知成没成功）----
        # 页面打开时就能看到带按钮的提示；系统通知失败时这也是唯一的提醒途径。
        payload = task.to_dict()
        payload["_native_ok"] = bool(result.get("ok"))
        payload["_native_msg"] = result.get("message", "")
        with _inbox_lock:
            _inbox[task.id] = payload

        fired.append(payload)

    return {"fired": len(fired), "native_ok": native_ok, "notified": fired}


def inbox() -> dict:
    """
    页面来取"已经弹过但还没在页面上显示"的提醒。

    每次调用返回当前的快照，但【不会】清空 ——
    要等页面明确调用 ack() 才移除。
    这样即使页面刷新、或者请求丢了，提示也不会凭空消失。
    """
    with _inbox_lock:
        items = list(_inbox.values())
    # 页面是否要自己播放提示音。
    # 【曾经的坑】原来这里传的是 native_supported（"系统支不支持"），
    #   但"系统支持"不等于"通知真的显示出来了" ——
    #   用户实测系统通知没弹出来，而页面以为"系统会响"所以保持安静，
    #   结果【完全没声音】。现在改由用户在设置页自己决定（默认开启）。
    sound_enabled = setting_repo.get_bool("notify_sound_enabled", True)

    return {
        "native_supported": notifier.is_supported(),
        "sound_enabled": sound_enabled,
        "poll_seconds": config.REMINDER_SCAN_SECONDS,
        "snooze_minutes": config.REMINDER_SNOOZE_MINUTES,
        "items": items,
    }


def ack(task_id: int) -> bool:
    """
    页面确认"这条提醒我已经看到并处理了"，把它从收件箱里移除。

    同时兜底地标记 reminded_at：
        万一系统通知没发成功（所以之前没标记），
        用户在页面上看到了并关掉 —— 这也算"送达了"，应该标记掉，
        否则后台线程会每 20 秒重试一次，一直弹个不停。
    """
    with _inbox_lock:
        item = _inbox.pop(task_id, None)

    if item is None:
        return False

    task = task_repo.get(task_id)
    if task is not None and not task.reminded_at:
        task_repo.mark_reminded(task_id, timeutil.now_str())
    return True


def clear_inbox():
    """清空收件箱（设置页的"清空提示"用）。"""
    with _inbox_lock:
        count = len(_inbox)
        _inbox.clear()
    return count


def snooze(task_id: int, minutes: int = None):
    """
    "稍后提醒"：把提醒时间往后推 N 分钟，并清空已提醒标记。

    用"当前时间 + N"，而不是"原提醒时间 + N"。
    因为如果你 10:00 才点"稍后 10 分钟"，按原时间（8:00）算会得到 8:10 ——
    那是过去的时间，会立刻又弹一次。
    """
    from app.services.task_service import get_task     # 局部导入，避免循环引用

    task = get_task(task_id)          # 不存在会抛 NotFoundError
    if minutes is None:
        minutes = config.REMINDER_SNOOZE_MINUTES

    new_time = timeutil.add_minutes(timeutil.now_str(), minutes)

    # 必须同时清空 reminded_at，否则新时间到了也不会再弹
    task = task_repo.update(
        task_id,
        remind_at=new_time,
        reminded_at=None,
        remind_enabled=1,
    )

    # 从收件箱里也移除，避免页面还挂着一个已经推迟掉的提示
    with _inbox_lock:
        _inbox.pop(task_id, None)

    return task


def test_notification() -> dict:
    """
    发一条测试通知（设置页的"测试系统通知"按钮用）。

    有这个按钮很重要：系统通知可能被"专注助手""勿扰模式"或系统策略拦住，
    用户需要一种方式自己确认"到底是我没配好，还是它本来就不响"。
    """
    result = notifier.show(
        title="测试通知",
        body="如果你看到这条通知，说明系统通知工作正常。",
        lines=["它不会自动消失，点右上角的 × 或点掉它即可。"],
    )
    return result


def stats() -> dict:
    """给设置页用的提醒统计。"""
    tasks = task_repo.list_tasks(status=config.STATUS_TODO)
    enabled = [t for t in tasks if t.remind_enabled]
    with _inbox_lock:
        inbox_count = len(_inbox)

    return {
        "pending": len(enabled),
        "notified": len([t for t in enabled if t.reminded_at]),
        "inbox": inbox_count,
        "poll_seconds": config.REMINDER_SCAN_SECONDS,
        "snooze_minutes": config.REMINDER_SNOOZE_MINUTES,
        "native": notifier.status(),
    }
