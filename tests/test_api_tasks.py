# -*- coding: utf-8 -*-
r"""
test_api_tasks.py -- 待办接口的集成测试（走完整的 HTTP 链路）。

和 test_services.py 的区别：
    那边直接调用 Python 函数，测的是"业务规则对不对"；
    这边发真实 HTTP 请求，测的是"从网址到数据库的整条路通不通"。
"""

from app import config


class TestCreate:
    def test_create_minimal(self, client):
        r = client.post("/api/tasks", json={"title": "买牛奶"})
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "买牛奶"
        assert data["status"] == config.STATUS_TODO
        assert data["id"] > 0
        assert len(data["created_at"]) == 16

    def test_create_full(self, client):
        r = client.post("/api/tasks", json={
            "title": "完整任务", "description": "详情", "priority": 2,
            "due_at": "2099-12-31 20:00", "remind_at": "2099-12-31 19:30",
            "category": "学习"})
        assert r.status_code == 201
        data = r.json()
        assert data["priority"] == 2
        assert data["category"] == "学习"
        assert data["remind_enabled"] is True     # 填了提醒时间会自动开启

    def test_title_is_trimmed(self, client):
        r = client.post("/api/tasks", json={"title": "  两边有空格  "})
        assert r.json()["title"] == "两边有空格"

    def test_empty_body_is_422(self, client):
        r = client.post("/api/tasks", json={})
        assert r.status_code == 422
        assert r.json()["type"] == "validation_error"

    def test_blank_title_is_422(self, client):
        assert client.post("/api/tasks", json={"title": "   "}).status_code == 422

    def test_past_time_is_400(self, client):
        r = client.post("/api/tasks", json={"title": "x", "due_at": "2020-01-01 00:00"})
        assert r.status_code == 400
        assert r.json()["type"] == "business_error"

    def test_unpadded_time_is_422(self, client):
        r"""★ BUG-018 的回归测试：不补零的时间必须被拒绝。"""
        r = client.post("/api/tasks", json={"title": "x", "due_at": "2099-1-1 8:30"})
        assert r.status_code == 422

    def test_title_too_long_is_422(self, client):
        r = client.post("/api/tasks", json={"title": "x" * 300})
        assert r.status_code == 422

    def test_priority_out_of_range_is_422(self, client):
        assert client.post("/api/tasks", json={"title": "x", "priority": 9}).status_code == 422


class TestList:
    def test_empty_list(self, client):
        r = client.get("/api/tasks")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_created(self, client, make_task):
        make_task(title="A")
        make_task(title="B")
        assert len(client.get("/api/tasks").json()) == 2

    def test_filter_todo(self, client, make_task):
        make_task(title="未完成")
        done = make_task(title="已完成")
        client.post("/api/tasks/%d/toggle" % done["id"])
        titles = [t["title"] for t in client.get("/api/tasks?status=todo").json()]
        assert titles == ["未完成"]

    def test_filter_done(self, client, make_task):
        make_task(title="未完成")
        done = make_task(title="已完成")
        client.post("/api/tasks/%d/toggle" % done["id"])
        titles = [t["title"] for t in client.get("/api/tasks?status=done").json()]
        assert titles == ["已完成"]

    def test_invalid_status_returns_all(self, client, make_task):
        make_task(title="A")
        assert len(client.get("/api/tasks?status=乱写").json()) == 1

    def test_search(self, client, make_task):
        make_task(title="写周报")
        make_task(title="买牛奶")
        assert len(client.get("/api/tasks?keyword=周报").json()) == 1

    def test_search_and_filter_combined(self, client, make_task):
        a = make_task(title="写周报")
        b = make_task(title="写代码")
        client.post("/api/tasks/%d/toggle" % b["id"])
        result = client.get("/api/tasks?status=todo&keyword=写").json()
        assert [t["title"] for t in result] == ["写周报"]
        assert a["id"] == result[0]["id"]


class TestGetOne:
    def test_found(self, client, sample_task):
        r = client.get("/api/tasks/%d" % sample_task["id"])
        assert r.status_code == 200
        assert r.json()["id"] == sample_task["id"]

    def test_not_found(self, client):
        r = client.get("/api/tasks/999999")
        assert r.status_code == 404
        assert r.json()["type"] == "not_found"


class TestStats:
    def test_empty(self, client):
        assert client.get("/api/tasks/stats").json() == {"total": 0, "todo": 0, "done": 0}

    def test_counts(self, client, make_task):
        make_task(title="A")
        b = make_task(title="B")
        client.post("/api/tasks/%d/toggle" % b["id"])
        assert client.get("/api/tasks/stats").json() == {"total": 2, "todo": 1, "done": 1}

    def test_stats_route_not_shadowed_by_id_route(self, client):
        r"""★ BUG-006 的回归测试。

        /stats 必须写在 /{task_id} 前面，否则 "stats" 会被当成 task_id，
        导致返回 422 而不是统计结果。
        """
        r = client.get("/api/tasks/stats")
        assert r.status_code == 200
        assert "total" in r.json()


class TestUpdate:
    def test_patch_title_only(self, client, sample_task):
        r = client.patch("/api/tasks/%d" % sample_task["id"], json={"title": "新标题"})
        assert r.status_code == 200
        assert r.json()["title"] == "新标题"
        assert r.json()["priority"] == sample_task["priority"]      # 没传的字段不变

    def test_patch_multiple_fields(self, client, sample_task):
        r = client.patch("/api/tasks/%d" % sample_task["id"],
                         json={"priority": 2, "category": "工作"})
        data = r.json()
        assert data["priority"] == 2 and data["category"] == "工作"
        assert data["title"] == sample_task["title"]

    def test_explicit_null_clears_field(self, client, make_task):
        r"""★ PATCH 语义的核心：显式传 null 表示"清空"，不传表示"不改"。"""
        t = make_task(title="x", category="学习")
        r = client.patch("/api/tasks/%d" % t["id"], json={"category": None})
        assert r.json()["category"] is None

    def test_not_passing_field_keeps_it(self, client, make_task):
        t = make_task(title="x", category="学习")
        r = client.patch("/api/tasks/%d" % t["id"], json={"title": "改了标题"})
        assert r.json()["category"] == "学习"

    def test_empty_patch_is_400(self, client, sample_task):
        r = client.patch("/api/tasks/%d" % sample_task["id"], json={})
        assert r.status_code == 400

    def test_patch_missing_is_404(self, client):
        assert client.patch("/api/tasks/999999", json={"title": "x"}).status_code == 404


class TestToggle:
    def test_toggle_twice(self, client, sample_task):
        tid = sample_task["id"]
        first = client.post("/api/tasks/%d/toggle" % tid).json()
        assert first["status"] == config.STATUS_DONE
        assert first["completed_at"] is not None
        second = client.post("/api/tasks/%d/toggle" % tid).json()
        assert second["status"] == config.STATUS_TODO
        assert second["completed_at"] is None

    def test_missing_is_404(self, client):
        assert client.post("/api/tasks/999999/toggle").status_code == 404


class TestDelete:
    def test_delete(self, client, sample_task):
        r = client.delete("/api/tasks/%d" % sample_task["id"])
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert client.get("/api/tasks").json() == []

    def test_delete_twice_is_404(self, client, sample_task):
        tid = sample_task["id"]
        client.delete("/api/tasks/%d" % tid)
        assert client.delete("/api/tasks/%d" % tid).status_code == 404


class TestValidationFormat:
    r"""统一后的错误格式（第三阶段新增）。"""

    def test_422_has_errors_list(self, client):
        body = client.post("/api/tasks", json={}).json()
        assert body["type"] == "validation_error"
        assert isinstance(body["errors"], list)
        assert body["errors"][0]["field"] == "title"

    def test_400_has_type(self, client):
        body = client.post("/api/tasks", json={"title": "x", "due_at": "2020-01-01 00:00"}).json()
        assert body["type"] == "business_error"
        assert body["detail"]

    def test_404_has_type(self, client):
        body = client.get("/api/tasks/999999").json()
        assert body["type"] == "not_found"
