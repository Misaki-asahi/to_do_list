# -*- coding: utf-8 -*-
"""
timeutil.py -- 时间工具。

【本项目的铁律】
    全项目只允许在这一个文件里做"时间 <-> 字符串"的转换，别处一律调用本模块的函数。

为什么要有这条规矩？（决策 3B：纯本地时间字符串）
    如果每个文件都自己写 datetime.strptime(...)，一旦格式改了，
    你要改十几个地方，改漏一个就出现"时间显示成 1970 年"这种诡异 Bug。
    集中到一处，将来要改成带时区的方案，也只需要动这个文件。

存储格式统一为："YYYY-MM-DD HH:MM"，例如 2026-02-20 08:30
"""

from datetime import datetime, timedelta

from app import config


def now() -> datetime:
    """当前时间（秒和微秒都归零，因为本项目只精确到"分"）。"""
    return datetime.now().replace(second=0, microsecond=0)


def now_str() -> str:
    """当前时间的字符串形式，用于写入数据库。"""
    return format_dt(datetime.now())


def format_dt(dt: datetime) -> str:
    """把 datetime 对象转成数据库里存的那种字符串。"""
    return dt.strftime(config.DATETIME_FORMAT)


def parse(text):
    """
    把字符串解析成 datetime 对象。

    解析失败时【不抛异常】，而是返回 None。
    为什么这样设计？
        因为时间字段来自用户输入，格式千奇百怪。
        让"解析失败"变成一个可以判断的普通返回值（None），
        比让它变成异常中断整个程序要安全得多。

    ★★ 关键：必须做"回环校验"，不能只依赖 strptime ★★

        这是单元测试抓出来的一个真 Bug（见 BUG-018）：

            datetime.strptime("2026-9-13 8:30", "%Y-%m-%d %H:%M")   # 竟然能成功！

        Python 的 strptime 对【补零】是宽容的，月份写 "9" 而不是 "09" 也照收。

        而本项目整个设计（决策 3B）都建立在一个前提上：
            **字符串的字典序 == 时间的先后顺序**

        一旦数据库里混进 "2026-9-13 8:30" 这种没补零的值，这个前提就崩了：

            排序结果：['2026-09-13 08:30', '2026-09-13 09:00', '2026-9-13 08:00']
                                                        ↑ 最早的 08:00 被排到了最后

        后果是"日历按日期查询漏数据、提醒扫描顺序错乱"，而且**很难查**。

        解决办法：把解析出来的时间【按标准格式重新格式化一遍】，
        和原始输入逐字符比较。不一致，就说明它不是标准格式，直接拒绝。

        这个技巧叫"回环校验"（round-trip validation），
        凡是"必须保证输入是唯一规范形式"的地方都可以用。
    """
    if not text:
        return None

    raw = str(text).strip()
    try:
        dt = datetime.strptime(raw, config.DATETIME_FORMAT)
    except (ValueError, TypeError):
        return None

    # 回环校验：重新格式化后必须和输入完全一样
    if dt.strftime(config.DATETIME_FORMAT) != raw:
        return None

    return dt


def is_valid(text) -> bool:
    """判断一个字符串是不是合法的时间格式。"""
    return parse(text) is not None


def is_due(text, base: datetime = None) -> bool:
    """判断某个时间是否已经到达（提醒扫描要用）。"""
    dt = parse(text)
    if dt is None:
        return False
    return dt <= (base or datetime.now())


def add_minutes(text: str, minutes: int) -> str:
    """在某个时间上加若干分钟（"稍后提醒"功能要用）。"""
    dt = parse(text)
    if dt is None:
        dt = now()
    return format_dt(dt + timedelta(minutes=minutes))


def date_part(text: str):
    """
    从 "2026-02-20 08:30" 取出日期部分 "2026-02-20"。

    这里体现了决策 3B 的一个隐藏好处：
        因为格式是"年-月-日 时:分"，从大到小排列，
        所以"取前 10 个字符"就是日期，字符串排序 = 时间排序。
        如果当初存的是时间戳数字，就没这么方便了。
    """
    if not text or len(text) < 10:
        return None
    return text[:10]


def today_prefix() -> str:
    """今天的日期字符串 "YYYY-MM-DD"，用于查询"今天到期的任务"。"""
    return datetime.now().strftime(config.DATE_FORMAT)


def humanize(text: str) -> str:
    """把时间变得更好读，例如 "今天 08:30" / "明天 09:00" / "2026-03-01 10:00"。"""
    dt = parse(text)
    if dt is None:
        return ""
    today = datetime.now().date()
    delta = (dt.date() - today).days
    if delta == 0:
        return "今天 " + dt.strftime("%H:%M")
    if delta == 1:
        return "明天 " + dt.strftime("%H:%M")
    if delta == -1:
        return "昨天 " + dt.strftime("%H:%M")
    if 1 < delta <= 7:
        return "%d 天后 " % delta + dt.strftime("%H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")
