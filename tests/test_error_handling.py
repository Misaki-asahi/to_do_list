# -*- coding: utf-8 -*-
r"""
test_error_handling.py -- 异常处理与日志系统的测试。

《阶段三-02-异常处理与日志》里已经有一个 scripts/check_errors.py 做人工验收。
这里把其中适合自动化的部分变成 pytest 用例，好处是：
    以后每次改代码跑一遍 pytest，就能确认错误处理没被改坏。
"""

import pytest

from app import config
from app.core import errors as app_errors


class TestErrorTypes:
    r"""异常类型本身的行为。"""

    def test_status_codes(self):
        assert app_errors.BusinessError.status_code == 400
        assert app_errors.NotFoundError.status_code == 404
        assert app_errors.ValidationError.status_code == 422
        assert app_errors.AppError.status_code == 500

    def test_error_types(self):
        assert app_errors.BusinessError.error_type == "business_error"
        assert app_errors.NotFoundError.error_type == "not_found"

    def test_all_inherit_from_app_error(self):
        """统一基类的意义：以后要加全局行为只需要写一处。"""
        for cls in (app_errors.BusinessError, app_errors.NotFoundError,
                    app_errors.ValidationError):
            assert issubclass(cls, app_errors.AppError)

    def test_message_preserved(self):
        exc = app_errors.BusinessError("出问题了")
        assert str(exc) == "出问题了"
        assert exc.message == "出问题了"

    def test_extra_payload(self):
        exc = app_errors.BusinessError("出问题", field="title")
        assert exc.extra == {"field": "title"}


class TestErrorResponses:
    r"""四种错误返回的 HTTP 状态码和结构。"""

    def test_400_business_error(self, client):
        r = client.post("/api/tasks", json={"title": "x", "due_at": "2020-01-01 00:00"})
        assert r.status_code == 400
        body = r.json()
        assert body["type"] == "business_error"
        assert body["detail"]

    def test_404_not_found(self, client):
        r = client.get("/api/tasks/999999")
        assert r.status_code == 404
        assert r.json()["type"] == "not_found"

    def test_422_validation_error(self, client):
        r = client.post("/api/tasks", json={})
        assert r.status_code == 422
        body = r.json()
        assert body["type"] == "validation_error"
        assert isinstance(body["errors"], list)

    def test_500_internal_error(self, client, monkeypatch):
        r"""
        兜底处理器：任何没预料到的异常都要变成规范的 500，而不是崩溃。

        同时验证【堆栈不会泄露给用户】—— 堆栈里可能有文件路径、SQL 等敏感信息。
        """
        from app.services import task_service

        def boom(*args, **kwargs):
            raise RuntimeError("测试故意制造的异常")

        monkeypatch.setattr(task_service, "list_tasks", boom)
        r = client.get("/api/tasks")
        assert r.status_code == 500
        body = r.json()
        assert body["type"] == "internal_error"
        assert "RuntimeError" not in str(body)
        assert "Traceback" not in str(body)

    def test_service_recovers_after_error(self, client, monkeypatch):
        """出过错之后，接口要能自动恢复正常（异常没把服务搞坏）。"""
        from app.services import task_service

        original = task_service.list_tasks
        monkeypatch.setattr(task_service, "list_tasks",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert client.get("/api/tasks").status_code == 500
        monkeypatch.setattr(task_service, "list_tasks", original)
        assert client.get("/api/tasks").status_code == 200


class TestLogging:
    r"""日志系统。"""

    def test_log_file_created(self, client):
        """跑过请求之后，日志文件应该真的存在。"""
        client.get("/api/tasks")
        assert config.LOG_FILE.exists()

    def test_request_is_logged(self, client):
        client.get("/api/tasks")
        content = config.LOG_FILE.read_text(encoding="utf-8", errors="replace")
        assert "GET /api/tasks" in content
        assert "200" in content

    def test_business_error_is_logged(self, client):
        client.post("/api/tasks", json={"title": "x", "due_at": "2020-01-01 00:00"})
        content = config.LOG_FILE.read_text(encoding="utf-8", errors="replace")
        assert "400" in content

    def test_unhandled_exception_logs_traceback(self, client, monkeypatch):
        r"""★ 500 必须把【完整堆栈】写进日志 —— 这是排查的唯一线索。"""
        before = config.LOG_FILE.read_text(encoding="utf-8", errors="replace") \
            if config.LOG_FILE.exists() else ""

        from app.services import task_service
        monkeypatch.setattr(task_service, "list_tasks",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("独有的错误标记XYZ")))
        client.get("/api/tasks")

        after = config.LOG_FILE.read_text(encoding="utf-8", errors="replace")
        new_content = after[len(before):]
        assert "独有的错误标记XYZ" in new_content
        assert "Traceback" in new_content
        # 同一个异常只记一次（重复记录会把日志撑爆）
        #
        # 【为什么不能数 "Traceback (most recent call last)" 的次数？】
        #   这个次数会随"有几个中间件"变化：Starlette 在应用挂了
        #   两个以上 http 中间件时，会把异常包进 ExceptionGroup，
        #   于是同一个异常的堆栈里会出现多次这个字样 ——
        #   那是【同一个异常的展开】，不是"被记了两次"。
        #   所以这里改成数"那一条日志记录"本身：它只该出现一次。
        assert new_content.count("未处理的异常 GET /api/tasks") == 1, (
            "同一个异常被记录了多次（日志会被撑爆）")

    def test_log_info_endpoint(self, client):
        client.get("/api/tasks")
        info = client.get("/api/settings/logs").json()["info"]
        assert info["exists"] is True
        assert info["file_count"] >= 1
        assert info["keep_days"] == config.LOG_KEEP_DAYS


class TestIsolation:
    r"""数据安全：测试绝不能碰到真实数据（BUG-011 的教训）。"""

    def test_using_isolated_data_dir(self):
        assert "test" in str(config.DATA_DIR).lower(), \
            "测试的数据目录必须指向 data/test 下面，实际是 %s" % config.DATA_DIR

    def test_db_path_in_isolated_dir(self):
        assert str(config.DB_PATH).startswith(str(config.DATA_DIR))

    def test_backup_dir_isolated(self):
        assert str(config.BACKUP_DIR).startswith(str(config.DATA_DIR))

    def test_log_dir_isolated(self):
        assert str(config.LOG_DIR).startswith(str(config.DATA_DIR))

    def test_notification_disabled_in_tests(self):
        r"""测试环境不应该真的弹系统通知（conftest 里做了替换）。"""
        from app.core import notifier
        result = notifier.show("测试", "不应该真的弹出来")
        assert result["ok"] is True
        assert "测试环境" in result["message"]
