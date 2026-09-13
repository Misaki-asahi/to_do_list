# -*- coding: utf-8 -*-
r"""
test_calendar_service.py -- 日历服务的单元测试。

这些函数都是【纯函数】（只算日期，不碰数据库），
所以测起来又快又稳，也是这次重构能放心拆分的前提。
"""

from datetime import date, timedelta

from app.services import calendar_service as cal


class TestParseYearMonth:
    def test_normal(self):
        assert cal.parse_year_month("2026", "9") == (2026, 9)
        assert cal.parse_year_month(2026, 9) == (2026, 9)

    def test_none_falls_back_to_today(self):
        today = date.today()
        assert cal.parse_year_month(None, None) == (today.year, today.month)

    def test_garbage_falls_back_to_today(self):
        today = date.today()
        assert cal.parse_year_month("abc", "xyz") == (today.year, today.month)

    def test_out_of_range(self):
        today = date.today()
        assert cal.parse_year_month(99999, 99) == (today.year, today.month)
        assert cal.parse_year_month(-5, 0) == (today.year, today.month)

    def test_whitespace(self):
        assert cal.parse_year_month("  2026  ", "  9  ") == (2026, 9)


class TestMonthBounds:
    def test_normal_month(self):
        first, last = cal.month_bounds(2026, 9)
        assert first == date(2026, 9, 1)
        assert last == date(2026, 9, 30)

    def test_february_common_year(self):
        _, last = cal.month_bounds(2025, 2)
        assert last == date(2025, 2, 28)

    def test_february_leap_year(self):
        _, last = cal.month_bounds(2024, 2)
        assert last == date(2024, 2, 29)

    def test_december(self):
        first, last = cal.month_bounds(2026, 12)
        assert first == date(2026, 12, 1) and last == date(2026, 12, 31)


class TestGridBounds:
    def test_always_starts_on_monday(self):
        for month in range(1, 13):
            first, last = cal.month_bounds(2026, month)
            start, _ = cal.grid_bounds(first, last)
            assert start.weekday() == 0, month

    def test_always_ends_on_sunday(self):
        for month in range(1, 13):
            first, last = cal.month_bounds(2026, month)
            _, end = cal.grid_bounds(first, last)
            assert end.weekday() == 6, month

    def test_grid_covers_whole_weeks(self):
        for month in range(1, 13):
            first, last = cal.month_bounds(2026, month)
            start, end = cal.grid_bounds(first, last)
            assert (end - start).days % 7 == 6, month

    def test_month_is_inside_grid(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        assert start <= first and last <= end


class TestPrevNextMonth:
    def test_normal(self):
        assert cal.prev_next_month(2026, 9) == ((2026, 8), (2026, 10))

    def test_january_wraps_to_previous_december(self):
        assert cal.prev_next_month(2026, 1)[0] == (2025, 12)

    def test_december_wraps_to_next_january(self):
        assert cal.prev_next_month(2026, 12)[1] == (2027, 1)

    def test_both_wrap_at_once(self):
        assert cal.prev_next_month(2026, 1) == ((2025, 12), (2026, 2))
        assert cal.prev_next_month(2026, 12) == ((2026, 11), (2027, 1))


class TestDayRange:
    def test_normal(self):
        start, end = cal.day_range(date(2026, 9, 20))
        assert start == "2026-09-20 00:00"
        assert end == "2026-09-20 23:59"

    def test_start_sorts_before_end(self):
        r"""★ 这条保证了当天的任务不会因为字符串比较被漏掉。"""
        start, end = cal.day_range(date(2026, 9, 20))
        assert start < end
        assert start < "2026-09-20 08:30" < end

    def test_zero_padded(self):
        start, _ = cal.day_range(date(2026, 1, 5))
        assert start == "2026-01-05 00:00"


class TestGroupByDate:
    class FakeTask:
        def __init__(self, due_at):
            self.due_at = due_at

        def to_dict(self):
            return {"due_at": self.due_at}

    def test_groups_same_day(self):
        tasks = [self.FakeTask("2026-09-20 08:00"),
                 self.FakeTask("2026-09-20 15:00"),
                 self.FakeTask("2026-09-22 09:00")]
        grouped = cal.group_tasks_by_date(tasks)
        assert len(grouped["2026-09-20"]) == 2
        assert len(grouped["2026-09-22"]) == 1

    def test_empty(self):
        assert cal.group_tasks_by_date([]) == {}


class TestBuildWeeks:
    def test_shape(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        weeks = cal.build_weeks(start, end, 2026, 9, date(2026, 9, 13), {})
        assert all(len(w) == 7 for w in weeks)
        assert 4 <= len(weeks) <= 6

    def test_day_count_matches_range(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        weeks = cal.build_weeks(start, end, 2026, 9, date(2026, 9, 13), {})
        total = sum(len(w) for w in weeks)
        assert total == (end - start).days + 1

    def test_in_month_flag(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        weeks = cal.build_weeks(start, end, 2026, 9, date(2026, 9, 13), {})
        in_month = [c["day"] for w in weeks for c in w if c["in_month"]]
        assert in_month == list(range(1, 31))       # 九月有 30 天

    def test_today_flag_unique(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        weeks = cal.build_weeks(start, end, 2026, 9, date(2026, 9, 13), {})
        todays = [c for w in weeks for c in w if c["is_today"]]
        assert len(todays) == 1 and todays[0]["day"] == 13

    def test_past_flag(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        weeks = cal.build_weeks(start, end, 2026, 9, date(2026, 9, 13), {})
        past_days = [c["day"] for w in weeks for c in w if c["is_past"] and c["in_month"]]
        assert past_days == list(range(1, 13))

    def test_tasks_attached_to_correct_cell(self):
        first, last = cal.month_bounds(2026, 9)
        start, end = cal.grid_bounds(first, last)
        by_date = {"2026-09-20": [{"title": "任务"}]}
        weeks = cal.build_weeks(start, end, 2026, 9, date(2026, 9, 13), by_date)
        cell = [c for w in weeks for c in w if c["date"] == "2026-09-20"][0]
        assert cell["tasks"][0]["title"] == "任务"
