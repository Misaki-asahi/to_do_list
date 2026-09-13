# -*- coding: utf-8 -*-
r"""
test_repositories.py -- 数据访问层的单元测试。

这一层是"唯一能碰数据库的地方"，它的正确性是整个项目的地基。
重点测：增删改查的边界情况（不存在、重复删、空值）。
"""

import pytest

from app import config
from app.core import timeutil
from app.repositories import setting_repo, task_repo


class TestCreate:
    def test_basic(self):
        t = task_repo.create(title="买牛奶")
        assert t.id is not None
        assert t.title == "买牛奶"
        assert t.status == config.STATUS_TODO

    def test_timestamps_are_auto_filled(self):
        """创建时间 / 修改时间必须由仓库层自动填，不让调用方传。"""
        t = task_repo.create(title="x")
        assert t.created_at == t.updated_at
        assert timeutil.is_valid(t.created_at)

    def test_ids_increase(self):
        a = task_repo.create(title="A")
        b = task_repo.create(title="B")
        assert b.id > a.id

    def test_default_values(self):
        t = task_repo.create(title="x")
        assert t.priority == config.PRIORITY_NORMAL
        assert t.remind_enabled == 0
        assert t.user_id == 1


class TestGet:
    def test_found(self):
        t = task_repo.create(title="找得到")
        assert task_repo.get(t.id).title == "找得到"

    def test_not_found_returns_none(self):
        """查不到要返回 None，而不是抛异常。"""
        assert task_repo.get(999999) is None


class TestList:
    def test_empty(self):
        assert task_repo.list_tasks() == []

    def test_returns_all(self):
        task_repo.create(title="A")
        task_repo.create(title="B")
        assert len(task_repo.list_tasks()) == 2

    def test_filter_by_status(self):
        a = task_repo.create(title="未完成")
        b = task_repo.create(title="已完成")
        task_repo.update(b.id, status=config.STATUS_DONE)
        assert [t.title for t in task_repo.list_tasks(status=config.STATUS_TODO)] == ["未完成"]
        assert [t.title for t in task_repo.list_tasks(status=config.STATUS_DONE)] == ["已完成"]

    def test_keyword_searches_title_and_description(self):
        task_repo.create(title="写周报")
        task_repo.create(title="无关", description="里面提到了周报两个字")
        task_repo.create(title="买牛奶")
        found = task_repo.list_tasks(keyword="周报")
        assert len(found) == 2

    def test_keyword_no_match(self):
        task_repo.create(title="A")
        assert task_repo.list_tasks(keyword="不存在的东西") == []

    def test_limit_and_offset(self):
        for i in range(5):
            task_repo.create(title="任务%d" % i)
        assert len(task_repo.list_tasks(limit=2)) == 2
        assert len(task_repo.list_tasks(limit=2, offset=4)) == 1


class TestUpdate:
    def test_basic(self):
        t = task_repo.create(title="旧标题")
        task_repo.update(t.id, title="新标题")
        assert task_repo.get(t.id).title == "新标题"

    def test_updated_at_refreshes(self):
        t = task_repo.create(title="x")
        before = t.updated_at
        task_repo.update(t.id, title="y")
        assert task_repo.get(t.id).updated_at >= before

    def test_rejects_unknown_field(self):
        r"""白名单防呆：不许改 id、created_at 这类字段（见 BUG 分析）。"""
        t = task_repo.create(title="x")
        with pytest.raises(ValueError):
            task_repo.update(t.id, created_at="1999-01-01 00:00")
        with pytest.raises(ValueError):
            task_repo.update(t.id, id=999)

    def test_update_missing_returns_none(self):
        assert task_repo.update(999999, title="x") is None

    def test_status_done_sets_completed_at(self):
        t = task_repo.create(title="x")
        task_repo.update(t.id, status=config.STATUS_DONE)
        assert task_repo.get(t.id).completed_at is not None

    def test_status_todo_clears_completed_at(self):
        t = task_repo.create(title="x")
        task_repo.update(t.id, status=config.STATUS_DONE)
        task_repo.update(t.id, status=config.STATUS_TODO)
        assert task_repo.get(t.id).completed_at is None

    def test_remind_enabled_converted_to_int(self):
        """传 True/False 也要能正确存成 1/0。"""
        t = task_repo.create(title="x")
        task_repo.update(t.id, remind_enabled=True)
        assert task_repo.get(t.id).remind_enabled == 1
        task_repo.update(t.id, remind_enabled=False)
        assert task_repo.get(t.id).remind_enabled == 0


class TestDelete:
    def test_delete_existing(self):
        t = task_repo.create(title="x")
        assert task_repo.delete(t.id) is True
        assert task_repo.get(t.id) is None

    def test_delete_twice_returns_false(self):
        """重复删除不能报错，只是返回 False。"""
        t = task_repo.create(title="x")
        task_repo.delete(t.id)
        assert task_repo.delete(t.id) is False

    def test_delete_missing_returns_false(self):
        assert task_repo.delete(999999) is False


class TestCountByStatus:
    def test_empty(self):
        assert task_repo.count_by_status() == {"todo": 0, "done": 0, "total": 0}

    def test_counts(self):
        task_repo.create(title="A")
        task_repo.create(title="B")
        c = task_repo.create(title="C")
        task_repo.update(c.id, status=config.STATUS_DONE)
        assert task_repo.count_by_status() == {"todo": 2, "done": 1, "total": 3}


class TestDateRange:
    r"""日历用的按日期范围查询。"""

    def test_list_between(self):
        task_repo.create(title="范围内", due_at="2026-09-15 10:00")
        task_repo.create(title="范围外", due_at="2026-10-15 10:00")
        found = task_repo.list_between("2026-09-01 00:00", "2026-09-30 23:59")
        assert [t.title for t in found] == ["范围内"]

    def test_ignores_tasks_without_due_date(self):
        task_repo.create(title="没有日期")
        assert task_repo.list_between("2026-01-01 00:00", "2026-12-31 23:59") == []

    def test_boundaries_are_inclusive(self):
        task_repo.create(title="开始瞬间", due_at="2026-09-01 00:00")
        task_repo.create(title="结束瞬间", due_at="2026-09-30 23:59")
        assert len(task_repo.list_between("2026-09-01 00:00", "2026-09-30 23:59")) == 2

    def test_list_undated(self):
        task_repo.create(title="有日期", due_at="2026-09-15 10:00")
        task_repo.create(title="没日期")
        assert [t.title for t in task_repo.list_undated()] == ["没日期"]


class TestReminderQueries:
    r"""提醒功能的两个核心查询。"""

    def test_due_for_reminder_finds_past_reminders(self):
        task_repo.create(title="该提醒了", remind_at="2020-01-01 09:00", remind_enabled=True)
        assert len(task_repo.due_for_reminder()) == 1

    def test_ignores_future_reminders(self):
        task_repo.create(title="未来", remind_at="2099-01-01 09:00", remind_enabled=True)
        assert task_repo.due_for_reminder() == []

    def test_ignores_disabled_reminders(self):
        task_repo.create(title="关了提醒", remind_at="2020-01-01 09:00", remind_enabled=False)
        assert task_repo.due_for_reminder() == []

    def test_ignores_completed_tasks(self):
        t = task_repo.create(title="做完了", remind_at="2020-01-01 09:00", remind_enabled=True)
        task_repo.update(t.id, status=config.STATUS_DONE)
        assert task_repo.due_for_reminder() == []

    def test_mark_reminded_prevents_repeat(self):
        r"""★ 防重复提醒的核心：标记之后就不再被扫出来。"""
        t = task_repo.create(title="弹过了", remind_at="2020-01-01 09:00", remind_enabled=True)
        assert len(task_repo.due_for_reminder()) == 1
        task_repo.mark_reminded(t.id)
        assert task_repo.due_for_reminder() == []

    def test_due_reminders_sorted_by_time(self):
        task_repo.create(title="晚的", remind_at="2020-01-02 09:00", remind_enabled=True)
        task_repo.create(title="早的", remind_at="2020-01-01 09:00", remind_enabled=True)
        found = task_repo.due_for_reminder()
        assert [t.title for t in found] == ["早的", "晚的"]


class TestResetAll:
    def test_requires_confirmation(self):
        r"""★ 危险操作必须显式确认（BUG-011 的教训）。"""
        task_repo.create(title="x")
        with pytest.raises(ValueError):
            task_repo.reset_all()
        assert len(task_repo.list_tasks()) == 1        # 数据还在

    def test_with_confirmation(self):
        task_repo.create(title="x")
        task_repo.create(title="y")
        assert task_repo.reset_all(confirm=True) == 2
        assert task_repo.list_tasks() == []


class TestSettingsRepo:
    def test_default_when_missing(self):
        assert setting_repo.get("不存在", "默认值") == "默认值"

    def test_set_and_get(self):
        setting_repo.set_value("theme", "dark")
        assert setting_repo.get("theme") == "dark"

    def test_overwrite(self):
        setting_repo.set_value("theme", "dark")
        setting_repo.set_value("theme", "light")
        assert setting_repo.get("theme") == "light"

    def test_bool(self):
        setting_repo.set_value("flag", "1")
        assert setting_repo.get_bool("flag") is True
        setting_repo.set_value("flag", "0")
        assert setting_repo.get_bool("flag") is False
        assert setting_repo.get_bool("从没设过", default=True) is True

    def test_value_is_stored_as_string(self):
        setting_repo.set_value("num", 42)
        assert setting_repo.get("num") == "42"

    def test_delete_key(self):
        setting_repo.set_value("x", "1")
        assert setting_repo.delete_key("x") is True
        assert setting_repo.get("x", "没了") == "没了"

    def test_all_settings(self):
        setting_repo.set_value("a", "1")
        setting_repo.set_value("b", "2")
        assert setting_repo.all_settings() == {"a": "1", "b": "2"}
