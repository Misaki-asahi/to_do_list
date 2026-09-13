# -*- coding: utf-8 -*-
"""
test_reminder.py -- 提醒功能的测试工具。

【为什么需要它？】
    正常创建一个提醒，最少也要等到"1 分钟后"才能看到效果，
    调试一次要等一分钟，太慢了。

    这个脚本直接往数据库里写一条"提醒时间已经过去 1 分钟"的任务，
    这样你在浏览器里刷新一下（或者等最多 20 秒的轮询），提醒就会立刻弹出来。

用法（在项目根目录执行）：
    python scripts/test_reminder.py            # 造一条"立刻该提醒"的任务
    python scripts/test_reminder.py --list     # 看看当前有哪些待提醒的任务
    python scripts/test_reminder.py --clear    # 删掉所有由本脚本产生的测试任务
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, database                    # noqa: E402
from app.core import timeutil                       # noqa: E402
from app.repositories import task_repo              # noqa: E402

MARK = "[提醒测试]"


def main():
    args = sys.argv[1:]
    config.ensure_dirs()
    database.init_db()

    if "--clear" in args:
        removed = 0
        for task in task_repo.list_tasks():
            if task.title.startswith(MARK):
                task_repo.delete(task.id)
                removed += 1
        print("已删除 %d 条测试任务。" % removed)
        return

    if "--list" in args:
        tasks = task_repo.list_tasks()
        print("当前共 %d 条待办：" % len(tasks))
        for t in tasks:
            flag = "已提醒" if t.reminded_at else "未提醒"
            print("  #%-3s %-28s 提醒时间=%-18s %s" % (
                t.id, t.title[:26], t.remind_at or "无",
                ("(" + flag + ")") if t.remind_enabled else "(未开启提醒)"))
        return

    # 默认行为：造一条"提醒时间已经过去 1 分钟"的任务
    #
    # ⚠️ 这是【故意】把提醒时间设成过去 1 分钟的，不是 Bug：
    #     如果设成"1 分钟后"，你就得真的等一分钟才能看到效果。
    #     设成"已经过去"，后台线程下一次扫描（最多 20 秒）就会立刻发现它。
    #   所以你看到"提醒时间比当前时间早一分钟"是正常的，说明脚本工作正常。
    past = timeutil.add_minutes(timeutil.now_str(), -1)
    task = task_repo.create(
        title=MARK + " 这是一条测试提醒",
        description="看到这个弹窗，就说明提醒功能正常工作了。",
        priority=config.PRIORITY_HIGH,
        remind_at=past,
        remind_enabled=True,
    )

    print("已创建测试任务：")
    print("   id       = %s" % task.id)
    print("   标题     = %s" % task.title)
    print("   提醒时间 = %s  （故意设成过去 1 分钟，好让它立刻触发）" % task.remind_at)
    print("   当前时间 = %s" % timeutil.now_str())
    print()
    print(">>> 现在什么都不用做，等最多 20 秒：")
    print("    ① 屏幕右下角会弹出 Windows 系统通知（不点掉不会消失）")
    print("    ② 如果浏览器开着待办页面，页面右下角也会出现一张提醒卡片")
    print()
    print("    （后台提醒线程每 %d 秒扫描一次）" % config.REMINDER_SCAN_SECONDS)
    print()
    print("    清理测试数据：python scripts/test_reminder.py --clear")


if __name__ == "__main__":
    main()
