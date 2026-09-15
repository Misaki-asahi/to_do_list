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


# ---------------------------------------------------------------------------
# 宽容解析（v0.4.3 新增）
#
# 【为什么需要它？—— 用户的原话】
#     「悬浮窗新建待办，设置提醒日期时日期格式错误会导致整个待办消失，
#       修复这个bug并且放宽日期格式，如：中英文皆可，
#       以用户当前系统时间为准，允许输入"某某分钟后"提醒」
#
#   原来的做法是：只认死格式 YYYY-MM-DD HH:MM，别的全拒绝。
#   而用户会自然地写 "30分钟后"、"明天"、"9月16日"、"3pm"……
#   写错了就是整条待办都存不进去 —— 体验很差。
#
#   这个函数负责把"人话"翻译成规范格式；翻译不了就返回 None，
#   由调用方给出**看得懂的中文提示**（而不是一坨英文报错）。
# ---------------------------------------------------------------------------

import re

# 中文数字（"三天后"里的"三"）
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 英文月份缩写 / 全称
_MONTH_EN = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEKDAY_EN = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}

# 相对单位：元组里**长的写在前面**，这样"分钟"不会被"分"抢先匹配掉
_UNITS = (
    ("week", ("星期", "礼拜", "weeks", "week", "周", "w")),
    ("day", ("days", "day", "天", "d")),
    ("hour", ("hours", "hour", "hrs", "hr", "个小时", "小时", "h")),
    ("minute", ("minutes", "minute", "mins", "min", "分钟", "分", "m")),
)

# "今天/明天"这类词 -> 相对今天几天
_DAY_WORDS = {
    "今天": 0, "今日": 0, "today": 0,
    "明天": 1, "明日": 1, "tomorrow": 1, "tmr": 1,
    "后天": 2, "後天": 2,
    "大后天": 3,
    "昨天": -1, "昨日": -1, "yesterday": -1,
}
# "今晚"这种带"晚上"含义的词 -> (相对天数, 默认时刻)
_EVENING_WORDS = {"今晚": (0, (20, 0)), "明晚": (1, (20, 0)), "tonight": (0, (20, 0))}

# 上午/下午这类前缀怎么影响小时
_PERIODS = {"凌晨": 0, "清晨": 0, "早晨": 0, "早上": 0, "上午": 0,
            "中午": 12, "下午": 12, "傍晚": 12, "晚上": 12, "夜里": 12}

_CLOCK_FUZZY = re.compile(
    r"^(?P<period>凌晨|清晨|早晨|早上|上午|中午|下午|傍晚|晚上|夜里|晚)?\s*"
    r"(?P<hour>[0-9零〇一二两三四五六七八九十]{1,3})\s*[点时:：]\s*"
    r"(?P<minute>半|[0-9零〇一二两三四五六七八九十]{1,3}\s*分?)?$"
)
_CLOCK_COLON = re.compile(r"^(\d{1,2})\s*[:：]\s*(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)?$", re.I)
_CLOCK_EN = re.compile(r"^(\d{1,2})\s*(?:[:：.]\s*(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)$", re.I)
_DAY_ABS = re.compile(r"^(?:(\d{4})\s*[-/.]\s*)?(\d{1,2})\s*[-/.]\s*(\d{1,2})$")
_DAY_EN_MD = re.compile(r"^(?:([a-z]{3,9})\.?\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?)$", re.I)
_DAY_EN_DM = re.compile(r"^(?:(\d{1,2})\s+([a-z]{3,9}))(?:\s*,?\s*(\d{4}))?$", re.I)
_WEEK_CN = re.compile(
    r"^(下下|下个|下|本|这|next|this)?\s*(?:周|星期|礼拜)(?:\s*([一二三四五六日天]))?$")
_WEEK_EN = re.compile(r"^(next|this)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)$", re.I)


def _to_int(token: str):
    """把 '3' / '三' / '十一' 这种写法转成整数；转不了返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):                      # 十一 ~ 十九
        tail = _CN_DIGITS.get(token[1:], None)
        return None if tail is None else 10 + tail
    if token.endswith("十"):                        # 二十 / 三十
        head = _CN_DIGITS.get(token[:-1], None)
        return None if head is None else head * 10
    if "十" in token:                               # 二十三
        head, _, tail = token.partition("十")
        h, t = _CN_DIGITS.get(head), _CN_DIGITS.get(tail)
        if h is None or t is None:
            return None
        return h * 10 + t
    if len(token) == 1:
        return _CN_DIGITS.get(token)
    return None


def _parse_clock(text: str):
    """解析"时刻"部分，返回 (小时, 分钟)；不认识返回 None。"""
    t = (text or "").strip()
    if not t:
        return None

    m = _CLOCK_COLON.match(t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        ap = (m.group(3) or "").lower().replace(".", "")
        if ap.startswith("p") and hour < 12:
            hour += 12
        if ap.startswith("a") and hour == 12:
            hour = 0
        return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None

    m = _CLOCK_EN.match(t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3).lower().startswith("p") and hour < 12:
            hour += 12
        if m.group(3).lower().startswith("a") and hour == 12:
            hour = 0
        return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None

    m = _CLOCK_FUZZY.match(t)
    if m:
        hour = _to_int(m.group("hour"))
        if hour is None:
            return None
        raw_minute = (m.group("minute") or "").strip()
        if raw_minute == "半":
            minute = 30
        elif raw_minute:
            minute = _to_int(raw_minute.replace("分", "").strip())
            if minute is None:
                return None
        else:
            minute = 0
        period = m.group("period")
        if period in _PERIODS:
            # ★★ 【v0.4.4 修】这里的判断条件以前写错了。
            #    _PERIODS 的值就是"这个时段属于上午(0)还是下午(12)"，
            #    但老代码根本没看这个值，一律"hour < 12 就 +12" ——
            #    于是"上午9点"被算成 21:00、"凌晨2点"被算成 14:00，
            #    整整差了 12 小时（而且不报错，提醒会在错误的时刻响）。
            if _PERIODS[period] == 12:          # 中午/下午/傍晚/晚上/夜里
                if hour < 12:
                    hour += 12                  # 下午3点 -> 15 点
            elif hour == 12:                    # 凌晨/清晨/早晨/早上/上午
                hour = 0                        # 上午12点 -> 00:00
        return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None

    return None


def _parse_relative(text: str, base: datetime):
    """解析"30分钟后""2 hours later""in 3 days"这类相对时间；不认识返回 None。"""
    t = (text or "").strip().lower()
    if not t:
        return None
    # 去掉句尾的"后 / 之后 / 以后 / later / after"，看看剩下什么
    core = re.sub(r"(之后|以后|后|later|after|from now)$", "", t).strip()
    core = re.sub(r"^in\s+", "", core).strip()
    if not core:
        return None

    m = re.match(r"^(?P<n>\d+(?:\.\d+)?|半|[零〇一二两三四五六七八九十]+)\s*(?P<unit>[a-z\u4e00-\u9fa5]+)\s*$",
                 core)
    if not m:
        return None
    unit_name = None
    for name, aliases in _UNITS:
        if m.group("unit") in aliases:
            unit_name = name
            break
    if unit_name is None:
        return None

    raw_n = m.group("n")
    if raw_n == "半":
        amount = 0.5
    elif raw_n.replace(".", "", 1).isdigit():
        amount = float(raw_n)
    else:
        got = _to_int(raw_n)
        if got is None:
            return None
        amount = float(got)

    if unit_name == "week":
        delta = timedelta(weeks=amount)
    elif unit_name == "day":
        delta = timedelta(days=amount)
    elif unit_name == "hour":
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(minutes=amount)
    return base + delta


def _parse_day(text: str, base: datetime):
    r"""
    解析"日期"部分，返回 `(date, keep_clock)`；不认识返回 `(None, False)`。

    ★`keep_clock` 是"没写时刻时要不要沿用现在的时刻"，这条规则很重要：

        说的是"哪一天"（今天 / 明天 / 下周三 / tomorrow）  -> 沿用现在的时刻
        说的是"某个具体日期"（9-16 / Sep 16 / 2026年9月16日）-> 用 00:00

    为什么这样分？
        "明天"是**相对**的说法，隐含"和现在差不多的时候"；
        而"9-16"是**绝对**的日期，对截止时间来说就是"那天的开始"。
        两种情况都按对方的方式处理，都会让用户觉得"它猜错了"。
    """
    t = (text or "").strip()
    if not t:
        return (None, False)
    low = t.lower()

    if low in _DAY_WORDS:
        return (base.date() + timedelta(days=_DAY_WORDS[low]), True)

    m = _WEEK_CN.match(t)
    if m:
        prefix = m.group(1) or ""
        weekday_token = m.group(2)
        if weekday_token is None:
            # "下周" / "这一周" 这种没写星期几的：按"整周"往后推
            weeks = 1 if prefix in ("下", "下个", "next") else (2 if prefix == "下下" else 0)
            return (base.date() + timedelta(weeks=weeks), True)
        target = _WEEKDAY_CN[weekday_token]
        delta = (target - base.weekday()) % 7
        if prefix in ("下", "下个", "next"):
            delta = delta or 7
        elif prefix == "下下":
            delta = (delta or 7) + 7
        return (base.date() + timedelta(days=delta), True)

    m = _WEEK_EN.match(low)
    if m:
        target = _WEEKDAY_EN[m.group(2)]
        delta = (target - base.weekday()) % 7
        if m.group(1) == "next":
            delta = delta or 7
        return (base.date() + timedelta(days=delta), True)

    m = _DAY_ABS.match(t)
    if m:
        year = int(m.group(1)) if m.group(1) else base.year
        try:
            return (datetime(year, int(m.group(2)), int(m.group(3))).date(), False)
        except ValueError:
            return (None, False)

    m = _DAY_EN_MD.match(low)
    if m and m.group(1) in _MONTH_EN:
        year = int(m.group(3)) if m.group(3) else base.year
        try:
            return (datetime(year, _MONTH_EN[m.group(1)], int(m.group(2))).date(), False)
        except ValueError:
            return (None, False)

    m = _DAY_EN_DM.match(low)
    if m and m.group(2) in _MONTH_EN:
        year = int(m.group(3)) if m.group(3) else base.year
        try:
            return (datetime(year, _MONTH_EN[m.group(2)], int(m.group(1))).date(), False)
        except ValueError:
            return (None, False)

    return (None, False)


def _normalize_text(raw: str) -> str:
    """统一全角 / 中文标点，方便后面用一套正则处理。"""
    s = (raw or "").strip()
    for a, b in (("：", ":"), ("．", "."), ("／", "/"), ("－", "-"), ("—", "-"),
                 ("–", "-"), ("　", " "), ("，", ","), ("、", ","), ("。", ".")):
        s = s.replace(a, b)
    s = s.replace("年", "-").replace("月", "-").replace("日", "-").replace("号", "-")
    s = re.sub(r"[-/.]+\s+", " ", s)          # "9-16- 15:00" -> "9-16 15:00"
    s = re.sub(r"[-/.]+\s*$", "", s)          # 去掉尾部的分隔符
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_flexible(text, base: datetime = None):
    r"""
    宽容地把用户随手写的时间理解成规范字符串；实在看不懂返回 None。

    ★ 这是"时间解析的唯一出口"（见本文件开头的铁律），
      悬浮窗的输入框、接口的校验都用它，**不允许别处再写一套**。

    支持的形式（中英文皆可，分隔符中英文皆可）：

        绝对时间
            "2026-09-16 15:00"    原样
            "2026/9/16 15:00"     斜杠
            "2026.9.16 15:00"     点
            "2026年9月16日 15:00"  中文
            "9-16 15:00" / "9/16" / "9月16日"   省略年份 = 今年
            "Sep 16 15:00" / "16 Sep"          英文月份
            "15:00" / "15：00" / "3:00pm" / "3pm"
            "下午3点" / "晚上八点半" / "9点15分" / "中午12点"

        相对时间（★ 以【当前系统时间】为基准）
            "30分钟后" / "30 分钟之后" / "半小时后"
            "2小时后" / "1天后" / "3天之后" / "2周后"
            "in 30 minutes" / "30 minutes later" / "2 hours later" / "30min"

        日期词
            "今天" / "明天" / "后天" / "大后天"
            "today" / "tomorrow" / "tonight"
            "下周三" / "周一" / "next monday" / "monday"

    【没写时刻时怎么定？—— 这条规则要说清楚，否则很容易"猜错"】
        · 相对时间（"30分钟后""2天后"）    -> 保留"现在的时刻"
          （"2天后"= 两天后的这个时候，这是最符合直觉的理解）
        · 日期词（"明天""tomorrow"）        -> 同样保留"现在的时刻"
        · 明确的日期（"9-16""Sep 16"）      -> **00:00**
          （因为"9-16"对截止时间来说就是"那天的开始"）
        · "今晚/tonight"                    -> 当天 20:00
    """
    raw = (text or "").strip()
    if not raw:
        return None
    base = (base or datetime.now()).replace(second=0, microsecond=0)

    s = _normalize_text(raw)
    if not s:
        return None

    # ① 已经是规范格式
    dt = parse(s)
    if dt is not None:
        return format_dt(dt)

    # ② 整串就是一个相对时间
    rel = _parse_relative(s, base)
    if rel is not None:
        return format_dt(rel)

    # ③ 拆成"时刻段 + 日期段"（顺序随意，"3pm tomorrow" 也认）
    #
    #   做法：先把"时刻"那一段挑出来，**剩下的拼回一个整体再去认日期**。
    #   为什么不逐段各认各的？因为日期本身可能由好几段组成：
    #   "Sep 16"、"16 Sep 2026"、"下周 三" —— 分开看每一段都不像日期，合起来才是。
    #   （第一版就是逐段认的，结果 "Sep 16 15:00" 被认成了"今天 15:00"。）
    tokens = [p for p in s.split(" ") if p]
    clock_part = None
    rest = []
    for token in tokens:
        if clock_part is None:
            got_clock = _parse_clock(token)
            if got_clock is not None:
                clock_part = got_clock
                continue
        rest.append(token)

    day_part = None
    keep_clock = False          # 没写时刻时是否沿用"现在的时刻"
    joined = " ".join(rest).strip()
    if joined:
        low = joined.lower()
        if low in _EVENING_WORDS:
            days, evening_clock = _EVENING_WORDS[low]
            day_part = base.date() + timedelta(days=days)
            clock_part = clock_part or evening_clock
        else:
            day_part, keep_clock = _parse_day(joined, base)
            if day_part is None:
                # ★★【认不出来就老实说认不出来，绝不退化成"只写了时刻"】
                #
                #   这一条是测试抓出来的真问题：
                #       "2026-2-30 10:00"（2 月没有 30 号）
                #   原本会走到下面的"只写时刻"分支，
                #   变成 **"明天 10:00"** —— 一个用户根本没写过的、看起来还很合理的时间。
                #   这种"静默换成另一个时间"比直接报错危险得多：
                #   用户不会发现，直到某天提醒没响。
                #
                #   规则：**用户写了日期但认不出来 -> 返回 None（交给上层报错）**，
                #   只有"确实一个日期都没写"才走"只写时刻"的分支。
                return None

    if day_part is None and clock_part is None:
        return None

    if day_part is None:                    # 只写了时刻 -> 今天；已经过了就顺延到明天
        candidate = datetime.combine(base.date(), datetime.min.time()).replace(
            hour=clock_part[0], minute=clock_part[1])
        if candidate <= base:
            candidate += timedelta(days=1)
        return format_dt(candidate)

    if clock_part is None:
        hour, minute = (base.hour, base.minute) if keep_clock else (0, 0)
    else:
        hour, minute = clock_part
    return format_dt(datetime.combine(day_part, datetime.min.time()).replace(
        hour=hour, minute=minute))


def describe_accepts() -> str:
    """给用户看的"可以怎么写"说明（报错时拼在提示里）。"""
    return ("可以写「30分钟后」「2小时后」「明天」「下周一」「9-16 15:00」"
            "「2026年9月16日 15:00」「3pm」这类写法")


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
