# -*- coding: utf-8 -*-
r"""
test_timeutil.py -- 时间工具的单元测试。

时间是最容易出 Bug 的东西（格式、边界、空值），
所以它是第一批必须被测到的模块。
"""

from datetime import datetime, timedelta

from app import config
from app.core import timeutil


class TestFormat:
    """格式转换。"""

    def test_now_str_format(self):
        """当前时间的格式必须是 YYYY-MM-DD HH:MM（长度 16）。"""
        text = timeutil.now_str()
        assert len(text) == 16
        assert text[4] == "-" and text[7] == "-" and text[10] == " "
        assert text[13] == ":"

    def test_now_str_is_parseable(self):
        """格式化出来的字符串必须能再解析回去（往返一致）。"""
        dt = timeutil.parse(timeutil.now_str())
        assert dt is not None
        assert timeutil.format_dt(dt) == timeutil.now_str()

    def test_format_dt(self):
        dt = datetime(2026, 9, 13, 8, 5)
        assert timeutil.format_dt(dt) == "2026-09-13 08:05"


class TestParse:
    """解析：正常、异常、空值三种情况都要覆盖。"""

    def test_valid(self):
        dt = timeutil.parse("2026-09-13 08:30")
        assert dt.year == 2026 and dt.month == 9 and dt.day == 13
        assert dt.hour == 8 and dt.minute == 30

    def test_none_returns_none(self):
        assert timeutil.parse(None) is None

    def test_empty_string_returns_none(self):
        assert timeutil.parse("") is None

    def test_bad_format_returns_none_no_exception(self):
        """格式不对要返回 None，【绝不能抛异常】。

        因为时间来自用户输入，抛异常会让整个接口 500。
        """
        for bad in ["2026/09/13", "2026-9-13 8:30", "昨天", "2026-13-45 99:99", "x"]:
            assert timeutil.parse(bad) is None, bad

    def test_whitespace_is_trimmed(self):
        assert timeutil.parse("  2026-09-13 08:30  ") is not None


class TestIsValid:
    def test_valid(self):
        assert timeutil.is_valid("2026-09-13 08:30") is True

    def test_invalid(self):
        assert timeutil.is_valid("2026-09-13") is False
        assert timeutil.is_valid(None) is False


class TestIsDue:
    def test_past_is_due(self):
        past = timeutil.format_dt(datetime.now() - timedelta(minutes=5))
        assert timeutil.is_due(past) is True

    def test_future_is_not_due(self):
        future = timeutil.format_dt(datetime.now() + timedelta(minutes=5))
        assert timeutil.is_due(future) is False

    def test_invalid_is_not_due(self):
        """格式不对时返回 False，而不是报错。"""
        assert timeutil.is_due("不是时间") is False


class TestAddMinutes:
    def test_normal(self):
        assert timeutil.add_minutes("2026-09-13 08:00", 30) == "2026-09-13 08:30"

    def test_cross_hour(self):
        assert timeutil.add_minutes("2026-09-13 08:50", 20) == "2026-09-13 09:10"

    def test_cross_day(self):
        assert timeutil.add_minutes("2026-09-13 23:50", 20) == "2026-09-14 00:10"

    def test_cross_month(self):
        assert timeutil.add_minutes("2026-09-30 23:50", 20) == "2026-10-01 00:10"

    def test_cross_year(self):
        assert timeutil.add_minutes("2026-12-31 23:50", 20) == "2027-01-01 00:10"

    def test_negative(self):
        assert timeutil.add_minutes("2026-09-13 08:00", -30) == "2026-09-13 07:30"

    def test_invalid_input_falls_back_to_now(self):
        """传了非法时间时，以"当前时间"为基准，而不是崩掉。"""
        result = timeutil.add_minutes("不是时间", 10)
        assert len(result) == 16


class TestDatePart:
    def test_normal(self):
        assert timeutil.date_part("2026-09-13 08:30") == "2026-09-13"

    def test_none(self):
        assert timeutil.date_part(None) is None

    def test_too_short(self):
        assert timeutil.date_part("2026") is None


class TestSortability:
    r"""
    【重点】验证决策 3B 的核心好处：
        时间字符串的字典序 == 时间先后顺序。

    如果这条不成立，日历按日期查询、提醒按时间扫描全都会出错。
    """

    def test_string_order_equals_time_order(self):
        times = ["2026-09-13 08:30", "2026-09-13 08:00",
                 "2026-12-01 00:00", "2027-01-01 00:00", "2026-10-01 09:00"]
        assert sorted(times) == sorted(times, key=timeutil.parse)

    def test_cross_year_sorting(self):
        assert "2026-12-31 23:59" < "2027-01-01 00:00"

    def test_zero_padding_matters(self):
        """不补零的格式会破坏排序 —— 这就是为什么必须补零。"""
        assert "2026-09-09 08:00" < "2026-09-13 08:00"


# ===========================================================================
# 宽容解析（v0.4.3 新增）
#
# 【为什么值得单独测这么一大块？】
#     因为它是"用户输入 -> 系统认识"的唯一入口。
#     用户写 "30分钟后"、"明天"、"9月16日"、"3pm" 都很自然，
#     而**解析错了不会报错，只会静默存成另一个时间** ——
#     这类错误最难发现，所以每种写法都要钉一条。
# ===========================================================================


class TestParseFlexible:
    r"""把"人话"翻译成规范时间（用户要求"放宽日期格式"）。

    所有用例都传 `base` 固定一个"现在"，这样测试**和运行时刻无关**
    （这条纪律来自 BUG-032 / BUG-045：测试不许"只在某个时段通过"）。
    """

    BASE = datetime(2026, 9, 15, 14, 30)

    def p(self, text):
        return timeutil.parse_flexible(text, self.BASE)

    # ---- 规范格式：原样接受 ----
    def test_canonical_untouched(self):
        assert self.p("2026-09-16 15:00") == "2026-09-16 15:00"

    # ---- 中英文 + 各种分隔符 ----
    def test_separators(self):
        for text in ("2026/09/16 15:00", "2026.09.16 15:00", "2026年9月16日 15:00",
                     "2026-9-16 15:00"):
            assert self.p(text) == "2026-09-16 15:00", text

    def test_month_day_without_year_uses_current_year(self):
        for text in ("9-16 15:00", "9/16 15:00", "9月16日 15:00", "9.16 15:00"):
            assert self.p(text) == "2026-09-16 15:00", text

    def test_english_month_names(self):
        assert self.p("Sep 16 15:00") == "2026-09-16 15:00"
        assert self.p("September 16") == "2026-09-16 00:00"
        assert self.p("16 Sep 2026 15:00") == "2026-09-16 15:00"

    # ---- 只写时刻 ----
    def test_time_only(self):
        assert self.p("15:00") == "2026-09-15 15:00"        # 今天还没到
        assert self.p("09:00") == "2026-09-16 09:00"        # 今天已经过了 -> 顺延明天

    def test_time_only_chinese_and_ampm(self):
        assert self.p("下午3点") == "2026-09-15 15:00"
        assert self.p("晚上八点半") == "2026-09-15 20:30"
        assert self.p("3pm") == "2026-09-15 15:00"
        assert self.p("3:30pm") == "2026-09-15 15:30"

    # ---- 相对时间（★ 以当前系统时间为基准）----
    def test_relative_minutes(self):
        assert self.p("30分钟后") == "2026-09-15 15:00"
        assert self.p("半小时后") == "2026-09-15 15:00"
        assert self.p("in 30 minutes") == "2026-09-15 15:00"
        assert self.p("30 minutes later") == "2026-09-15 15:00"

    def test_relative_hours_days_weeks(self):
        assert self.p("2小时后") == "2026-09-15 16:30"
        assert self.p("1天后") == "2026-09-16 14:30"
        assert self.p("一周后") == "2026-09-22 14:30"
        assert self.p("2 hours later") == "2026-09-15 16:30"

    # ---- 日期词 ----
    def test_day_words(self):
        assert self.p("明天") == "2026-09-16 14:30"          # 沿用现在的时刻
        assert self.p("tomorrow") == "2026-09-16 14:30"
        assert self.p("后天") == "2026-09-17 14:30"
        assert self.p("today") == "2026-09-15 14:30"

    def test_evening_words(self):
        assert self.p("今晚") == "2026-09-15 20:00"
        assert self.p("tonight") == "2026-09-15 20:00"

    def test_weekday(self):
        # 2026-09-15 是周二；"下周三"= 2026-09-16？不是 ——
        # 下周指的是"下一周"，这里按"最近的那个周三（若就是今天则 +7）"取
        assert self.p("周三") == "2026-09-16 14:30"
        assert self.p("monday") == "2026-09-21 14:30"

    # ---- ★ 两种"没写时刻"的规则不一样，这是最容易搞错的地方 ----
    def test_bare_date_gets_midnight(self):
        r"""说明"某个日期"-> 00:00；说"哪一天"-> 沿用现在的时刻。"""
        assert self.p("9-16") == "2026-09-16 00:00"           # 具体日期 -> 0 点
        assert self.p("2026年9月16日") == "2026-09-16 00:00"
        assert self.p("明天") == "2026-09-16 14:30"           # 相对说法 -> 沿用时刻

    # ---- 认不出来的 ----
    def test_unparseable_returns_none(self):
        for bad in ("", "   ", "瞎写的东东", "third", "25:00", "2026-13-45",
                    "2026-2-30 10:00"):
            assert self.p(bad) is None, bad

    def test_result_is_always_canonical(self):
        """★ 输出必须是规范格式 —— 这是"字符串排序 == 时间排序"的前提（决策 3B）。"""
        for text in ("30分钟后", "明天", "9月16日", "Sep 16 15:00", "3pm",
                     "下午3点", "2026/09/16 15:00", "下周"):
            got = self.p(text)
            assert got is not None, text
            assert timeutil.is_valid(got), (text, got)

    def test_image_of_relative_time_is_in_the_future(self):
        """相对时间算出来的必须在"现在"之后 —— 否则接口会以"不能早于当前时间"拒绝。"""
        for text in ("1分钟后", "30分钟后", "2小时后", "1天后", "一周后"):
            got = self.p(text)
            assert timeutil.parse(got) > self.BASE, (text, got)


class TestLocalMachineTimeOnly:
    r"""★★ 时间必须一律以【运行这个程序的电脑的本地时间】为准（用户明确要求）。

    【用户原话】
        「时间以用户电脑时间为准」

    【这条要求靠什么保证？】
        靠"全项目只用 datetime.now()（无时区的本地时间），
        并且**不做任何 UTC / 时区转换**"。

        为什么不引入时区处理？因为这是**本机个人工具**：
        数据只存在自己电脑上、也只在自己电脑上看。
        引入 UTC + 时区转换会带来一整类新问题（存的到底是什么时间？
        换台电脑看还是不是同一个时刻？），而收益为零。

    【为什么要把这条写成测试？】
        "把所有 datetime.now() 改成 datetime.utcnow()" 看起来只是改个名字，
        但会让**所有时间整体偏移 8 小时**（东八区），
        而且症状是"提醒晚了 8 小时才响"——很难第一时间联想到时区。

        这类"改一个词、错一整天"的地方，值得用一条测试拦一下。
    """
    def test_no_utc_or_timezone_conversion_anywhere(self):
        import re
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent
        app_dir = project_root / "app"
        forbidden = re.compile(
            r"utcnow|astimezone|\butc\b|timezone\.|tzinfo|pytz|dateutil|ZoneInfo")
        offenders = []
        for py in sorted(app_dir.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            for m in forbidden.finditer(text):
                line_no = text[:m.start()].count(chr(10)) + 1
                offenders.append("%s:%d 里出现了 %r" % (
                    py.relative_to(project_root), line_no, m.group(0)))
        assert not offenders, (
            "时间必须是本机本地时间，不允许出现 UTC / 时区转换：" + "；".join(offenders))

    def test_now_uses_local_time(self):
        """datetime.now() 和 utcnow() 在有夏令时的地区会差一小时 —— 这里确认用的是本地那个。"""
        import datetime as _dt

        from app.core import timeutil

        local_hour = _dt.datetime.now().hour
        assert timeutil.now().hour == local_hour, "now() 必须是本地时间"
        assert timeutil.now_str()[:13] == _dt.datetime.now().strftime("%Y-%m-%d %H")


class TestFlexiblePeriods:
    r"""★★ 【v0.4.4 新增】上午 / 下午 不能搞反。

    【为什么专门钉住这一件事？】
        实测发现过一个非常隐蔽的错：时段词的判定写成了
        "小时 < 12 就加 12"，结果【上午9点】被算成 21:00、
        【凌晨2点】被算成 14:00 —— 整整差 12 小时。
        它不报错、不崩溃，只是提醒会在错误的时刻响，
        用户只会觉得"这软件提醒不准"，根本查不到原因。
    """

    BASE = datetime(2026, 3, 15, 10, 30)

    def _parse(self, text, base=None):
        from app.core import timeutil

        return timeutil.parse_flexible(text, base or self.BASE)

    def test_morning_words_stay_in_the_morning(self):
        """上午/早上/早晨/清晨/凌晨 —— 小时数【不能】被加 12。"""
        for text, want in (("上午9点", "09:00"), ("早上7点", "07:00"),
                           ("早晨6点", "06:00"), ("清晨5点", "05:00"),
                           ("凌晨2点", "02:00")):
            got = self._parse(text)
            assert got and got.endswith(want), (
                "%s 应当落在 %s（上午），实际算成了 %s" % (text, want, got))

    def test_afternoon_words_do_go_to_pm(self):
        """下午/晚上/傍晚 —— 小时数要加 12。"""
        for text, want in (("下午3点", "15:00"), ("晚上八点半", "20:30"),
                           ("傍晚6点", "18:00"), ("夜里11点", "23:00")):
            got = self._parse(text)
            assert got and got.endswith(want), (
                "%s 应当是 %s，实际 %s" % (text, want, got))

    def test_noon_and_midnight(self):
        """中午12点 = 12:00；凌晨12点 / 上午12点 = 00:00。"""
        assert self._parse("中午12点").endswith("12:00")
        assert self._parse("中午1点").endswith("13:00")
        assert self._parse("凌晨12点").endswith("00:00")
        assert self._parse("上午12点").endswith("00:00")

    def test_mixed_language_still_works(self):
        """中英混排也要认（v0.4.3 就支持，这里顺手钉住别改坏）。"""
        got = self._parse("明天 3pm")
        assert got and got.endswith("15:00"), got
        got = self._parse("next monday 晚上8点")
        assert got and got.endswith("20:00"), got
