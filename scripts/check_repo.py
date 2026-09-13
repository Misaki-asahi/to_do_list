# -*- coding: utf-8 -*-
"""
check_repo.py -- 数据访问层自检脚本。

用法（在项目根目录执行，或直接双击 check.bat）：
    python scripts/check_repo.py

【重要设计：测试绝不碰你的真实数据】
    这个脚本在导入 app 之前就调用 use_isolated_db()，
    把数据库切到 data/test/check_repo.db，并在导入后立刻 assert_isolated() 复核。
    所以它创建、修改、删除的全是临时数据，你 data/todo.db 里的待办一条都不会少。
    这是专业做法：测试环境与真实环境必须隔离（见 docs/问题记录 BUG-011）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 输出可能被重定向到文件，此时编码可能是 GBK（见 BUG-019），先做一层保护
from scripts._console import use_safe_output   # noqa: E402
use_safe_output()

# ★★ 必须放在所有 "from app import ..." 之前 ★★
# 它把数据库切到 data/test/check_repo.db，
# 保证这个脚本无论怎么折腾都不会碰到你的真实待办。
from scripts._isolation import assert_isolated, cleanup, use_isolated_db   # noqa: E402

TEST_DB = use_isolated_db("check_repo")

from app import config, database          # noqa: E402

# 第二道安全网：即使上面的隔离失效了，这一步也会立刻中止，绝不误删你的待办
assert_isolated()
from app.core import timeutil             # noqa: E402
from app.repositories import task_repo, setting_repo   # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    """一个小断言助手：打印结果并统计通过/失败数，而不是一出错就崩掉。"""
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  [通过] %s" % name)
    else:
        FAIL += 1
        print("  [失败] %s  %s" % (name, detail))


def banner(text):
    """打印小节标题。空行 + 标题，让长输出一眼能看出分组。"""
    print()
    print(text)


# ======================================================================
# 下面每个函数负责一组检查。
# 它们都收一个共享的 ctx 字典：前一组把新建的任务放进去，后一组拿出来用。
# 这样拆开的好处是——每一组都短到能一眼看完，出问题也知道该去哪找。
# ======================================================================

def check_schema(ctx):
    """【1】建表：确认表结构和你设计的一致。"""
    banner("【1】建表")
    from sqlalchemy import inspect

    tables = sorted(inspect(database.engine).get_table_names())
    check("创建了 tasks / settings 两张表", tables == ["settings", "tasks"], str(tables))

    cols = [c["name"] for c in inspect(database.engine).get_columns("tasks")]
    # 15 = 原来的 14 个 + 回收站用的 deleted_at
    check("tasks 表有 15 个字段", len(cols) == 15, str(len(cols)))
    check("包含防重复提醒字段 reminded_at", "reminded_at" in cols)
    check("包含回收站字段 deleted_at", "deleted_at" in cols)


def check_create(ctx):
    """【2】增：新建任务后的默认值是否正确。"""
    banner("【2】增 Create")

    t1 = task_repo.create(title="买牛奶", priority=config.PRIORITY_LOW)
    t2 = task_repo.create(
        title="交作业", description="发给老师", priority=config.PRIORITY_HIGH,
        due_at=timeutil.add_minutes(timeutil.now_str(), 600),
        remind_at=timeutil.add_minutes(timeutil.now_str(), 540),
        remind_enabled=True, category="学习",
    )
    check("新建后拿到自增 id", t1.id == 1 and t2.id == 2, "%s / %s" % (t1.id, t2.id))
    check("默认状态是未完成", t1.status == config.STATUS_TODO)
    check("created_at / updated_at 自动写入", bool(t1.created_at) and bool(t1.updated_at))

    ctx["t1"] = t1
    ctx["t2"] = t2


def check_read(ctx):
    """【3】查：列表、按 id、关键字搜索。"""
    banner("【3】查 Read")
    t2 = ctx["t2"]

    check("总数正确", len(task_repo.list_tasks()) == 2)
    check("按 id 精确查询", task_repo.get(t2.id).title == "交作业")
    check("查不存在的 id 返回 None", task_repo.get(99999) is None)
    check("关键字搜索命中描述", len(task_repo.list_tasks(keyword="老师")) == 1)
    check("关键字搜不到时返回空", len(task_repo.list_tasks(keyword="不存在的东西")) == 0)


def check_update(ctx):
    """【4】改：字段更新与 updated_at 单调递增。"""
    banner("【4】改 Update")
    t1 = ctx["t1"]

    old = task_repo.get(t1.id).updated_at
    task_repo.update(t1.id, title="买牛奶和面包", priority=config.PRIORITY_HIGH)
    check("标题已更新", task_repo.get(t1.id).title == "买牛奶和面包")
    check("updated_at 不会倒退", task_repo.get(t1.id).updated_at >= old)


def check_status_link(ctx):
    """【5】完成状态联动：done <-> completed_at 必须同进同退。"""
    banner("【5】完成状态联动")
    t2 = ctx["t2"]

    task_repo.update(t2.id, status=config.STATUS_DONE)
    check("标记完成后写入 completed_at", task_repo.get(t2.id).completed_at is not None)
    task_repo.update(t2.id, status=config.STATUS_TODO)
    check("取消完成会清空 completed_at", task_repo.get(t2.id).completed_at is None)
    stats = task_repo.count_by_status()
    check("按状态统计正确", stats == {"todo": 2, "done": 0, "total": 2}, str(stats))


def check_field_whitelist(ctx):
    """【6】字段白名单：不允许用户改 created_at 这类不该改的字段。"""
    banner("【6】字段白名单防呆")
    t1 = ctx["t1"]

    try:
        task_repo.update(t1.id, created_at="1999-01-01 00:00")
        check("拒绝修改 created_at", False, "竟然允许修改")
    except ValueError:
        check("拒绝修改 created_at", True)


def check_reminder_scan(ctx):
    """【7】提醒扫描与防重复：只扫「到点了 + 开着提醒 + 没完成 + 没提醒过」的。"""
    banner("【7】提醒扫描与防重复")

    past = task_repo.create(title="到点该提醒", remind_at="2020-01-01 09:00", remind_enabled=True)
    future = task_repo.create(title="未来的提醒", remind_at="2099-01-01 09:00", remind_enabled=True)
    off = task_repo.create(title="没开提醒", remind_at="2020-01-01 09:00", remind_enabled=False)
    done_task = task_repo.create(title="已完成的不提醒", remind_at="2020-01-01 09:00", remind_enabled=True)
    task_repo.update(done_task.id, status=config.STATUS_DONE)

    found_ids = [t.id for t in task_repo.due_for_reminder()]
    check("扫出已到点的任务", past.id in found_ids, str(found_ids))
    check("不扫未来的提醒", future.id not in found_ids)
    check("不扫关闭提醒的任务", off.id not in found_ids)
    check("不扫已完成的任务", done_task.id not in found_ids)

    task_repo.mark_reminded(past.id)
    check("标记已提醒后不再扫出",
          past.id not in [t.id for t in task_repo.due_for_reminder()])

    ctx["past"] = past


def check_delete(ctx):
    """【8】删：删除要能区分「删掉了」和「本来就没有」。"""
    banner("【8】删 Delete")
    past = ctx["past"]

    check("删除存在的记录返回 True", task_repo.delete(past.id) is True)
    check("重复删除返回 False", task_repo.delete(past.id) is False)
    check("删除不存在的 id 返回 False", task_repo.delete(99999) is False)


def check_settings_table(ctx):
    """【9】设置表：一个通用的键值对存储，将来加新设置不用改表结构。"""
    banner("【9】设置表 键值对")

    setting_repo.set_value("autostart_enabled", "1")
    setting_repo.set_value("theme", "dark")
    check("布尔型设置读取正确", setting_repo.get_bool("autostart_enabled") is True)
    check("默认值机制生效", setting_repo.get("not_exist", "默认值") == "默认值")
    check("重复写入是更新而非新增", setting_repo.all_settings().get("theme") == "dark")

    setting_repo.set_value("theme", "light")
    check("更新生效", setting_repo.get("theme") == "light")


def check_timeutil(ctx):
    """【10】时间工具：格式校验、解析、取日期、加减。"""
    banner("【10】时间工具")

    check("合法时间格式校验", timeutil.is_valid("2026-12-31 23:59") is True)
    check("非法格式返回 False 而不抛异常", timeutil.is_valid("2026/12/31") is False)
    check("空值安全", timeutil.parse(None) is None)
    check("取日期部分", timeutil.date_part("2026-12-31 23:59") == "2026-12-31")
    check("时间加法", timeutil.add_minutes("2026-12-31 23:59", 1) == "2027-01-01 00:00")


# 检查组的执行顺序。想加新检查，就在对应位置插一个函数、往这里加个名字。
CHECK_GROUPS = (
    check_schema,
    check_create,
    check_read,
    check_update,
    check_status_link,
    check_field_whitelist,
    check_reminder_scan,
    check_delete,
    check_settings_table,
    check_timeutil,
)


def main():
    # 每次从干净的临时库开始
    if TEST_DB.exists():
        TEST_DB.unlink()
    config.ensure_dirs()
    database.init_db()

    print("测试数据库 : %s" % config.DB_PATH)
    print("（与真实数据隔离，你的待办不会被动到）")

    ctx = {}
    for group in CHECK_GROUPS:
        group(ctx)

    print()
    print("=" * 64)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))

    # 清理临时数据库（只动临时文件，不碰你的真实数据）
    database.engine.dispose()
    cleanup(TEST_DB)
    print("临时数据库已清理，你的真实数据未受影响。")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
