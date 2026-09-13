# -*- coding: utf-8 -*-
"""
reminders.py -- 提醒相关的 API 路由。

【架构变更说明（重要）】

    以前：前端轮询 /api/reminders/due，由接口负责扫描和标记。
    现在：**后端有一个后台线程负责扫描和发系统通知**，
          前端只需要轮询 /api/reminders/inbox，看看"有没有已经弹过的提醒要显示"。

    为什么要改？
        因为系统级通知必须由后端发（浏览器关掉也要能提醒）。
        既然后端已经在扫描了，前端再扫一遍就是重复劳动。

    具体的架构分析见 app/core/scheduler.py 顶部的说明。
"""

from fastapi import APIRouter

from app.core import scheduler
from app.schemas import MessageOut, TaskOut
from app.services import reminder_service

router = APIRouter(prefix="/api/reminders", tags=["提醒"])


@router.get(
    "/inbox",
    summary="取「已弹过但页面还没显示」的提醒",
)
def get_inbox():
    """
    前端每隔几秒调用一次。

    【和旧的 /due 有什么区别？】
        /due   会"取走并标记"，调一次就消费一次。
        /inbox 只是读取快照，**不会清空**；
               要等页面调用 /ack 明确确认才移除。

        这样更安全：页面刷新、请求失败、用户还没看清，提示都不会凭空消失。
    """
    return reminder_service.inbox()


@router.post(
    "/{task_id}/ack",
    response_model=MessageOut,
    summary="确认已处理某条提醒（从收件箱移除）",
)
def ack_reminder(task_id: int):
    ok = reminder_service.ack(task_id)
    return MessageOut(ok=ok, message="已处理" if ok else "这条提醒不在待处理列表里")


@router.post(
    "/{task_id}/snooze",
    response_model=TaskOut,
    summary="稍后提醒（默认推迟 10 分钟）",
)
def snooze_reminder(task_id: int, minutes: int = None):
    """把提醒时间往后推 N 分钟，并清空已提醒标记，让它到点后能再弹一次。"""
    # 不存在时服务层抛 NotFoundError，全局处理器翻译成 404
    return reminder_service.snooze(task_id, minutes).to_dict()


@router.get(
    "/stats",
    summary="提醒统计（设置页用）",
)
def reminder_stats():
    return reminder_service.stats()


@router.post(
    "/test",
    summary="发一条测试通知（设置页用）",
)
def test_notification():
    """让用户自己确认"系统通知到底能不能响"。"""
    result = reminder_service.test_notification()
    result["scheduler_running"] = scheduler.is_running()
    return result


@router.get(
    "/scheduler",
    summary="后台提醒线程的运行状态",
)
def scheduler_status():
    """
    排查"为什么没提醒"时先看这里：
        running     为 False  -> 后台线程没起来
        last_error  非空      -> 线程里出异常了
        last_fired  一直是 0  -> 可能真的没有到点的提醒
    """
    return {
        "running": scheduler.is_running(),
        "poll_seconds": scheduler.config.REMINDER_SCAN_SECONDS,
        "last_run": scheduler.last_run,
    }
