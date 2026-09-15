# -*- coding: utf-8 -*-
"""
task_repo.py -- 待办事项的数据访问层（Repository）。

【这一层的定位】
    全项目【唯一】允许直接操作数据库的地方。
    它只做"存取数据"这件纯粹的事，不做业务判断。

【它不负责什么】
    不判断"标题是否为空"、"提醒时间能不能早于现在"—— 那是 services 层的事。
    这样分开的好处：以后换数据库（SQLite -> Postgres），只需要重写这一层。

【命名约定】
    create / get / list / update / delete / count —— 看一眼就知道干什么。
"""

from sqlalchemy import func, select

from app import config
from app.core import timeutil
from app.database import get_session
from app.models import Task


def create(
    title: str,
    description: str = None,
    priority: int = config.PRIORITY_NORMAL,
    due_at: str = None,
    remind_at: str = None,
    remind_enabled: bool = False,
    category: str = None,
    user_id: int = 1,
) -> Task:
    """
    新增一条待办，返回创建好的对象（里面已经带了自动生成的 id）。

    注意 created_at / updated_at 是【这里】自动写入的，
    不让调用方传 —— 防止有人写出"创建时间比现在还晚"这种脏数据。
    """
    now = timeutil.now_str()

    with get_session() as session:
        task = Task(
            title=title,
            description=description,
            status=config.STATUS_TODO,
            priority=priority,
            due_at=due_at,
            remind_at=remind_at,
            remind_enabled=1 if remind_enabled else 0,
            category=category,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.flush()      # flush 会真正执行 INSERT，从而拿到自增的 id
                             # （commit 由 with 块结束时自动完成）
        return task


def get(task_id: int, user_id: int = 1):
    """
    按 id 查一条【未被删除的】任务，查不到返回 None。

    【注意这里多了 deleted_at IS NULL】
        进了回收站的任务对外一律"不存在"，
        所以 /api/tasks/{id} 查一条已删除的任务会正常返回 404，
        而不会把回收站里的内容泄漏出去。
    """
    with get_session() as session:
        stmt = select(Task).where(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
        )
        return session.execute(stmt).scalar_one_or_none()


def get_including_deleted(task_id: int, user_id: int = 1):
    """
    按 id 查一条，【包含】回收站里的任务。

    只有"还原"和"彻底删除"这两个操作才该用它 ——
    因为它们的操作对象，恰恰就是已经进了回收站的任务。
    """
    with get_session() as session:
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id)
        return session.execute(stmt).scalar_one_or_none()


def list_tasks(status: str = None, keyword: str = None, limit: int = None, offset: int = 0, user_id: int = 1):
    """
    查询待办列表。

    参数：
        status  : "todo" / "done" / None(全部)     —— 对应需求「按完成状态查看」
        keyword : 在标题和描述里做模糊搜索
        limit / offset : 分页（数据多的时候一次只取一页）

    排序规则：未完成在前 -> 截止时间早的在前 -> 新建的在前。
    """
    with get_session() as session:
        # deleted_at IS NULL：回收站里的任务不出现在列表里
        stmt = select(Task).where(Task.user_id == user_id, Task.deleted_at.is_(None))

        if status:
            stmt = stmt.where(Task.status == status)

        if keyword:
            # ★★【必须转义 LIKE 的元字符（BUG-041）】
            #   在 SQL 的 LIKE 里，% 和 _ 是通配符：
            #       % = 任意多个字符        _ = 任意一个字符
            #   用户想搜"完成度 100%"时，那个 % 会被当成"任意字符"——
            #   于是搜一个 % 就把【全部任务】都搜出来了（实测 31 条全中）。
            #
            #   注意：这【不是】SQL 注入（参数化查询已经挡住了注入），
            #   而是"元字符语义"问题 —— 安全（不让输入变成代码）和
            #   正确（让输入被当成普通数据）是要分别处理的两件事。
            #
            #   转义顺序很重要：必须**先转义反斜杠本身**，
            #   否则后面插入的那些反斜杠会被自己再转义一次。
            escaped = (keyword.replace("\\", "\\\\")
                              .replace("%", "\\%")
                              .replace("_", "\\_"))
            like = "%" + escaped + "%"
            stmt = stmt.where(Task.title.like(like, escape="\\")
                              | Task.description.like(like, escape="\\"))

        # 注意：SQLite 里字符串排序就是时间排序（因为格式是"年-月-日 时:分"）
        stmt = stmt.order_by(Task.status.asc(), Task.due_at.is_(None), Task.due_at.asc(), Task.id.desc())

        if limit:
            stmt = stmt.limit(limit).offset(offset)

        return list(session.execute(stmt).scalars().all())


def list_between(start_text: str, end_text: str, user_id: int = 1):
    """
    查询 due_at 落在 [start_text, end_text] 区间内的任务。

    【为什么可以直接用字符串比较大小？】
        因为决策 3B 把时间存成了 "YYYY-MM-DD HH:MM" 这种从大到小排列的格式，
        SQLite 里字符串比较就是逐字符比 ASCII，而它的顺序恰好和时间顺序一致：
            "2026-09-01 00:00" < "2026-09-20 08:30" < "2026-10-01 00:00"   ✓
        如果当初存的是 "2026-9-1 8:30"（不补零），比较就全乱了。

        —— 这正是当初选这个格式换来的好处。
    """
    with get_session() as session:
        stmt = (
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.deleted_at.is_(None),        # 回收站里的不占日历格子
                Task.due_at.is_not(None),
                Task.due_at >= start_text,
                Task.due_at <= end_text,
            )
            .order_by(Task.due_at.asc(), Task.id.asc())
        )
        return list(session.execute(stmt).scalars().all())


def list_undated(user_id: int = 1):
    """查询没有截止时间的任务（日历页右侧的"未安排日期"面板用）。"""
    with get_session() as session:
        stmt = (
            select(Task)
            .where(Task.user_id == user_id, Task.deleted_at.is_(None), Task.due_at.is_(None))
            .order_by(Task.status.asc(), Task.id.desc())
        )
        return list(session.execute(stmt).scalars().all())


def update(task_id: int, **fields) -> Task:
    """
    修改一条待办。

    用法：update(3, title="新标题", priority=2)

    为什么要过滤字段？
        如果直接把用户传来的数据全部塞进去，
        有人就能改 created_at、甚至改 id，把数据搞乱。
        所以这里用白名单，只允许改下面这些。
    """
    allowed = {
        "title", "description", "status", "priority",
        "due_at", "remind_at", "remind_enabled", "reminded_at",
        "category", "completed_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError("不允许修改这些字段: " + ", ".join(sorted(unknown)))

    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return None

        for key, value in fields.items():
            if key == "remind_enabled":
                value = 1 if value else 0
            setattr(task, key, value)

        # 标记状态时自动记录完成时间（这样统计"本周完成几件事"才有数据）
        if fields.get("status") == config.STATUS_DONE and task.completed_at is None:
            task.completed_at = timeutil.now_str()
        elif fields.get("status") == config.STATUS_TODO:
            task.completed_at = None

        task.updated_at = timeutil.now_str()
        session.flush()
        return task


# ===========================================================================
# 回收站（软删除）
#
# 【核心思路】
#   delete()  -> 只写 deleted_at，数据【还在数据库里】  （可恢复）
#   restore() -> 把 deleted_at 清成 NULL                （捞回来）
#   purge()   -> 真正执行 DELETE FROM tasks             （不可恢复）
#
#   使用者看到的是同样的效果：列表里立刻就没了。
#   区别在于——删错了还能不能救回来。
# ===========================================================================


def delete(task_id: int, user_id: int = 1) -> bool:
    """
    删除一条待办 —— 【软删除】，只是打个标记，数据仍在数据库里。

    【为什么从硬删除改成软删除？】
        这是用一次真实的数据丢失事故换来的（见 BUG-011）。
        硬删除是"从数据库里抹掉"，没有后悔药；
        软删除是"移到回收站"，删错了还能捞回来。

        两者对使用者的体验几乎一样，成本也只差一个字段，
        但一个可以挽回，一个不可以。

    返回 True  表示这次真的删掉了；
    返回 False 表示它本来就不存在，或者【已经在回收站里了】。
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.deleted_at is not None:
            return False
        now = timeutil.now_str()
        task.deleted_at = now
        task.updated_at = now      # 删除也算一次修改，updated_at 要跟着走
        return True


def restore(task_id: int, user_id: int = 1) -> bool:
    """
    从回收站还原一条。

    返回 False 表示它不在回收站里 —— 要么不存在，要么本来就没被删过。
    （这样"还原一条没删过的任务"会被挡住，而不是悄悄成功。）
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.deleted_at is None:
            return False
        task.deleted_at = None
        task.updated_at = timeutil.now_str()
        return True


def purge(task_id: int, user_id: int = 1) -> bool:
    """
    【彻底删除】—— 真执行 DELETE，不可恢复。

    【为什么要限制"必须先删过"？】
        这既是业务规则（得先进回收站才能清空），也是一道安全闸门：
        如果 purge 也能删正常任务，那某天有人手滑把 purge 当成普通删除调用，
        就会【直接永久删掉】用户的数据，连回收站都没得救。

        多这一行判断，就堵死了这条路。
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.deleted_at is None:
            return False
        session.delete(task)      # ← 只有这里是真删
        return True


def list_deleted(user_id: int = 1):
    """
    回收站列表，按【删除时间倒序】—— 最近删的排最前面。

    为什么这么排？因为"刚删错、想赶紧找回来"是最常见的场景，
    把最新的放最上面能省掉一次寻找。
    """
    with get_session() as session:
        stmt = (
            select(Task)
            .where(Task.user_id == user_id, Task.deleted_at.is_not(None))
            .order_by(Task.deleted_at.desc(), Task.id.desc())
        )
        return list(session.execute(stmt).scalars().all())


def count_deleted(user_id: int = 1) -> int:
    """回收站里有几条。用于页面上显示「回收站 (3)」这样的角标。"""
    with get_session() as session:
        stmt = select(func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.deleted_at.is_not(None),
        )
        return session.execute(stmt).scalar() or 0


def purge_all(user_id: int = 1, confirm: bool = False) -> int:
    """
    清空回收站 —— 永久删除里面【全部】任务，返回删除条数。

    ⚠️ 和 reset_all() 一样不可恢复，所以同样必须显式传 confirm=True。
    （数据安全铁律二：危险操作必须有显式确认参数）

    注意它【只删回收站里的】，不会碰正常任务 ——
    即使有人误调用，损失也被限制在"已经删过一次"的范围内。
    """
    if not confirm:
        raise ValueError(
            "purge_all() 会永久删除回收站里的全部任务且无法恢复，"
            "必须显式调用 purge_all(confirm=True)。"
        )

    with get_session() as session:
        stmt = select(Task).where(Task.user_id == user_id, Task.deleted_at.is_not(None))
        tasks = session.execute(stmt).scalars().all()
        n = len(tasks)
        for task in tasks:
            session.delete(task)
        return n


def count_by_status(user_id: int = 1) -> dict:
    """
    统计各状态的数量，例如 {"todo": 3, "done": 5, "total": 8}。
    列表页顶部的"全部 / 未完成 / 已完成"标签上的数字就靠它。
    """
    with get_session() as session:
        stmt = (
            select(Task.status, func.count(Task.id))
            .where(Task.user_id == user_id, Task.deleted_at.is_(None))
            .group_by(Task.status)
        )
        rows = session.execute(stmt).all()

    result = {config.STATUS_TODO: 0, config.STATUS_DONE: 0}
    for status, count in rows:
        result[status] = count
    result["total"] = result[config.STATUS_TODO] + result[config.STATUS_DONE]
    return result


def due_for_reminder(now_text: str = None, user_id: int = 1):
    """
    【第 9 步提醒功能的核心查询】

    找出所有"该弹提醒"的任务，条件是：
        1. 开启了提醒         remind_enabled = 1
        2. 还没完成           status = 'todo'
        3. 提醒时间已到       remind_at <= 当前时间
        4. 还没提醒过         reminded_at IS NULL     <- 防重复的关键

    第 4 条最重要：提醒一旦弹过就把 reminded_at 写上，
    这样程序重启、页面刷新都不会重复弹同一条提醒。
    """
    if now_text is None:
        now_text = timeutil.now_str()

    with get_session() as session:
        stmt = (
            select(Task)
            .where(
                Task.user_id == user_id,
                # 已删除的任务绝不能再弹提醒 —— 否则"删了还提醒"会让人莫名其妙
                Task.deleted_at.is_(None),
                Task.remind_enabled == 1,
                Task.status == config.STATUS_TODO,
                Task.remind_at.is_not(None),
                Task.remind_at <= now_text,
                Task.reminded_at.is_(None),
            )
            .order_by(Task.remind_at.asc())
        )
        return list(session.execute(stmt).scalars().all())


def mark_reminded(task_id: int, when: str = None) -> bool:
    """把某条任务标记为"已提醒"，防止它被弹第二次。"""
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return False
        task.reminded_at = when or timeutil.now_str()
        task.updated_at = timeutil.now_str()
        return True


def reset_all(user_id: int = 1, confirm: bool = False) -> int:
    """
    删除该用户的【全部】待办。返回删除条数。

    ⚠️⚠️ 这是一个不可恢复的危险操作，所以【必须】显式传 confirm=True 才会执行。

    【为什么加这道闸门？】
        开发过程中，我写的临时测试脚本里顺手调用了 reset_all()，
        结果删掉了用户真实创建的全部待办，无法恢复（见 BUG-011）。

        加了 confirm 参数后，那种"顺手一写就清空数据库"的脚本
        会直接抛异常停下，而不是安静地把数据删光。

        —— 减少一次事故，往往只需要一个"你确定吗"。
    """
    if not confirm:
        raise ValueError(
            "reset_all() 会删除全部待办且无法恢复，必须显式调用 reset_all(confirm=True)。"
            "如果你是在写测试，请改用隔离数据库（见 scripts/_isolation.py）。"
        )

    with get_session() as session:
        tasks = session.execute(select(Task).where(Task.user_id == user_id)).scalars().all()
        n = len(tasks)
        for task in tasks:
            session.delete(task)
        return n
