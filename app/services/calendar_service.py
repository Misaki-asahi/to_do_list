# -*- coding: utf-8 -*-
r"""
calendar_service.py -- 日历视图的数据加工（模块 M6）。

【这个模块负责什么】
    把一个月的任务，加工成"日历网格"这个可以直接渲染的结构。

【为什么不把日期计算放到前端？】
    "这个月 1 号是星期几""要不要补格子""跨年怎么办""闰年二月几天"
    这些边界情况很容易写错。Python 的 datetime / calendar 模块处理这些是强项，
    放在后端算好，前端就只需要 for 循环画格子 —— 出错的概率小得多。

    （决策 2A：本项目只做日历视图，不做 .ics 导入导出，所以不需要额外的日历服务层。）

【第三阶段重构说明】
    原来 month_view() 一个函数写了 86 行，从"解析参数"一路干到"拼返回字典"，
    中间还夹着四段带编号注释的步骤。读的时候必须从头跟到尾，
    想单独看"网格是怎么排的"都做不到。

    现在拆成了几个各干一件事的小函数：

        parse_year_month()      参数容错
        month_bounds()          本月第一天 / 最后一天
        grid_bounds()           补齐成完整的周
        prev_next_month()       上个月 / 下个月（跨年）
        group_tasks_by_date()   按日期分组
        build_weeks()           逐格生成网格
        day_range()             某一天的起止时刻

    month_view() 只剩下"按顺序调用它们"的编排逻辑，一眼能看完。

    ⚠️ 这些函数是【靠测试保护】的：tests/test_api_calendar.py 里
       对网格边界、闰年、跨年都有断言，所以这次拆分才敢动手。
"""

import calendar as pycalendar
from datetime import date, timedelta

from app.repositories import task_repo

#: 星期表头（周一开头，符合中文习惯）
WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]


# ===========================================================================
# 一、参数处理
# ===========================================================================

def parse_year_month(year_raw, month_raw):
    r"""
    把网址参数解析成合法的"年、月"，任何异常都退回"今天所在的月份"。

    【为什么需要这个函数？（BUG-009 的修复）】
        最初路由里写的是 year: int —— 想着"FastAPI 会帮我转成整数，转不了就报错"。
        但实测发现：?year=abc 会直接返回 422，
        【服务层里那些"退回今天"的兜底代码根本没机会执行】。

        原因：参数类型注解是在【框架层】校验的，比服务层早得多。
        所以想让"容错"真正生效，就必须把参数以【字符串】接收进来，自己解析。

    教训：**校验发生的位置，决定了你的容错代码有没有机会运行。**
    """
    today = date.today()

    try:
        year = int(str(year_raw).strip())
    except (TypeError, ValueError):
        year = today.year

    try:
        month = int(str(month_raw).strip())
    except (TypeError, ValueError):
        month = today.month

    if not (1970 <= year <= 2999):
        year = today.year
    if not (1 <= month <= 12):
        month = today.month

    return year, month


# ===========================================================================
# 二、日期计算（纯函数，不碰数据库，最容易单独测试）
# ===========================================================================

def month_bounds(year: int, month: int):
    """返回本月的 (第一天, 最后一天)。"""
    days_in_month = pycalendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, days_in_month)


def grid_bounds(first_day: date, last_day: date):
    r"""
    把月份补齐成完整的周，返回 (网格开始日, 网格结束日)。

    Python 的 weekday()：**周一 = 0，周日 = 6**（注意和 JavaScript 相反！）

        往左补：1 号若是周三（weekday=2）-> 往前退 2 天到周一
        往右补：最后一天若是周五（weekday=4）-> 往后加 2 天到周日

    这样每一行都正好是"周一到周日"七天，前端直接 for 循环画就行。
    """
    grid_start = first_day - timedelta(days=first_day.weekday())
    grid_end = last_day + timedelta(days=(6 - last_day.weekday()))
    return grid_start, grid_end


def prev_next_month(year: int, month: int):
    """返回 (上个月, 下个月)，会自动处理跨年。"""
    prev = (year - 1, 12) if month == 1 else (year, month - 1)
    nxt = (year + 1, 1) if month == 12 else (year, month + 1)
    return prev, nxt


def day_range(day: date):
    r"""
    某一天的起止时刻，例如 ("2026-09-20 00:00", "2026-09-20 23:59")。

    因为时间格式是 "YYYY-MM-DD HH:MM"，做区间查询时两端都得补上时分，
    否则 "2026-09-20" 这个字符串比 "2026-09-20 00:00" 小，当天的任务会被漏掉。
    """
    text = day.isoformat()
    return text + " 00:00", text + " 23:59"


# ===========================================================================
# 三、数据加工
# ===========================================================================

def group_tasks_by_date(tasks) -> dict:
    """
    把任务按"截止日期的日期部分"分组。

        [任务A(2026-09-20 08:00), 任务B(2026-09-20 15:00), 任务C(2026-09-22 09:00)]
            -> {"2026-09-20": [A, B], "2026-09-22": [C]}

    取前 10 位就是日期部分 —— 这正是决策 3B（固定宽度时间格式）带来的便利。
    """
    by_date = {}
    for task in tasks:
        key = (task.due_at or "")[:10]
        by_date.setdefault(key, []).append(task.to_dict())
    return by_date


def build_weeks(grid_start: date, grid_end: date, year: int, month: int,
                today: date, by_date: dict) -> list:
    """
    逐格生成日历网格：每周一行，每行 7 格。

    每一格包含前端渲染需要的全部信息：
        date / day / month      日期本身
        in_month                是不是本月（上月的尾巴和下月的开头要淡化显示）
        is_today / is_past      今天 / 已经过去（前端据此上色）
        tasks                   那天的任务列表
    """
    weeks = []
    cursor = grid_start

    while cursor <= grid_end:
        week = []
        for _ in range(7):
            key = cursor.isoformat()
            week.append({
                "date": key,
                "day": cursor.day,
                "month": cursor.month,
                "in_month": (cursor.month == month and cursor.year == year),
                "is_today": (cursor == today),
                "is_past": (cursor < today),
                "tasks": by_date.get(key, []),
            })
            cursor += timedelta(days=1)
        weeks.append(week)

    return weeks


# ===========================================================================
# 四、对外接口
# ===========================================================================

def month_view(year_raw=None, month_raw=None, user_id: int = 1) -> dict:
    """
    生成某个月的日历网格。

    返回结构（可以直接被前端渲染）：
        {
          "year": 2026, "month": 9, "title": "2026 年 9 月",
          "weekday_names": ["一","二","三","四","五","六","日"],
          "weeks": [
             [ {date, day, in_month, is_today, is_past, tasks:[...]}, x7 ],
             ... 最多 6 行 ...
          ],
          "undated": [ ...没有日期的任务... ],
          "month_task_count": 12
        }
    """
    # 1) 参数容错
    year, month = parse_year_month(year_raw, month_raw)
    today = date.today()

    # 2) 这个月的范围和"补齐成整周"后的网格范围
    first_day, last_day = month_bounds(year, month)
    grid_start, grid_end = grid_bounds(first_day, last_day)

    # 3) 查询范围内的任务并按日期分组
    tasks = task_repo.list_between(
        grid_start.isoformat() + " 00:00",
        grid_end.isoformat() + " 23:59",
        user_id,
    )
    by_date = group_tasks_by_date(tasks)

    # 4) 上个月 / 下个月（给导航按钮用）
    prev, nxt = prev_next_month(year, month)

    return {
        "year": year,
        "month": month,
        "title": "%d 年 %d 月" % (year, month),
        "today": today.isoformat(),
        "grid_start": grid_start.isoformat(),
        "grid_end": grid_end.isoformat(),
        "prev": {"year": prev[0], "month": prev[1]},
        "next": {"year": nxt[0], "month": nxt[1]},
        "weekday_names": WEEKDAY_NAMES,
        "weeks": build_weeks(grid_start, grid_end, year, month, today, by_date),
        "undated": [t.to_dict() for t in task_repo.list_undated(user_id)],
        "month_task_count": _count_in_month(tasks, year, month),
    }


def _count_in_month(tasks, year: int, month: int) -> int:
    """数一数有多少任务落在指定的年月里（网格里可能包含上月/下月的任务）。"""
    prefix = "%04d-%02d" % (year, month)
    return sum(1 for t in tasks if (t.due_at or "")[:7] == prefix)


def day_detail(day_text: str, user_id: int = 1) -> dict:
    """
    某一天的详细内容（点日历某一天时用）。

    这里做了一次"输入清洗"：day_text 来自网址参数，可能是任何字符串，
    所以用 date.fromisoformat 试一下，不合法就退回今天。
    """
    try:
        parsed = date.fromisoformat(str(day_text))
    except (ValueError, TypeError):
        parsed = date.today()

    today = date.today()
    tasks = task_repo.list_between(*day_range(parsed), user_id)

    return {
        "date": parsed.isoformat(),
        "is_past": parsed < today,
        "is_today": parsed == today,
        "tasks": [t.to_dict() for t in tasks],
        # 前端"在这天新建任务"时用的默认截止时间：当天结束前
        "default_due_at": parsed.isoformat() + " 23:59",
    }
