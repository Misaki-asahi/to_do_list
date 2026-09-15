# -*- coding: utf-8 -*-
r"""
test_api_floating.py -- 悬浮窗接口的集成测试（v0.3.0 新增）。

【这个文件重点守三件事】

    1. **PATCH 语义**：改一个字段不能把别的字段顺手重置 ——
       这条错了的后果是"拖一次窗口，用户调好的字号颜色全没了"。

    2. **令牌闸门**：窗口专用的那几个接口必须带令牌，
       不带或带错的一律 403 —— 否则别的网页也能改数据、关窗口。

    3. **参数校验的两种出口**：
       格式不对（比如颜色写成 "red"）-> 422，且错误信息是中文；
       规则不允许（比如透明度 2.0）-> 由 schemas 的 Field 范围挡成 422。
       两条路都要给出"用户看得懂"的原因，不能是一句 "Invalid input"。
"""

import pytest


class TestGetFloating:
    def test_shape(self, client):
        data = client.get("/api/floating").json()
        for key in ("config", "window", "supported", "defaults",
                    "themes", "fonts", "choices", "limits", "quick_remind", "token"):
            assert key in data, key

    def test_fonts_include_four_chinese_fonts(self, client):
        r"""★ 用户点名要的四款中文字体要通过接口给到前端。"""
        fonts = client.get("/api/floating").json()["fonts"]
        for name in ("宋体", "楷体", "仿宋", "黑体"):
            assert name in fonts, name

    def test_limits_include_defaults(self, client):
        r"""取值上限、下限、默认值一起给前端，前端才能把滑块初始化对。"""
        limits = client.get("/api/floating").json()["limits"]
        assert "opacity_default" in limits
        assert "font_size_default" in limits
        assert limits["opacity"][0] <= limits["opacity_default"] <= limits["opacity"][1]

    def test_default_config_matches_user_current_settings(self, client):
        r"""★ 出厂默认必须等于**用户当前正在用的那套设置**（v0.4.2 起）。

        【为什么改成这样？】
            用户 2026-09-14 明确要求：
            「字体字号，不透明度默认设置为我的当前设置内容」。
            他真实库里存的是：字号 **10**、不透明度 **0.90**、字体宋体。
            所以出厂默认一并对齐 —— 这样"恢复默认设置"或换台电脑重装，
            出来的样子和他现在用的一致。

        【⚠️ 这里有一段被推翻的历史，别删】
            在 v0.3.3 时默认是 **0.70**，依据是用户更早说过的
            「默认为黑色 30%透明度」。
            两个说法都是用户自己给的、前后不一致 ——
            **以最新一次为准**，把这段留下是为了下次不再纠结"到底哪个对"。

            顺便记住方向（写反过一次）：
                不透明度 0.90  ==  透明度 10%
                不透明度 0.70  ==  透明度 30%
                不透明度 0.30  ==  透明度 70%
        """
        from app import config

        cfg = client.get("/api/floating").json()["config"]
        assert cfg["bg_color"] == "#000000"
        assert cfg["opacity"] == config.FLOATING_DEFAULT_OPACITY == 0.90, (
            "默认不透明度要跟用户当前设置一致（0.90 = 透明度 10%）。"
            "注意方向和『透明度』相反：写成 0.10 才是理解反了")
        assert cfg["font_family"] == "宋体"
        assert cfg["font_size"] == config.FLOATING_DEFAULT_FONT_SIZE == 10
        assert cfg["always_on_top"] is False

    def test_choices_match_config(self, client):
        from app import config
        data = client.get("/api/floating").json()
        assert set(data["choices"]["group_by"]) == set(config.FLOATING_GROUP_BY_CHOICES)
        assert set(data["choices"]["scope"]) == set(config.FLOATING_SCOPE_CHOICES)

    def test_limits_match_config(self, client):
        from app import config
        limits = client.get("/api/floating").json()["limits"]
        assert limits["font_size"] == [config.FLOATING_MIN_FONT_SIZE,
                                       config.FLOATING_MAX_FONT_SIZE]
        assert limits["opacity"][0] == config.FLOATING_MIN_OPACITY
        assert limits["opacity"][1] == config.FLOATING_MAX_OPACITY

    def test_themes_present(self, client):
        from app import config
        themes = client.get("/api/floating").json()["themes"]
        assert set(themes) == set(config.FLOATING_THEMES)

    def test_token_is_not_empty(self, client):
        r"""令牌要自动生成 —— 用户不该被迫先"生成一个令牌"才能用这个功能。"""
        token = client.get("/api/floating").json()["token"]
        assert token and len(token) > 10


class TestPatchConfig:
    def test_single_field(self, client):
        r = client.patch("/api/floating/config", json={"opacity": 0.88})
        assert r.status_code == 200
        # 返回值是"改动之后的完整状态"（含 config + window），见 floating_service 的说明
        assert abs(r.json()["status"]["config"]["opacity"] - 0.88) < 0.001

    def test_patch_keeps_other_fields(self, client):
        r"""★ 本文件最重要的一条断言：改一个字段，别的不许动。"""
        client.patch("/api/floating/config", json={"font_size": 18, "bg_color": "#112233"})
        client.patch("/api/floating/config", json={"x": 500})
        cfg = client.get("/api/floating").json()["config"]
        assert cfg["x"] == 500
        assert cfg["font_size"] == 18
        assert cfg["bg_color"] == "#112233"

    def test_empty_body_rejected(self, client):
        r = client.patch("/api/floating/config", json={})
        assert r.status_code == 400
        assert r.json()["type"] == "business_error"

    def test_response_includes_window_status(self, client):
        r"""返回值里带 window 状态：前端点一下开关就能立刻显示"运行中/未运行"。"""
        data = client.patch("/api/floating/config", json={"opacity": 0.8}).json()
        assert "window" in data["status"]
        assert "running" in data["status"]["window"]

    def test_enable_shows_window(self, client):
        r"""开启总开关时，visible 自动跟开（用户的本意就是"我要看到它"）。"""
        data = client.patch("/api/floating/config", json={"enabled": True}).json()
        assert data["status"]["config"]["enabled"] is True
        assert data["status"]["config"]["visible"] is True

    def test_disable_hides_window(self, client):
        client.patch("/api/floating/config", json={"enabled": True})
        data = client.patch("/api/floating/config", json={"enabled": False}).json()
        assert data["status"]["config"]["visible"] is False


class TestConfigValidationOverApi:
    @pytest.mark.parametrize("payload", [
        {"opacity": 2.0},                       # 超过上限
        {"opacity": 0.29},                      # 低于下限（只剩一层影子）
        {"opacity": 0.0},                       # 完全透明
        {"width": 50},                          # 太窄
        {"height": 20},                         # 太矮
        {"font_size": 999},                     # 字号太大
        {"font_size": 1},                       # 字号太小
        {"group_by": "按运气"},                  # 不在选项里
        {"scope": "全部"},                       # 不在选项里
    ])
    def test_rejected(self, client, payload):
        r = client.patch("/api/floating/config", json=payload)
        assert r.status_code == 422, payload
        # 错误信息要是中文的（前端 api.js 会把英文翻译成中文，
        # 但我们自己写的校验器本来就应该直接给中文，省一道翻译）
        assert r.json()["detail"]

    def test_opacity_extremes_accepted(self, client):
        r"""★ 用户要求"透明度 0~100 都能设"：两端都要能存进去。"""
        from app import config
        for value in (config.FLOATING_MIN_OPACITY, 1.0):
            r = client.patch("/api/floating/config", json={"opacity": value})
            assert r.status_code == 200, value

    def test_font_size_extremes_accepted(self, client):
        """字号也要能调到两端（小屏 / 大屏 / 视力不便的用法都照顾到）。"""
        from app import config
        for value in (config.FLOATING_MIN_FONT_SIZE, config.FLOATING_MAX_FONT_SIZE):
            r = client.patch("/api/floating/config", json={"font_size": value})
            assert r.status_code == 200, value

    @pytest.mark.parametrize("bad_color", ["red", "#FFF", "#GGGGGG", "1f2933", "#12345678"])
    def test_bad_color_rejected(self, client, bad_color):
        r"""★ 颜色格式必须严格校验。

        因为这个值会被【原样】写进 tkinter 的参数。传一个乱码进去，
        悬浮窗进程会在创建控件时抛异常 —— 而它是个独立进程，
        用户只会看到"窗口打不开了"，完全不知道原因。
        在接口这一层挡住，他就能立刻看到"颜色格式不对"。
        """
        r = client.patch("/api/floating/config", json={"fg_color": bad_color})
        assert r.status_code == 422, bad_color

    def test_color_normalized_to_lowercase(self, client):
        cfg = client.get("/api/floating").json()["config"]
        client.patch("/api/floating/config", json={"bg_color": "#ABCDEF"})
        cfg = client.get("/api/floating").json()["config"]
        assert cfg["bg_color"] == "#abcdef"

    @pytest.mark.parametrize("bad_font", [" ", "有引号\"的字体", "带{大括号}的名字"])
    def test_bad_font_rejected(self, client, bad_font):
        r"""★ 字体名里的引号 / 大括号必须挡住。

        tkinter 的字体名会被拼进 Tcl 命令，带引号的字符串会被 Tcl
        当成【语法】而不是名字 —— 轻则字体不生效，重则窗口创建直接失败。
        （和"参数化 SQL"是同一类问题：不要让用户输入变成代码。）
        """
        r = client.patch("/api/floating/config", json={"font_family": bad_font})
        assert r.status_code == 422, bad_font

    def test_good_fonts_accepted(self, client):
        for name in ["Microsoft YaHei UI", "Consolas", "宋体", "Segoe UI"]:
            r = client.patch("/api/floating/config", json={"font_family": name})
            assert r.status_code == 200, name


class TestThemeAndReset:
    def test_apply_theme(self, client):
        r = client.post("/api/floating/theme?name=护眼绿")
        assert r.status_code == 200
        assert r.json()["config"]["bg_color"].startswith("#")

    def test_unknown_theme(self, client):
        r = client.post("/api/floating/theme?name=不存在")
        assert r.status_code == 400

    def test_missing_theme_param(self, client):
        assert client.post("/api/floating/theme").status_code == 422

    def test_reset(self, client):
        client.patch("/api/floating/config", json={"x": 900, "font_size": 20})
        r = client.post("/api/floating/reset")
        assert r.status_code == 200
        cfg = r.json()
        from app import config
        assert cfg["x"] == config.FLOATING_DEFAULT_X
        assert cfg["font_size"] == config.FLOATING_DEFAULT_FONT_SIZE


class TestWindowButtons:
    def test_close_then_status(self, client):
        client.patch("/api/floating/config", json={"enabled": True})
        r = client.post("/api/floating/window/close")
        assert r.status_code == 200
        cfg = client.get("/api/floating").json()["config"]
        assert cfg["visible"] is False
        # 【重点】关闭只是隐藏，总开关必须还开着 —— 否则用户会以为
        # "点了个叉，功能自己关掉了"，下次开机它就不出现了。
        assert cfg["enabled"] is True

    def test_show_after_close(self, client):
        client.patch("/api/floating/config", json={"enabled": True})
        client.post("/api/floating/window/close")
        r = client.post("/api/floating/window/show")
        assert r.status_code == 200
        cfg = client.get("/api/floating").json()["config"]
        assert cfg["visible"] is True

    def test_restart_returns_ok(self, client):
        r = client.post("/api/floating/window/restart")
        assert r.status_code == 200


class TestTokenGate:
    def test_data_without_token(self, client):
        r"""★ 不带令牌访问"窗口专用"接口必须被拒。"""
        assert client.get("/api/floating/data").status_code == 403

    def test_data_with_wrong_token(self, client):
        r"""★ 令牌不对必须 403，而且错误类型要明确（前端好据此提示）。"""
        r = client.get("/api/floating/data", headers={"X-Float-Token": "wrong-token-12345"})
        assert r.status_code == 403
        assert r.json()["type"] == "forbidden"

    def test_data_with_token(self, client):
        token = client.get("/api/floating").json()["token"]
        r = client.get("/api/floating/data", headers={"X-Float-Token": token})
        assert r.status_code == 200
        assert "groups" in r.json()

    def test_config_needs_token(self, client):
        assert client.get("/api/floating/config").status_code == 403

    def test_remind_at_needs_token(self, client):
        assert client.get("/api/floating/remind-at?choice=30").status_code == 403

    def test_tasks_create_needs_token(self, client):
        r = client.post("/api/floating/tasks", json={"title": "偷偷加的"})
        assert r.status_code == 403

    def test_settings_page_endpoints_do_not_need_token(self, client):
        r"""★ 反过来也要验：设置页（浏览器）没法带令牌，所以那些接口不能要求令牌。

        这一条容易被忽略：如果"顺手"给所有 /api/floating 接口都加了令牌，
        设置页会整个失效 —— 而且报错是 403，看起来像权限问题，
        排查方向会被带偏。所以正反两面都要有用例守着。
        """
        assert client.get("/api/floating").status_code == 200
        assert client.patch("/api/floating/config", json={"opacity": 0.9}).status_code == 200
        assert client.post("/api/floating/window/close").status_code == 200


class TestWindowTaskOperations:
    @pytest.fixture
    def token(self, client):
        return {"X-Float-Token": client.get("/api/floating").json()["token"]}

    def test_create(self, client, token):
        r = client.post("/api/floating/tasks", json={"title": "从窗口新建"}, headers=token)
        assert r.status_code == 200
        assert r.json()["task"]["title"] == "从窗口新建"
        # 真的进了数据库（从主列表也能看到）
        assert client.get("/api/tasks").json()[0]["title"] == "从窗口新建"

    def test_create_with_priority_and_category(self, client, token):
        r = client.post("/api/floating/tasks",
                        json={"title": "重要的事", "priority": 2, "category": "工作"},
                        headers=token)
        assert r.status_code == 200
        task = r.json()["task"]
        assert task["priority"] == 2
        assert task["category"] == "工作"

    def test_create_with_remind(self, client, token):
        from app.core import timeutil
        from datetime import timedelta
        remind = (timeutil.now() + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M")
        r = client.post("/api/floating/tasks",
                        json={"title": "待会儿提醒", "remind_at": remind},
                        headers=token)
        assert r.status_code == 200
        assert r.json()["task"]["remind_enabled"] is True

    def test_create_blank_title(self, client, token):
        r = client.post("/api/floating/tasks", json={"title": "   "}, headers=token)
        assert r.status_code == 422

    def test_toggle(self, client, token, make_task):
        task = make_task(title="勾一下")
        r = client.post("/api/floating/tasks/%d/toggle" % task["id"], headers=token)
        assert r.status_code == 200
        assert r.json()["task"]["status"] == "done"

    def test_toggle_missing_task(self, client, token):
        assert client.post("/api/floating/tasks/999999/toggle", headers=token).status_code == 404

    def test_update(self, client, token, make_task):
        task = make_task(title="旧名")
        r = client.patch("/api/floating/tasks/%d" % task["id"],
                         json={"title": "新名", "priority": 0}, headers=token)
        assert r.status_code == 200
        assert r.json()["task"]["title"] == "新名"

    def test_read_single_task(self, client, token, make_task):
        r"""★ 编辑弹窗打开时先读一次，保证"看到的就是最新的"。

        直接用列表里的 10 秒旧快照预填再保存，会把这段时间内的改动覆盖掉。
        """
        task = make_task(title="读我", description="详细说明", priority=2, category="工作")
        r = client.get("/api/floating/tasks/%d" % task["id"], headers=token)
        assert r.status_code == 200
        got = r.json()["task"]
        assert got["title"] == "读我"
        assert got["description"] == "详细说明"
        assert got["priority"] == 2
        assert got["category"] == "工作"

    def test_read_single_task_needs_token(self, client, make_task):
        task = make_task(title="别偷看")
        assert client.get("/api/floating/tasks/%d" % task["id"]).status_code == 403

    def test_read_missing_task(self, client, token):
        assert client.get("/api/floating/tasks/999999", headers=token).status_code == 404

    def test_data_lists_categories(self, client, token, make_task):
        r"""★ 数据接口要带上"用过的分类"，供编辑弹窗做快捷选择。

        没有它的话，用户每次都得凭记忆手打分类名，
        打错一个字就会多出一个新分类（"工作"和"工作了"变成两类）。
        """
        make_task(title="一", category="工作")
        make_task(title="二", category="生活")
        make_task(title="三", category=None)

        data = client.get("/api/floating/data?force=true", headers=token).json()
        assert "工作" in data["categories"]
        assert "生活" in data["categories"]
        # 空分类不该出现在列表里
        assert "" not in data["categories"]

    def test_data_groups_have_hints(self, client, token, make_task, future_due_clock):
        # 【不要写死"今天 23:00"】深夜跑会被业务规则拒绝（见 conftest.future_due 的说明）
        due, _which = future_due_clock()
        make_task(title="今天的事", due_at=due)
        data = client.get("/api/floating/data?force=true", headers=token).json()
        assert data["groups"][0].get("hint")

    def test_task_payload_has_raw_fields(self, client, token, make_task, future_due_clock):
        r"""★ 编辑弹窗预填要用"原始字符串"，不能用"今天 18:00"这种给人看的标签。

        混用会导致一个很恼人的 Bug：打开编辑框直接点保存就报格式错误
        （因为输入框里填的是"今天 18:00"，而接口只认 2026-09-14 18:00）。
        """
        due, which = future_due_clock()
        make_task(title="看原始字段", due_at=due, description="说明")

        data = client.get("/api/floating/data?force=true", headers=token).json()
        task = data["groups"][0]["tasks"][0]
        assert task["due_raw"] == due                 # 给输入框用（原样回传）
        assert task["due_label"]                      # 给人看（"今天 23:50"/"明天 23:50"）
        assert task["due_raw"] != task["due_label"]
        assert task["due_label"].startswith("今天" if which == "today" else "明天")
        assert task["description_raw"] == "说明"

    def test_update_empty_body(self, client, token, make_task):
        task = make_task(title="什么都不改")
        r = client.patch("/api/floating/tasks/%d" % task["id"], json={}, headers=token)
        assert r.status_code == 400

    def test_snooze(self, client, token, make_task):
        from app.core import timeutil
        from datetime import timedelta
        remind = (timeutil.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        task = make_task(title="晚点再说", remind_at=remind, remind_enabled=True)

        r = client.post("/api/floating/tasks/%d/snooze?minutes=15" % task["id"], headers=token)
        assert r.status_code == 200
        after = client.get("/api/tasks/%d" % task["id"]).json()
        assert after["remind_at"] > remind       # 时间被往后推了

    def test_remind_at_endpoint(self, client):
        token = {"X-Float-Token": client.get("/api/floating").json()["token"]}
        r = client.get("/api/floating/remind-at?choice=tomorrow", headers=token)
        assert r.status_code == 200
        from app.core import timeutil
        assert timeutil.is_valid(r.json()["remind_at"])

    def test_data_reflects_new_task(self, client, token):
        r"""★ 端到端：窗口新建一条 -> 窗口的数据接口里立刻能看到。

        这条把"写"和"读"串起来验证了一遍 ——
        如果只测写成功，而数据接口的缓存没被清掉，
        用户会看到"我加了一条，但它不显示"（要等 1 秒缓存过期才出现）。
        """
        client.post("/api/floating/tasks", json={"title": "马上要看到"}, headers=token)
        data = client.get("/api/floating/data?force=true", headers=token).json()
        titles = [t["title"] for g in data["groups"] for t in g["tasks"]]
        assert "马上要看到" in titles
