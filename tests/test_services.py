# -*- coding: utf-8 -*-
r"""
test_services.py -- 业务规则层的单元测试。

这一层只测【业务规则】，不碰数据库和 HTTP。
规则是项目里最容易变的东西，所以每一条规则都应该有一个对应的测试。
"""

import pytest

from app import config
from app.core import errors, timeutil
from app.schemas import TaskCreate, TaskUpdate
from app.services import task_service


def make(**kwargs):
    """快捷构造一个 TaskCreate。"""
    payload = {"title": "测试任务"}
    payload.update(kwargs)
    return TaskCreate(**payload)


class TestCreateRules:
    def test_basic(self):
        t = task_service.create_task(make())
        assert t.title == "测试任务"
        assert t.status == config.STATUS_TODO

    def test_title_is_trimmed(self):
        t = task_service.create_task(make(title="  前后有空格  "))
        assert t.title == "前后有空格"

    def test_filling_remind_time_auto_enables_switch(self):
        r"""★ 业务规则 1：填了提醒时间，就自动打开提醒开关。"""
        t = task_service.create_task(make(remind_at="2099-01-01 09:00"))
        assert t.remind_enabled == 1

    def test_enabled_without_time_is_rejected(self):
        """规则 2：开了提醒却没填时间 -> 拒绝。"""
        with pytest.raises(errors.BusinessError):
            task_service.create_task(make(remind_enabled=True))

    def test_past_remind_time_is_rejected(self):
        """规则 3：提醒时间不能是过去。"""
        with pytest.raises(errors.BusinessError) as exc:
            task_service.create_task(make(remind_at="2020-01-01 09:00"))
        assert "不能早于当前时间" in str(exc.value)

    def test_past_due_time_is_rejected(self):
        """规则 4：截止时间也不能是过去。"""
        with pytest.raises(errors.BusinessError):
            task_service.create_task(make(due_at="2020-01-01 09:00"))

    def test_future_time_is_accepted(self):
        t = task_service.create_task(make(due_at="2099-01-01 09:00"))
        assert t.due_at == "2099-01-01 09:00"

    def test_category_trimmed_and_emptied_to_none(self):
        t = task_service.create_task(make(category="   "))
        assert t.category is None


class TestGetTask:
    def test_found(self):
        created = task_service.create_task(make())
        assert task_service.get_task(created.id).id == created.id

    def test_not_found_raises(self):
        with pytest.raises(errors.NotFoundError):
            task_service.get_task(999999)


class TestUpdateRules:
    def setup_method(self):
        self.task = task_service.create_task(make(title="原始标题"))

    def test_update_title(self):
        updated = task_service.update_task(self.task.id, {"title": "新标题"})
        assert updated.title == "新标题"

    def test_blank_title_rejected(self):
        with pytest.raises(errors.BusinessError):
            task_service.update_task(self.task.id, {"title": "   "})

    def test_missing_task_raises(self):
        with pytest.raises(errors.NotFoundError):
            task_service.update_task(999999, {"title": "x"})

    def test_past_time_rejected(self):
        with pytest.raises(errors.BusinessError):
            task_service.update_task(self.task.id, {"due_at": "2020-01-01 09:00"})

    def test_enabling_reminder_without_time_rejected(self):
        """已经存了提醒时间的情况要能正确合并判断。"""
        with pytest.raises(errors.BusinessError):
            task_service.update_task(self.task.id, {"remind_enabled": True})

    def test_changing_remind_time_resets_reminded_flag(self):
        r"""★ 业务规则 4（最容易漏的一条）：

        改了提醒时间就必须清空"已提醒"标记，
        否则新的时间到了也不会再弹（因为扫描条件是 reminded_at 为空）。
        """
        t = task_service.create_task(make(remind_at="2099-01-01 08:00"))
        from app.repositories import task_repo
        task_repo.mark_reminded(t.id)
        assert task_repo.get(t.id).reminded_at is not None

        task_service.update_task(t.id, {"remind_at": "2099-01-01 09:00"})
        assert task_repo.get(t.id).reminded_at is None

    def test_category_empty_becomes_none(self):
        updated = task_service.update_task(self.task.id, {"category": "  "})
        assert updated.category is None


class TestToggle:
    def test_toggles_both_ways(self):
        t = task_service.create_task(make())
        assert task_service.toggle_status(t.id).status == config.STATUS_DONE
        assert task_service.toggle_status(t.id).status == config.STATUS_TODO

    def test_missing_raises(self):
        with pytest.raises(errors.NotFoundError):
            task_service.toggle_status(999999)


class TestDelete:
    def test_delete(self):
        t = task_service.create_task(make())
        assert task_service.delete_task(t.id) is True
        with pytest.raises(errors.NotFoundError):
            task_service.get_task(t.id)

    def test_delete_missing_raises(self):
        with pytest.raises(errors.NotFoundError):
            task_service.delete_task(999999)


class TestListCleaning:
    r"""列表接口的参数清洗：网址参数不可信，非法值要当成"没传"。"""

    def test_invalid_status_ignored(self):
        task_service.create_task(make(title="A"))
        assert len(task_service.list_tasks(status="乱写")) == 1

    def test_blank_keyword_treated_as_none(self):
        task_service.create_task(make(title="A"))
        assert len(task_service.list_tasks(keyword="   ")) == 1

    def test_valid_status_filter(self):
        task_service.create_task(make(title="A"))
        b = task_service.create_task(make(title="B"))
        task_service.toggle_status(b.id)
        assert len(task_service.list_tasks(status=config.STATUS_TODO)) == 1


class TestSchemaValidation:
    r"""schemas 层的格式校验（和业务规则是两回事）。"""

    def test_blank_title_rejected(self):
        with pytest.raises(Exception):
            make(title="   ")

    def test_bad_time_format_rejected(self):
        r"""★ 不补零的格式必须被拒绝（BUG-018）。"""
        for bad in ["2026/09/13", "2026-9-13 8:30", "2026-9-3 08:30"]:
            with pytest.raises(Exception):
                make(due_at=bad)

    def test_good_time_format_accepted(self):
        assert make(due_at="2026-09-13 08:30").due_at == "2026-09-13 08:30"

    def test_priority_out_of_range_rejected(self):
        with pytest.raises(Exception):
            make(priority=99)
        with pytest.raises(Exception):
            make(priority=-1)

    def test_title_too_long_rejected(self):
        with pytest.raises(Exception):
            make(title="x" * (config.TITLE_MAX_LENGTH + 1))

    def test_update_all_fields_optional(self):
        TaskUpdate()          # 全都不传也不该报错

    def test_update_status_validated(self):
        with pytest.raises(Exception):
            TaskUpdate(status="乱写")
