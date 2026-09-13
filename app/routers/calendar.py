# -*- coding: utf-8 -*-
"""
calendar.py -- 日历视图的 API 路由（模块 M6）。

设计说明（决策 2A）：
    本项目只做"日历视图"（点某天建任务），不做 .ics 导入导出。
    所以这里不需要处理第三方日历格式，只有两个只读接口。
"""

from fastapi import APIRouter, Query

from app.services import calendar_service

router = APIRouter(prefix="/api/calendar", tags=["日历"])


@router.get(
    "/month",
    summary="某个月的日历网格",
)
def get_month(
    # ⚠️ 这里刻意接收【字符串】而不是 int。
    #    写成 int 的话，?year=abc 会在框架层就被拦下返回 422，
    #    服务层里的"退回当月"兜底代码根本没机会执行（见 BUG-009）。
    #    日历页是给人看的页面，网址被手改错就白屏，体验很差 ——
    #    所以宁可自己解析、自己容错。
    year: str = Query(None, description="年份，例如 2026；不传或非法表示今年"),
    month: str = Query(None, description="月份 1~12；不传或非法表示本月"),
):
    """
    返回可以直接渲染的日历网格（已经排好版：每周一行，每行 7 格）。

    参数不合法时（包括传了非数字）都会退回"今天所在的月份"，而不是报错。
    """
    return calendar_service.month_view(year, month)


@router.get(
    "/day",
    summary="某一天的详细内容",
)
def get_day(day: str = Query(None, description="日期，格式 YYYY-MM-DD")):
    """点日历某一天时调用，返回那天有哪些任务，以及新建任务时的默认截止时间。"""
    return calendar_service.day_detail(day or "")
