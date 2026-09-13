# -*- coding: utf-8 -*-
"""
demo_db.py -- 功能 2 的验证脚本：证明"数据真的存下来了"。

用法（在项目根目录执行）：
    python scripts/demo_db.py            # 新建一条演示任务，并列出全部
    python scripts/demo_db.py --list     # 只列出，不新增
    python scripts/demo_db.py --reset    # 清空所有演示数据

【最重要的验证方法】
    连续运行两次 python scripts/demo_db.py，
    第二次你能看到第一次那条数据【还在】—— 这就是持久化。
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 把项目根目录加入模块搜索路径。
# 因为直接运行 scripts/demo_db.py 时，Python 只会把 scripts/ 当作搜索起点，
# 找不到 app 包。这一行让 "from app import ..." 能正常工作。
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, database                 # noqa: E402
from app.core import timeutil                    # noqa: E402
from app.repositories import task_repo           # noqa: E402


def show_tables():
    """打印数据库里有哪些表、每张表有哪些字段。"""
    from sqlalchemy import inspect

    inspector = inspect(database.engine)
    tables = sorted(inspector.get_table_names())
    print("数据库文件 : %s" % config.DB_PATH)
    print("文件已存在 : %s" % config.DB_PATH.exists())
    print("表数量     : %d  ->  %s" % (len(tables), tables))
    for name in tables:
        cols = inspector.get_columns(name)
        print("  表 %s 的字段（%d 个）:" % (name, len(cols)))
        print("    " + ", ".join(c["name"] for c in cols))


def show_tasks():
    """列出所有待办。"""
    tasks = task_repo.list_tasks()
    stats = task_repo.count_by_status()
    print()
    print("当前共 %d 条（未完成 %d / 已完成 %d）" % (
        stats["total"], stats["todo"], stats["done"]))
    if not tasks:
        print("  （还没有任何待办）")
        return
    print("  %-4s %-22s %-8s %-18s %s" % ("ID", "标题", "状态", "截止时间", "创建时间"))
    print("  " + "-" * 78)
    for t in tasks:
        print("  %-4s %-22s %-8s %-18s %s" % (
            t.id, t.title[:20], config.STATUS_NAMES.get(t.status, t.status),
            t.due_at or "-", t.created_at))


def main():
    args = sys.argv[1:]

    # 1) 确保目录和表都存在（第一次运行会自动创建）
    config.ensure_dirs()
    database.init_db()

    if "--reset" in args:
        # confirm=True 是必须的：这个参数是一道"你确定吗"的闸门。
        # 这里是用户明确敲了 --reset 才走到这里，属于有意为之，所以可以确认。
        n = task_repo.reset_all(confirm=True)
        print("已清空 %d 条待办。" % n)
        show_tasks()
        return

    if "--list" not in args:
        # 2) 新建一条演示任务
        n = task_repo.count_by_status()["total"] + 1
        task = task_repo.create(
            title="演示任务 #%d" % n,
            description="这条数据由 scripts/demo_db.py 创建。关掉程序再运行 --list，它还在。",
            priority=config.PRIORITY_NORMAL,
            due_at=timeutil.add_minutes(timeutil.now_str(), 60),
        )
        print("已创建任务：id=%s  title=%s" % (task.id, task.title))
        print()
        print(">>> 现在把窗口关掉，再执行一次：")
        print("        python scripts/demo_db.py --list")
        print("    如果能看到刚才这条任务，说明数据【真正持久化保存】了。")

    show_tables()
    show_tasks()


if __name__ == "__main__":
    main()
