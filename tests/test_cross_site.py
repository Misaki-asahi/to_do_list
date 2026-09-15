# -*- coding: utf-8 -*-
r"""
test_cross_site.py -- 跨站请求防护（v0.4.0 加，v0.4.5 收紧）。

【这个防线防的是什么？】
    服务只监听 127.0.0.1，所以"外面的人连不上"。
    但【你自己浏览器里打开的任意网页】能向你本机的这个服务发请求 ——
    浏览器是站在你这边发的，防火墙拦不住。

    最麻烦的是"不带请求体的 POST"属于浏览器的简单请求，**不触发跨域预检**，
    浏览器根本不拦。实测（BUG-043）：带 Origin: http://evil.example 时
        POST /api/floating/window/close  -> 200  悬浮窗被关掉
        POST /api/settings/shutdown      -> 200  整个程序退出

【v0.4.5 又收紧了一次】
    第一版只比对主机名（127.0.0.1 / localhost / ::1），
    于是"本机任意端口的网页"都算自己人 —— 你在本机跑的任何开发服务器
    （比如 127.0.0.1:5173）上的页面都能调这些接口。
    现在连端口一起比，只有本服务自己的页面才算。
"""

import pytest


class TestCrossSiteGuard:
    def _try(self, client, headers, method="GET", path="/api/settings"):
        if method == "GET":
            return client.get(path, headers=headers)
        return client.post(path, headers=headers, json={})

    def test_foreign_origin_is_rejected(self, client):
        r = self._try(client, {"Origin": "http://evil.example"})
        assert r.status_code == 403
        assert r.json()["type"] == "cross_site_blocked"

    def test_null_origin_is_rejected(self, client):
        r"""file:// 页面、沙箱 iframe 会发 Origin: null —— 那不是我们的页面。"""
        assert self._try(client, {"Origin": "null"}).status_code == 403

    def test_fetch_metadata_cross_site_is_rejected(self, client):
        r"""就算 Origin 缺失，Sec-Fetch-Site: cross-site 也要拦住。"""
        assert self._try(client, {"Sec-Fetch-Site": "cross-site"}).status_code == 403

    def test_dangerous_endpoints_are_covered_too(self, client):
        r"""★ 防护是"对所有接口一视同仁"的，不能只挡住 /api/settings。

        这里点名两个最危险的：关掉悬浮窗、退出整个程序。
        """
        for path in ("/api/settings/shutdown", "/api/floating/window/close",
                     "/api/tasks", "/api/floating"):
            r = self._try(client, {"Origin": "http://evil.example"}, "POST", path)
            assert r.status_code == 403, path

    def test_own_origin_is_allowed(self, client):
        from app import config

        for origin in ("http://127.0.0.1:%d" % config.PORT,
                       "http://localhost:%d" % config.PORT):
            r = self._try(client, {"Origin": origin})
            assert r.status_code == 200, origin

    def test_other_local_port_is_rejected(self, client):
        r"""★★ v0.4.5 新增：本机、但【不是我们这个端口】的网页，也要拒。

        不拦的话，"你在本机跑着的任何网页应用"都能读写你的待办、甚至关掉程序。
        """
        for origin in ("http://127.0.0.1:5173", "http://localhost:9999",
                       "http://127.0.0.1:80", "http://127.0.0.1"):
            r = self._try(client, {"Origin": origin})
            assert r.status_code == 403, "%s 应该被拒绝" % origin

    def test_no_origin_is_allowed(self, client):
        r"""命令行 curl、悬浮窗进程（urllib）不发 Origin —— 它们就在本机，放行。"""
        assert client.get("/api/settings").status_code == 200
