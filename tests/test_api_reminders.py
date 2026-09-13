# -*- coding: utf-8 -*-
r"""
test_api_reminders.py -- 提醒接口的集成测试。

注意：conftest.py 里已经把系统通知换成了空实现，
      所以跑这些测试【不会】真的往屏幕上弹通知。
"""

from app import config


class TestInbox:
    def test_empty(self, client):
        r = client.get("/api/reminders/inbox")
        assert r.status_code == 200
        data = r.json()
        assert data["items"] == []
        assert "sound_enabled" in data
        assert "native_supported" in data

    def test_sound_enabled_default_true(self, client):
        r"""默认开启页面提示音 —— 宁可多响一声，也不要让用户完全错过提醒。"""
        assert client.get("/api/reminders/inbox").json()["sound_enabled"] is True


class TestSnooze:
    def test_snooze_pushes_time_forward(self, client, make_task):
        r"""★ 稍后提醒用的是"当前时间 + N"，不是"原提醒时间 + N"。

        为什么？假设原来设的是 8:00，你 10:00 才点"稍后 10 分钟"：
            · 按原时间算 -> 8:10（已经过去了）-> 立刻又弹一次，很烦
            · 按当前时间算 -> 10:10 -> 符合直觉："从现在起再过 10 分钟"
        """
        from app.core import timeutil

        t = make_task(title="稍后提醒", remind_at="2099-01-01 09:00")
        r = client.post("/api/reminders/%d/snooze?minutes=15" % t["id"])
        assert r.status_code == 200

        new_time = r.json()["remind_at"]
        low = timeutil.add_minutes(timeutil.now_str(), 14)
        high = timeutil.add_minutes(timeutil.now_str(), 16)
        assert low <= new_time <= high, "新提醒时间 %s 不在预期范围 %s~%s 内" % (new_time, low, high)

    def test_snooze_clears_reminded_flag(self, client, make_task):
        from app.repositories import task_repo
        t = make_task(title="x", remind_at="2099-01-01 09:00")
        task_repo.mark_reminded(t["id"])
        r = client.post("/api/reminders/%d/snooze" % t["id"])
        assert r.json()["reminded_at"] is None

    def test_snooze_missing_is_404(self, client):
        assert client.post("/api/reminders/999999/snooze").status_code == 404


class TestStats:
    def test_stats_shape(self, client):
        r = client.get("/api/reminders/stats")
        assert r.status_code == 200
        data = r.json()
        for key in ("pending", "notified", "inbox", "poll_seconds", "snooze_minutes", "native"):
            assert key in data

    def test_counts_reminders(self, client, make_task):
        make_task(title="有提醒", remind_at="2099-01-01 09:00")
        make_task(title="没提醒")
        assert client.get("/api/reminders/stats").json()["pending"] == 1


class TestScheduler:
    def test_scheduler_running(self, client):
        r = client.get("/api/reminders/scheduler")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is True
        assert data["poll_seconds"] == config.REMINDER_SCAN_SECONDS
        assert "last_run" in data


class TestTestNotification:
    def test_send_test_notification(self, client):
        r = client.post("/api/reminders/test")
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestAck:
    def test_ack_missing_is_not_ok(self, client):
        r = client.post("/api/reminders/999999/ack")
        assert r.status_code == 200
        assert r.json()["ok"] is False
