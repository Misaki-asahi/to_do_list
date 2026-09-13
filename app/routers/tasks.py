# -*- coding: utf-8 -*-
"""
tasks.py -- 待办相关的 API 路由（模块 M9）。

【这一层的定位】
    它是"前台接待"：收下请求 -> 交给服务层 -> 把结果包装成响应。
    【绝对不写业务规则】，也不直接碰数据库。

【为什么要这样分工？】
    因为"网址长什么样"和"业务规则是什么"是完全独立的两件事。
    以后你想把 /api/tasks 改成 /v2/todos，只需要改这个文件的一行；
    想改业务规则，只需要改 services。两者互不干扰。
"""

from fastapi import APIRouter, Query

from app.core import errors as app_errors
from app.schemas import MessageOut, TaskCreate, TaskOut, TaskStats, TaskUpdate, TrashOut
from app.services import task_service

# prefix 表示这个文件里所有接口都以 /api/tasks 开头
# tags 只影响自动生成的接口文档 /docs 里的分组显示
router = APIRouter(prefix="/api/tasks", tags=["待办"])


@router.post(
    "",
    response_model=TaskOut,
    status_code=201,
    summary="新增待办",
)
def create_task(payload: TaskCreate):
    """
    新增一条待办。

    流程：
        1. FastAPI 先用 TaskCreate 校验请求体（格式不对直接返回 422）
        2. 交给 service 层做业务判断（不合规返回 400）
        3. 返回创建好的完整对象（含数据库生成的 id 和时间）

    status_code=201 是 HTTP 规范里"资源创建成功"的标准状态码。
    """
    # 【重构说明】这里以前包了一层 try/except 把 BusinessError 转成 HTTPException。
    # 现在异常直接往上冒，由 app/main.py 的全局处理器统一翻译成 400。
    # 好处：路由函数只剩"调用服务层"这一件事，一眼看得懂。
    return task_service.create_task(payload).to_dict()


@router.get(
    "",
    response_model=list[TaskOut],
    summary="查询待办列表",
)
def list_tasks(
    status: str | None = Query(None, description="筛选状态：todo 未完成 / done 已完成，不传表示全部"),
    keyword: str | None = Query(None, description="在标题和描述里做模糊搜索"),
):
    """查询待办列表，支持按状态筛选和关键字搜索。"""
    tasks = task_service.list_tasks(status=status, keyword=keyword)
    return [task.to_dict() for task in tasks]


# ===========================================================================
# ★★★ 路由顺序非常重要 ★★★
#
#   FastAPI 是按【定义顺序】自上而下匹配路由的。
#   所以【静态路径】必须写在【动态路径】前面。
#
#   反例：如果 /{task_id} 写在 /stats 前面，那么访问 /api/tasks/stats 时，
#          FastAPI 会先把它匹配成 task_id="stats"，然后尝试转成整数，
#          失败后直接返回 422：
#              {"loc":["path","task_id"],"msg":"unable to parse string as an integer",
#               "input":"stats"}
#
#   本项目真的踩过这个坑（见 BUG-006），所以这里格外标注。
#   规则：本文件里所有 "固定字符串" 的路由，都要放在 /{task_id} 之前。
# ===========================================================================


@router.get(
    "/stats",
    response_model=TaskStats,
    summary="统计各状态数量",
)
def get_stats():
    """返回 总数 / 未完成 / 已完成，用于列表页顶部的统计显示。"""
    return task_service.get_stats()


# ---------------------------------------------------------------------------
# 回收站
#
# ⚠️ /trash 是【固定字符串】路由，所以它必须写在这里 ——
#    也就是必须排在下面的 /{task_id} 之前。
#    如果写反了，访问 /api/tasks/trash 会被当成 task_id="trash"，
#    转整数失败后直接返回 422（这个坑本项目真的踩过，见 BUG-006）。
# ---------------------------------------------------------------------------


@router.get(
    "/trash",
    response_model=TrashOut,
    summary="回收站列表",
)
def list_trash():
    """
    查看回收站里有哪些任务（按删除时间倒序，最近删的在最前）。

    这是【只读】操作，不会改动任何数据。
    """
    return task_service.list_trash()


@router.delete(
    "/trash",
    response_model=MessageOut,
    summary="清空回收站（不可恢复）",
)
def empty_trash(
    confirm: bool = Query(
        False,
        description="必须传 true 才会真正执行。少传或传 false 会返回 400，这是一道防手滑闸门",
    ),
):
    """
    永久删除回收站里的【全部】任务。

    【为什么要求传 confirm=true？】
        因为这是本项目唯一的"不可恢复"的批量操作。
        多打这 15 个字符，换来的是"绝不会因为点错一个按钮就丢掉一批数据"。
        （数据安全铁律二，由来见 BUG-011）
    """
    count = task_service.empty_trash(confirm=confirm)
    return MessageOut(ok=True, message="已永久删除 %d 条，无法恢复" % count)


@router.get(
    "/{task_id}",
    response_model=TaskOut,
    summary="按 id 查询单条待办",
)
def get_task(task_id: int):
    """查询单条待办。

    注意：这个动态路由【必须】写在 /stats 这类静态路由的后面，
    否则 /api/tasks/stats 会被当成 task_id="stats" 去匹配。
    """
    # 找不到时服务层会抛 NotFoundError，全局处理器会翻译成 404
    return task_service.get_task(task_id).to_dict()


@router.patch(
    "/{task_id}",
    response_model=TaskOut,
    summary="修改待办（只传要改的字段）",
)
def update_task(task_id: int, payload: TaskUpdate):
    """
    修改一条待办。

    用的是 PATCH 而不是 PUT，区别很重要：
        PUT   = "用我给你的这份完整数据替换掉原来那条"（没传的字段会被清空）
        PATCH = "只改我提到的这几个字段"（没传的字段保持原样）

    对"改个标题"这种操作，PATCH 才是正确的语义。
    """
    # ★ 核心的一行：exclude_unset=True 只取出用户【真正传过】的字段，
    #   从而区分"没传（不改）"和"传了 null（清空）"。
    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise app_errors.BusinessError("没有提供任何要修改的字段")

    return task_service.update_task(task_id, changes).to_dict()


@router.post(
    "/{task_id}/toggle",
    response_model=TaskOut,
    summary="切换完成 / 未完成",
)
def toggle_task(task_id: int):
    """点一下复选框就调用它，不需要前端判断当前是什么状态。"""
    return task_service.toggle_status(task_id).to_dict()


@router.delete(
    "/{task_id}",
    response_model=MessageOut,
    summary="删除待办",
)
def delete_task(task_id: int):
    """
    删除待办 —— 注意这是【移到回收站】，不是真的抹掉。

    想彻底删掉，要先删除、再调 /{task_id}/purge，
    或者在回收站页点「清空回收站」。
    """
    # 不存在时服务层会抛 NotFoundError -> 404
    task_service.delete_task(task_id)
    return MessageOut(ok=True, message="已移到回收站")


@router.post(
    "/{task_id}/restore",
    response_model=TaskOut,
    summary="从回收站还原",
)
def restore_task(task_id: int):
    """把一条任务从回收站捞回来，返回还原后的完整对象。

    用 POST 而不是 PUT/PATCH，是因为它不是一个"修改字段"的操作，
    而是"触发一个动作"—— 和 /toggle 是同一类。
    """
    return task_service.restore_task(task_id).to_dict()


@router.delete(
    "/{task_id}/purge",
    response_model=MessageOut,
    summary="彻底删除（不可恢复）",
)
def purge_task(task_id: int):
    """永久删除一条【已在回收站里】的任务。

    路径里用 purge 而不是 delete，就是为了和上面的"移到回收站"区分开 ——
    光看网址就知道哪个能后悔、哪个不能。
    """
    task_service.purge_task(task_id)
    return MessageOut(ok=True, message="已彻底删除，无法恢复")
