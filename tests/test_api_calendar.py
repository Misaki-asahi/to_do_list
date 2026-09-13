# -*- coding: utf-8 -*-
r"""
test_api_calendar.py -- 日历接口的集成测试。

日历最容易出错的地方是"日期计算"（跨月、跨年、闰年），
所以下面重点测这些边界。
"""

import calendar as pycalendar
from datetime import date, timedelta


class TestMonthGrid:
    def test_default_is_current_month(self, client):
        r = client.get("/api/calendar/month")
        assert r.status_code == 200
        today = date.today()
        assert r.json()["year"] == today.year
        assert r.json()["month"] == today.month

    def test_specific_month(self, client):
        r = client.get("/api/calendar/month?year=2026&month=9")
        assert r.json()["title"] == "2026 年 9 月"

    def test_each_week_has_seven_days(self, client):
        data = client.get("/api/calendar/month?year=2026&month=9").json()
        assert all(len(week) == 7 for week in data["weeks"])

    def test_grid_starts_on_monday(self, client):
        data = client.get("/api/calendar/month?year=2026&month=9").json()
        start = date.fromisoformat(data["grid_start"])
        assert start.weekday() == 0        # 0 = 周一

    def test_grid_ends_on_sunday(self, client):
        data = client.get("/api/calendar/month?year=2026&month=9").json()
        end = date.fromisoformat(data["grid_end"])
        assert end.weekday() == 6          # 6 = 周日

    def test_days_in_month_match_calendar_module(self, client):
        """网格里"本月"的格子数，必须等于这个月实际的天数。"""
        for year, month in [(2026, 9), (2026, 2), (2024, 2), (2026, 1), (2026, 12)]:
            data = client.get("/api/calendar/month?year=%d&month=%d" % (year, month)).json()
            in_month = sum(1 for w in data["weeks"] for c in w if c["in_month"])
            assert in_month == pycalendar.monthrange(year, month)[1], (year, month)

    def test_leap_year_february(self, client):
        data = client.get("/api/calendar/month?year=2024&month=2").json()
        assert sum(1 for w in data["weeks"] for c in w if c["in_month"]) == 29

    def test_year_navigation_across_new_year(self, client):
        data = client.get("/api/calendar/month?year=2026&month=1").json()
        assert data["prev"] == {"year": 2025, "month": 12}
        data = client.get("/api/calendar/month?year=2026&month=12").json()
        assert data["next"] == {"year": 2027, "month": 1}

    def test_today_is_marked(self, client):
        data = client.get("/api/calendar/month").json()
        todays = [c for w in data["weeks"] for c in w if c["is_today"]]
        assert len(todays) == 1

    def test_weekday_names(self, client):
        data = client.get("/api/calendar/month").json()
        assert data["weekday_names"] == ["一", "二", "三", "四", "五", "六", "日"]


class TestParameterFallback:
    r"""★ BUG-009 的回归测试：参数非法要退回当月，而不是报 422。"""

    def test_missing_params(self, client):
        assert client.get("/api/calendar/month").status_code == 200

    def test_non_numeric_params(self, client):
        assert client.get("/api/calendar/month?year=abc&month=xyz").status_code == 200

    def test_out_of_range_month(self, client):
        assert client.get("/api/calendar/month?year=2026&month=99").status_code == 200

    def test_negative_year(self, client):
        assert client.get("/api/calendar/month?year=-5&month=0").status_code == 200

    def test_decimal_year(self, client):
        assert client.get("/api/calendar/month?year=2026.5&month=9").status_code == 200


class TestTasksInGrid:
    def test_task_appears_on_its_day(self, client, make_task):
        r"""任务必须落在它截止日期那一天对应的格子里。

        注意：日期要取【未来】的 —— 因为业务规则不允许把截止时间设成过去。
        """
        target = date.today() + timedelta(days=10)
        make_task(title="未来的任务", due_at=target.isoformat() + " 10:00")

        data = client.get("/api/calendar/month?year=%d&month=%d"
                          % (target.year, target.month)).json()
        cell = [c for w in data["weeks"] for c in w if c["date"] == target.isoformat()][0]
        assert [t["title"] for t in cell["tasks"]] == ["未来的任务"]

    def test_task_without_due_date_goes_to_undated(self, client, make_task):
        make_task(title="没有日期")
        data = client.get("/api/calendar/month").json()
        assert [t["title"] for t in data["undated"]] == ["没有日期"]

    def test_undated_task_not_in_grid(self, client, make_task):
        make_task(title="没有日期")
        data = client.get("/api/calendar/month").json()
        all_titles = [t["title"] for w in data["weeks"] for c in w for t in c["tasks"]]
        assert "没有日期" not in all_titles


class TestDayDetail:
    def test_normal_day(self, client):
        r = client.get("/api/calendar/day?day=2026-09-20")
        assert r.status_code == 200
        assert r.json()["date"] == "2026-09-20"
        assert r.json()["default_due_at"].endswith("23:59")

    def test_past_day_marked(self, client):
        assert client.get("/api/calendar/day?day=2020-01-01").json()["is_past"] is True

    def test_bad_day_falls_back_to_today(self, client):
        r"""参数非法要退回今天，而不是报错白屏。"""
        r = client.get("/api/calendar/day?day=完全不是日期")
        assert r.status_code == 200
        assert r.json()["date"] == date.today().isoformat()

    def test_empty_day_param(self, client):
        assert client.get("/api/calendar/day?day=").status_code == 200

    def test_tasks_of_that_day(self, client, make_task):
        make_task(title="当天任务", due_at="2026-09-20 15:00")
        r = client.get("/api/calendar/day?day=2026-09-20")
        assert [t["title"] for t in r.json()["tasks"]] == ["当天任务"]
