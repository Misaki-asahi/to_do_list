# -*- coding: utf-8 -*-
"""
floating.py -- 桌面悬浮窗的 API 路由（v0.3.0 新增）。

【这个文件里有两类接口，它们的"观众"不一样】

    一、给【设置页（浏览器）】用的：
        GET   /api/floating                 一次性拿到悬浮窗全部设置与状态
        PATCH /api/floating/config          改设置（滑块、色块、开关都在改它）
        POST  /api/floating/theme           一键套用配色
        POST  /api/floating/reset           恢复出厂设置
        POST  /api/floating/window/show     立即显示（把隐藏了的窗口叫回来）
        POST  /api/floating/window/restart  重启窗口
        POST  /api/floating/window/close    隐藏窗口

    二、给【悬浮窗进程】用的（这些要带令牌）：
        GET   /api/floating/data            窗口要显示的全部数据（分块后的）
        GET   /api/floating/config          只问设置（每 3 秒问一次）
        GET   /api/floating/remind-at       把"10 分钟后"换算成具体时间
        POST  /api/floating/tasks           在窗口里新建待办
        POST  /api/floating/tasks/{id}/toggle
        PATCH /api/floating/tasks/{id}
        POST  /api/floating/tasks/{id}/snooze

【为什么"改设置"的接口不带令牌，而"读写数据"的要带？】

    因为设置页是浏览器里的一个普通网页，没法携带只在服务端知道的秘密
    （除非再用一套登录体系，那是这个本地项目不需要的复杂度）。
    而窗口是服务端自己拉起来的子进程，令牌可以直接用环境变量传给它 ——
    所以对"能改数据"的那一组接口加上令牌这道闸门，成本为零、收益是真有。

    ⚠️ 说清楚边界：这个令牌【不是】完整的鉴权机制，
       服务本身只监听 127.0.0.1（只允许本机访问），
       它的作用是"防误触、防别的网页瞎调"，而不是"防黑客"。
       真要防黑客得靠"不对外暴露端口"这件事本身。
"""

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app import config
from app.core import errors as app_errors
from app.repositories import floating_repo
from app.schemas import FloatingConfigIn
from app.services import floating_service

router = APIRouter(prefix="/api/floating", tags=["悬浮窗"])


# ===========================================================================
# 令牌校验（只作用于"窗口专用"的那几个接口）
# ===========================================================================


def _friendly_errors(exc) -> str:
    r"""
    把 Pydantic 的校验错误整理成**一句中文**（v0.4.3）。

    【为什么要做这一步？】
        默认的错误长这样，直接丢给用户等于没提示：

            HTTP 422：提交的数据格式不正确：1 validation error for TaskCreate
            remind_at
              Value error, 时间格式必须是 YYYY-MM-DD HH:MM ...
              [type=value_error, input_value='30分钟后', input_type=str]
                For further information visit https://errors.pydantic.dev/...

        而用户只想知道"我哪里写得不对、该怎么改"。
        所以这里只取每条错误里的 msg（那部分才是我们自己写的中文提示），拼成一句人话。
    """
    try:
        items = exc.errors()
    except AttributeError:
        return "提交的数据格式不正确：%s" % exc
    messages = []
    for item in items:
        field = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        message = str(item.get("msg", "")).replace("Value error, ", "")
        messages.append(("%s：%s" % (field, message)) if field else message)
    return "；".join(messages) or "提交的数据格式不正确"


def _check_token(token: str | None):
    """
    校验悬浮窗令牌；不对就抛 403。

    【为什么用消息头而不是网址参数？】
        网址会被写进日志、浏览器历史、以及任何中间层的记录里。
        令牌属于"秘密"，放在 X-Float-Token 头里，默认不会被记录下来。
        （这也是所有正规 API 的做法：Bearer Token 走 Authorization 头。）

    【为什么找不到令牌时抛 403 而不是 500？】
        403 = "我知道你是谁，但你不许做这件事"，语义正好，
        前端（窗口）也能据此给出"令牌不对，请重启窗口"这种明确提示。
    """
    expected = floating_repo.get_token()
    if not expected:
        # 服务端还没有令牌（比如数据库刚被清空）—— 这时候放行，
        # 因为"拦一个连钥匙都还没配好的门"只会把用户挡在外面。
        return
    if not token or token != expected:
        raise app_errors.ForbiddenError(
            "悬浮窗令牌不正确：请到设置页点一次「重启窗口」")


# ===========================================================================
# 一、设置页用的接口
# ===========================================================================


@router.get("", summary="获取悬浮窗的全部设置与运行状态")
def get_floating():
    """
    设置页一打开就调用它，拿到：
        设置值 / 窗口是否在运行 / 默认值 / 可选项 / 取值范围 / 配色主题
    """
    return floating_service.status()


@router.patch("/config", summary="修改悬浮窗设置（只传要改的字段）")
def patch_config(payload: FloatingConfigIn):
    """
    改设置。

    ⚠️ 用的是 PATCH 语义：**只传你真正想改的那个字段**。
       exclude_unset=True 是关键 —— 它区分了
       "这个字段没出现在请求里（不改）"和"显式传了 false（要改成关闭）"。
       如果误用 PUT 的语义（整份替换），那么窗口拖动一次位置，
       就会把用户的字号、颜色、分块方式全都重置回默认值。

    （这个坑在列表页的"编辑"接口那里也讲过一遍，是同一类问题。）
    """
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise app_errors.BusinessError("没有提供任何要修改的字段")
    return floating_service.update_config(changes)


@router.post("/theme", summary="一键套用配色主题")
def apply_theme(name: str = Query(..., description="主题名，见 GET /api/floating 的 themes")):
    return {"ok": True, "message": "已套用配色：%s" % name,
            "config": floating_service.apply_theme(name)}


@router.post("/reset", summary="恢复默认设置")
def reset():
    """
    把所有设置恢复成出厂默认。

    【为什么这个操作不需要 confirm 参数？】
        因为它【只】重置"外观和位置"，不碰任何待办数据 ——
        "数据安全铁律二"管的是会丢数据的操作，这里不丢任何数据。
        不过它会把窗口关掉（位置回到默认），所以返回值里会说清楚。
    """
    return floating_service.reset_config()


@router.post("/window/show", summary="立即显示悬浮窗")
def show_window():
    """把隐藏了的窗口叫回来（也顺带把总开关打开）。"""
    return floating_service.show_now()


@router.post("/window/restart", summary="重启悬浮窗")
def restart_window():
    """重开一个窗口（界面卡住、或者想强制刷新时用）。"""
    return floating_service.restart_window()


@router.post("/window/close", summary="隐藏悬浮窗")
def close_window():
    """
    隐藏窗口（等价于点窗口右上角的 ✕）。

    【注意：这只是"隐藏"，不是"关闭功能"】
        设置里的"已开启"保持不变，下次开机自启它还会出现。
        想彻底关掉请在设置页关总开关。
    """
    floating_service.update_config({"visible": False})
    return {"ok": True, "message": "悬浮窗已隐藏（设置页里可以再叫出来）"}


# ===========================================================================
# 二、窗口进程用的接口（需要令牌）
# ===========================================================================


@router.get("/data", summary="悬浮窗要显示的全部数据")
def data(
    request: Request,
    force: bool = Query(False, description="true 表示忽略缓存，强制重新计算"),
    x_float_token: str | None = Header(None, alias="X-Float-Token"),
):
    """
    返回分好块的任务列表 + 统计数字 + 最近一条提醒。

    窗口每隔几秒调一次，所以这里做了一个 1 秒的缓存：
    用户在设置页点"刷新"和窗口定时刷新可能同时发生，
    没有缓存的话同一份数据会被算两遍（分组、装饰字段都不便宜）。
    """
    _check_token(x_float_token)
    return floating_service.window_payload(force=force)


@router.get("/config", summary="只读取悬浮窗设置（窗口轮询用）")
def read_config(x_float_token: str | None = Header(None, alias="X-Float-Token")):
    """窗口每隔几秒问一次"设置改了没"，只返回设置，不返回任务（省流量、也更快）。"""
    _check_token(x_float_token)
    return {"config": floating_service.get_config(),
            "version": floating_repo.get_data_version()}


@router.get("/remind-at", summary="把提醒快捷选项换算成具体时间")
def remind_at(
    choice: str = Query(..., description="10/30/60（分钟）或 today / tomorrow"),
    x_float_token: str | None = Header(None, alias="X-Float-Token"),
):
    """
    【为什么要绕一圈让服务端算？】
        因为新建待办时接口会校验"提醒时间不能早于当前时间"。
        如果客户端自己算，两边时钟只要差几秒，就会出现
        "我选了 10 分钟后，却提示时间早于当前"这种自相矛盾的错误。
        统一由服务端的钟来算，这种错误从结构上就不可能发生。
    """
    _check_token(x_float_token)
    return {"choice": choice, "remind_at": floating_service.compute_remind_at(choice)}


@router.post("/tasks", summary="在悬浮窗里新建待办")
def create_task(payload: dict, x_float_token: str | None = Header(None, alias="X-Float-Token")):
    """
    新建一条待办。

    【为什么不直接复用 POST /api/tasks？】
        因为那个接口的入参模型是针对"完整的表单"设计的，
        而窗口只需要"一句话 + 可选的优先级/分类/提醒"。
        不过 —— 注意下面这一行：**校验和落库完全复用同一个服务层函数**。
        接口可以有两个，业务规则只能有一份（否则两边的行为迟早会不一致）。
    """
    _check_token(x_float_token)
    from app.schemas import TaskCreate

    # 用 Pydantic 模型做一次严格校验，再把结果交给服务层 ——
    # 这样窗口传来的数据在格式上和网页传来的完全等价（同一套规则、同一套报错）。
    try:
        validated = TaskCreate(**payload)
    except Exception as exc:                    # noqa: BLE001
        raise app_errors.ValidationError(_friendly_errors(exc))

    return floating_service.quick_add(validated)


@router.get("/tasks/{task_id}", summary="读一条待办（编辑弹窗预填用）")
def read_task(task_id: int, x_float_token: str | None = Header(None, alias="X-Float-Token")):
    """
    打开编辑弹窗时先调它，保证"看到的就是最新的"。

    列表里的数据最多是 10 秒前的快照；直接用快照预填再保存，
    会把 10 秒内别人（或在网页里）做的修改覆盖掉。
    """
    _check_token(x_float_token)
    return floating_service.get_task(task_id)


@router.post("/tasks/{task_id}/toggle", summary="勾选 / 取消勾选")
def toggle_task(task_id: int, x_float_token: str | None = Header(None, alias="X-Float-Token")):
    _check_token(x_float_token)
    return floating_service.toggle(task_id)


@router.patch("/tasks/{task_id}", summary="在悬浮窗里编辑待办")
def update_task(task_id: int, payload: dict,
                x_float_token: str | None = Header(None, alias="X-Float-Token")):
    _check_token(x_float_token)
    from app.schemas import TaskUpdate

    try:
        validated = TaskUpdate(**payload)
    except Exception as exc:                    # noqa: BLE001
        raise app_errors.ValidationError(_friendly_errors(exc))

    changes = validated.model_dump(exclude_unset=True)
    if not changes:
        raise app_errors.BusinessError("没有提供任何要修改的字段")
    return floating_service.update_task(task_id, changes)


@router.post("/tasks/{task_id}/snooze", summary="稍后提醒")
def snooze_task(task_id: int,
                minutes: int = Query(None, description="推迟多少分钟，默认取配置值"),
                x_float_token: str | None = Header(None, alias="X-Float-Token")):
    _check_token(x_float_token)
    return floating_service.snooze(task_id, minutes)
