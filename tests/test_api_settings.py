# -*- coding: utf-8 -*-
r"""
test_api_settings.py -- 设置接口的集成测试。
"""


class TestGetSettings:
    def test_all_sections_present(self, client):
        data = client.get("/api/settings").json()
        for key in ("autostart", "stats", "about", "backups", "settings"):
            assert key in data

    def test_stats_sections(self, client):
        stats = client.get("/api/settings").json()["stats"]
        for key in ("tasks", "db", "reminders", "notify", "scheduler", "logs", "storage"):
            assert key in stats, key

    def test_about(self, client):
        about = client.get("/api/settings").json()["about"]
        assert about["name"]
        assert about["version"]
        assert about["python"]


class TestPreference:
    def test_set_sound_off_then_on(self, client):
        r = client.post("/api/settings/preference",
                        json={"key": "notify_sound_enabled", "value": "0"})
        assert r.status_code == 200
        assert client.get("/api/reminders/inbox").json()["sound_enabled"] is False

        client.post("/api/settings/preference",
                    json={"key": "notify_sound_enabled", "value": "1"})
        assert client.get("/api/reminders/inbox").json()["sound_enabled"] is True

    def test_set_app_id_mode(self, client):
        for mode in ("system", "own", "auto"):
            r = client.post("/api/settings/preference",
                            json={"key": "notify_app_id_mode", "value": mode})
            assert r.status_code == 200, mode

    def test_reject_unknown_key(self, client):
        r"""★ 白名单：不许往设置表里随便写东西。"""
        r = client.post("/api/settings/preference",
                        json={"key": "autostart_enabled", "value": "1"})
        assert r.status_code == 400
        assert r.json()["type"] == "business_error"

    def test_reject_invalid_value(self, client):
        r = client.post("/api/settings/preference",
                        json={"key": "notify_app_id_mode", "value": "乱写"})
        assert r.status_code == 400


class TestOpenFolder:
    def test_reject_path_traversal(self, client):
        r"""★ 安全：不能通过这个接口打开系统任意目录。"""
        for bad in ["../../windows", "C:/Windows", "..", "不存在"]:
            r = client.post("/api/settings/open-folder", json={"target": bad})
            assert r.status_code == 400, bad

    def test_target_must_be_string(self, client):
        assert client.post("/api/settings/open-folder", json={}).status_code == 422


class TestBackup:
    def test_manual_backup(self, client):
        r = client.post("/api/settings/backup")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["backups"]["count"] >= 1

    def test_backup_file_is_listed(self, client):
        client.post("/api/settings/backup")
        items = client.get("/api/settings").json()["backups"]["items"]
        assert len(items) >= 1
        assert items[0]["name"].startswith("todo_")


class TestLogs:
    def test_logs_endpoint(self, client):
        r = client.get("/api/settings/logs")
        assert r.status_code == 200
        data = r.json()
        assert "info" in data and "content" in data
        assert data["info"]["file"]

    def test_lines_parameter_is_clamped(self, client):
        r"""防止有人传个天文数字把服务器拖死。"""
        assert client.get("/api/settings/logs?lines=999999").status_code == 200
        assert client.get("/api/settings/logs?lines=-5").status_code == 200
        assert client.get("/api/settings/logs?lines=abc").status_code == 422


class TestShutdown:
    def test_shutdown_route_exists(self, client):
        r"""
        【只验证接口存在，不真的调用它。】

        调用 /shutdown 会 os._exit(0) —— 那会把跑测试的进程一起杀掉。
        所以这里只检查它出现在接口文档里。

        这是一个已知的测试盲区，如实记录：
            接口存在 ✓ / 真的能退出 ✗（靠人工验证）
        """
        spec = client.get("/openapi.json").json()
        assert "/api/settings/shutdown" in spec["paths"]
