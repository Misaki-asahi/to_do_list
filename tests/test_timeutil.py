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
