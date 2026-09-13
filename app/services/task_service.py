# -*- coding: utf-8 -*-
"""
task_service.py -- 待办的【业务规则】层（模块 M4）。

【这一层负责什么】
    "这件事在业务上合不合理"，例如：
        - 提醒时间能不能早于当前时间？
        - 填了提醒时间但没开提醒开关，应该怎么办？
        - 标题会不会太长？

【这一层不负责什么】
    不碰数据库（那是 repositories 的事），不碰 HTTP（那是 routers 的事）。

【为什么要单独一层？】
    因为业务规则会变，而存取方式不会。
    比如某天你想加一条"标题不能重复"的规则，只需要改这个文件，
    完全不用碰数据库代码和路由代码。
"""

from app import config
from app.core import timeutil
from app.repositories import task_repo


# 【第三阶段重构】异常类型统一搬到了 app/core/errors.py。
# 原因：BusinessError / NotFoundError 不止任务模块会用，
#       提醒、日历、设置模块都要用。定义在这里的话，
#       别的模块就得"为了拿一个异常类而 import 整个 task_service"，依赖很别扭。
#
# 这里保留 import，是为了兼容老代码里的 task_service.BusinessError 写法。
from app.core.errors import BusinessError, NotFoundError   # noqa: F401


def create_task(data) -> "object":
    """
    新建待办。

    参数 data 是 schemas.TaskCreate 对象（已经通过了格式校验）。
    这一步做的是【格式校验之外】的业务判断。
    """
    title = (data.title or "").strip()

    # ---- 业务规则 1：填了提醒时间，就自动打开提醒开关 ----
    # 为什么？
    #   从用户角度看，"我填了提醒时间"本身就意味着"我要被提醒"。
    #   如果还要再手动去勾一个开关，是多余的心智负担。
    remind_enabled = bool(data.remind_enabled or data.remind_at)

    # ---- 业务规则 2：开了提醒就必须有提醒时间 ----
    if remind_enabled and not data.remind_at:
        raise BusinessError("开启了提醒，就必须填写提醒时间")

    # ---- 业务规则 3：提醒时间不能是过去 ----
    # 为什么？
    #   如果允许填过去的时间，后台扫描线程会在你点"添加"的瞬间就弹出提醒，
    #   用户会觉得"这软件坏了吧"。不如直接告诉他填错了。
    if remind_enabled and data.remind_at:
        if timeutil.parse(data.remind_at) < timeutil.now():
            raise BusinessError(
                "提醒时间不能早于当前时间（现在是 %s）" % timeutil.now_str()
            )

    # ---- 业务规则 4：截止时间也不能是过去 ----
    if data.due_at and timeutil.parse(data.due_at) < timeutil.now():
        raise BusinessError("截止时间不能早于当前时间（现在是 %s）" % timeutil.now_str())

    # ---- 业务规则 5：优先级必须是合法值 ----
    # schemas 已经用 ge=0, le=2 限制过了，这里再兜一层，
    # 防止以后有人绕过接口直接调用本函数。
    if data.priority not in config.PRIORITY_NAMES:
        raise BusinessError("优先级只能是 0（低）/ 1（中）/ 2（高）")

    return task_repo.create(
        title=title,
        description=data.description,
        priority=data.priority,
        due_at=data.due_at,
        remind_at=data.remind_at,
        remind_enabled=remind_enabled,
        category=(data.category or "").strip() or None,
    )


def list_tasks(status: str = None, keyword: str = None):
    """查询待办列表。

    这里做一层"参数清洗"，因为查询参数是从网址上直接来的，不可信：
        - status 只允许 "todo" / "done" / None，其他值一律忽略（等于查全部）
        - keyword 去掉首尾空格，空的就当没传
    """
    if status not in (config.STATUS_TODO, config.STATUS_DONE):
        status = None
    keyword = (keyword or "").strip() or None
    return task_repo.list_tasks(status=status, keyword=keyword)


def get_stats() -> dict:
    """统计数字（列表页顶部显示）。"""
    return task_repo.count_by_status()


def get_task(task_id: int):
    """按 id 取一条，不存在就抛 NotFoundError。"""
    task = task_repo.get(task_id)
    if task is None:
        raise NotFoundError("找不到 id 为 %s 的待办（可能已被删除）" % task_id)
    return task


def update_task(task_id: int, changes: dict):
    """
    修改一条待办。

    参数 changes 是【只包含用户真正传过的字段】的字典
    （由路由层的 model_dump(exclude_unset=True) 产生）。
    """
    task = get_task(task_id)

    # ---- 规则 1：改了标题就再去一次空格 ----
    if "title" in changes:
        changes["title"] = (changes["title"] or "").strip()
        if not changes["title"]:
            raise BusinessError("标题不能为空，也不能只有空格")

    # ---- 规则 2：时间字段不能改成过去 ----
    # （提醒时间允许是过去吗？不允许 —— 否则一保存就会立刻弹提醒）
    for field, label in (("due_at", "截止时间"), ("remind_at", "提醒时间")):
        if field in changes and changes[field]:
            if timeutil.parse(changes[field]) < timeutil.now():
                raise BusinessError(
                    "%s不能早于当前时间（现在是 %s）" % (label, timeutil.now_str())
                )

    # ---- 规则 3：开启提醒就必须有提醒时间 ----
    # 注意要合并计算：可能这次只改了 remind_enabled，而 remind_at 是之前就存好的
    final_enabled = changes.get("remind_enabled", bool(task.remind_enabled))
    final_remind_at = changes.get("remind_at", task.remind_at)
    if final_enabled and not final_remind_at:
        raise BusinessError("开启了提醒，就必须填写提醒时间")

    # ---- 规则 4：提醒时间变了，就重置"已提醒"标记 ----
    # 为什么？举例：本来提醒设的是 8:00，已经弹过了；用户改成 9:00。
    # 如果不重置 reminded_at，改成 9:00 后【永远不会再弹】——
    # 因为扫描条件是"reminded_at 为空"，而它已经被填过了。
    if "remind_at" in changes and changes["remind_at"] != task.remind_at:
        changes["reminded_at"] = None

    # ---- 规则 5：优先级合法性 ----
    if "priority" in changes and changes["priority"] is not None:
        if changes["priority"] not in config.PRIORITY_NAMES:
            raise BusinessError("优先级只能是 0（低）/ 1（中）/ 2（高）")

    # ---- 规则 6：分类去掉首尾空格，空的变 None ----
    if "category" in changes:
        changes["category"] = (changes["category"] or "").strip() or None

    return task_repo.update(task_id, **changes)


def toggle_status(task_id: int):
    """
    切换完成状态：未完成 <-> 已完成。

    为什么提供"切换"而不是"设为完成/设为未完成"两个接口？
        因为前端就不用自己判断当前是什么状态了。
        点一下 -> 后端翻转 -> 返回新状态，逻辑只有一处，两边不会不一致。
    """
    task = get_task(task_id)
    new_status = (
        config.STATUS_TODO if task.status == config.STATUS_DONE else config.STATUS_DONE
    )
    # completed_at 的写入/清空由仓库层的 update() 自动处理
    return task_repo.update(task_id, status=new_status)


def delete_task(task_id: int):
    """
    删除一条待办 —— 移到回收站（软删除）。

    删除前先确认它存在，不存在就报 404。
    【注意】这里用的是 get_task()，它只找"未删除的"任务，
    所以对一条已经在回收站里的任务再删一次会返回 404，而不是静默成功。
    """
    get_task(task_id)          # 不存在会在这里抛 NotFoundError
    task_repo.delete(task_id)
    return True


# ===========================================================================
# 回收站（业务规则层）
#
# 【这三条规则为什么放在 service 而不是 repo？】
#   因为它们判断的是"这件事合不合理"，而不是"怎么存取数据"：
#       还原   -> 只有【在回收站里】的才能还原
#       彻底删 -> 只有【在回收站里】的才能彻底删（防误删的第一道闸门）
#       清空   -> 必须显式确认（数据安全铁律二）
#   这些规则将来可能变（比如加个"回收站保留 30 天"），
#   放在这一层，改的时候不用碰数据库代码。
# ===========================================================================


def list_trash() -> dict:
    """回收站列表，附带总数（页面上的角标用）。"""
    tasks = task_repo.list_deleted()
    return {
        "items": [task.to_dict() for task in tasks],
        "count": len(tasks),
    }


def restore_task(task_id: int):
    """
    把一条任务从回收站还原回正常列表。返回还原后的对象。

    【为什么失败要抛 NotFoundError 而不是悄悄返回？】
        如果还原一个不存在（或没删过）的 id 却返回成功，
        用户会以为"捞回来了"，回列表一看却没有 —— 这种"假成功"最难排查。
        宁可明确报错。
    """
    if not task_repo.restore(task_id):
        raise NotFoundError(
            "回收站里找不到 id 为 %s 的待办（可能它并没有被删除）" % task_id
        )
    return task_repo.get(task_id)


def purge_task(task_id: int) -> bool:
    """
    【彻底删除】一条任务，不可恢复。

    只有【已经在回收站里】的任务才能被彻底删除。
    这条规则挡住了最常见的一种误操作：把 purge 当成普通删除来调用。
    """
    if not task_repo.purge(task_id):
        raise NotFoundError(
            "回收站里找不到 id 为 %s 的待办（只有已删除的任务才能被彻底删除）" % task_id
        )
    return True


def empty_trash(confirm: bool = False) -> int:
    """
    清空回收站，返回真正删掉的条数。

    【为什么这里还要再判一次 confirm？】
        仓库层的 purge_all() 已经有一道 confirm 闸门了，这里又加一道，
        看起来是重复。但这正是"纵深防御"：
        万一以后有人绕过路由直接调 service，这一层还能拦住。
        危险操作多一道闸门，成本几乎为零，收益是"少一次事故"。
    """
    if not confirm:
        raise BusinessError(
            "清空回收站会永久删除里面全部任务且无法恢复，请确认后再操作"
        )
    return task_repo.purge_all(confirm=True)
