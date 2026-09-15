# -*- coding: utf-8 -*-
r"""
test_floating_service.py -- 桌面悬浮窗业务逻辑的单元测试（v0.3.0 新增）。

【这些测试在测什么？】
    测"该不该开窗口""任务该怎么分块""设置范围对不对"这些【判断】，
    不测"窗口长什么样"（那是 tkinter 的事，得靠人眼看着确认）。

【怎么保证测试不会真的弹出窗口？】
    tests/conftest.py 里有一个全局装置，把"启动窗口进程"这一步换成了假函数。
    所以这里可以放心大胆地写 "enabled=True"，断言的是
    "它决定要开窗口了"（假函数被调用），而不是"屏幕上真的多了一个窗口"。

【怎么保证测试不碰你的真实数据？】
    tests/conftest.py 在 import app 之前就把数据目录切到 data/test/pytest 了
    （数据安全铁律一），而且每个测试前会清库。
"""

import time

import pytest

from app import config
from app.repositories import floating_repo, setting_repo
from app.services import floating_service


@pytest.fixture
def no_window(monkeypatch):
    """
    记录"有没有真的去启动窗口进程"，并保证它不会被真的启动。

    【为什么不用 conftest 里那个全局假函数？】
        conftest 里那个是【全局兜底】（防止测试误弹出窗口）。
        这里再包一层，是为了每次测试都重新计数 —— 断言起来更清楚，
        而且它会恢复原状，不会把调用记录串到别的测试里去。
    """
    calls = []

    def fake_popen(cmd, **kwargs):
        class _Proc:
            pid = 424242
        calls.append(cmd)
        stream = kwargs.get("stdout")
        if hasattr(stream, "close"):
            try:
                stream.close()
            except OSError:
                pass
        return _Proc()

    monkeypatch.setattr(floating_service, "_popen", fake_popen)
    return calls


@pytest.fixture
def running_window(monkeypatch):
    """
    假装"已经有一个窗口在运行"。

    这样测"开着的时候不要再开一个""关闭时要让它退出"就很容易了。
    """
    state = {"running": True, "stopped": 0}

    monkeypatch.setattr(floating_service, "is_window_running", lambda: state["running"])

    def fake_stop(reason="", timeout=3.0):
        state["running"] = False
        state["stopped"] += 1
        return True

    monkeypatch.setattr(floating_service, "stop_window", fake_stop)
    return state


# ===========================================================================
# 一、设置：默认值、改设置、范围校验
# ===========================================================================


class TestDefaults:
    def test_all_fields_have_defaults(self):
        values = floating_service.defaults()
        for name in floating_repo.FIELDS:
            assert name in values, name

    def test_default_is_on(self):
        r"""★ 悬浮窗默认是【开启并显示】的（v0.4.2 起，用户要求）。

        【这条为什么改过？—— 值得记下来】
            最初（v0.3.0）默认是**关闭**的，理由写在这条测试里：
            "新功能默认关闭是基本的礼貌，不然用户第二天开机
             发现桌面上莫名多一个窗口，会以为是病毒。"

            这个理由本身没错 —— 它默认服务的是"陌生的新用户"。

            但用户 2026-09-14 明确要求：「悬浮窗默认启动」。
            他是这个软件**唯一的使用者**，对他来说"多一个窗口"
            正是他想要的效果，不是惊吓。

            **教训：默认值的取舍取决于"用户是谁"。**
            面向陌生人时，"默认关闭"是礼貌；
            面向"我自己用"时，"默认打开"才是省事。
        """
        cfg = floating_service.get_config()
        assert cfg["enabled"] is True, "出厂就该是开着的（用户要求）"
        assert cfg["visible"] is True, "出厂就该显示出来"

    def test_default_look_matches_user_current_settings(self, client):
        r"""★★ 默认外观 = 用户当前正在用的那套（黑底 + 宋体 10 磅 + 不透明度 0.90）。

        【★ 方向必须记住，写反过 ★】
            用户口语说的「透明**度** 30%」= 代码里的**不透明度 0.70**
            （允许三成的光透过去）。开发中真写反过一次：
            把"透明度 30%"做成了"不透明度 0.30"——那几乎是一层全透的玻璃。

            · 不透明度 0.90  ->  透明度 10%   ← 现在默认这个（v0.4.2）
            · 不透明度 0.70  ->  透明度 30%   ← v0.3.3 时的默认，已被推翻
            · 不透明度 1.00  ->  透明度 0%
            · 不透明度 0.30  ->  透明度 70%

        【为什么默认值变过？】
            用户 2026-09-14 要求"不透明度默认设置为我的当前设置内容"，
            而他当前用的是 0.90。以**最新一次要求**为准。
        """
        cfg = floating_service.get_config()
        assert cfg["bg_color"] == "#000000", "默认背景是纯黑"
        assert cfg["opacity"] == config.FLOATING_DEFAULT_OPACITY == 0.90, (
            "默认不透明度应该跟用户当前设置一致（0.90 = 透明度 10%）。"
            "注意方向和『透明度』相反：写成 0.10 才是理解反了")
        assert cfg["font_family"] == "宋体", "默认字体是宋体"
        assert cfg["font_size"] == config.FLOATING_DEFAULT_FONT_SIZE == 10, (
            "默认字号 10 磅（= 用户当前设置）")

    def test_clarity_and_transparency_are_different_things(self, client):
        r"""★ 分清「清晰度」和「对比度」—— 用户专门澄清过。

        用户原话：「字体清晰指的是字体分辨率保持高，
                    在任何情况下不能因为模糊像素低而看不清」。

        【这两个概念的区别，值得写进测试里当文档】
            清晰度（锐利）：笔画边缘不糊 —— 靠 **DPI 感知**保证，
                            和透明度**无关**（半透明只是等比变淡，边缘仍是锐的）。
            对比度（读得出）：字和背景的亮度差 —— 这个**会**随透明度下降而降低。

        所以：
            · "字体必须清晰" 这条约束 -> 落在 `FLOATING_DPI_AWARE` 上；
            · "透明度可以调得很低" 这条要求 -> 落在不透明度范围上。
            两者互不冲突，之前把它们混在一起是理解错了。
        """
        # 清晰度那条约束的落点：DPI 感知必须开着（见 BUG-030）
        assert config.FLOATING_DPI_AWARE is True

        # 透明度那条要求的落点：范围允许调到很透（用户要 30% 透明度）
        assert config.FLOATING_MIN_OPACITY <= config.FLOATING_DEFAULT_OPACITY
        assert config.FLOATING_DEFAULT_OPACITY == 0.90      # v0.4.2：用户当前设置

    def test_opacity_floor_is_readable(self, client):
        r"""下限不能低到"完全读不出内容"。

        这个下限守的是**可读性**，不是清晰度：
        不透明度低到只剩一层影子时，窗口就失去意义了。
        """
        assert config.FLOATING_MIN_OPACITY >= 0.20, (
            "再低就只剩一层影子了，什么内容都读不出来")
        assert config.FLOATING_MIN_OPACITY < config.FLOATING_MAX_OPACITY

    def test_clarity_warn_line_is_inside_the_range(self, client):
        """提醒线必须在允许范围【之内】才有意义（否则用户永远看不到提醒）。"""
        assert config.FLOATING_MIN_OPACITY <= config.FLOATING_CLARITY_WARN_BELOW <= 1.0

    def test_default_opacity_is_above_the_warn_line(self, client):
        """默认值不应该一上来就触发"字会被桌面透进来"的提醒。"""
        assert config.FLOATING_DEFAULT_OPACITY >= config.FLOATING_CLARITY_WARN_BELOW

    def test_dpi_awareness_enabled_by_default(self, client):
        r"""★ DPI 感知默认必须开着 —— 它是"字不糊"的第一大原因（也是唯一的原因）。

        不开的话，Windows 会把整个窗口拉伸放大，文字被插值糊掉。
        这正是用户说的"不能因为模糊像素低而看不清"。
        """
        assert config.FLOATING_DPI_AWARE is True

    def test_default_window_can_be_covered(self, client):
        r"""★★ 用户明确要求：「悬浮窗要设于桌面，任何窗口都能覆盖」。

        也就是说默认【不置顶】。这条测试守着这个默认值 ——
        因为把它改回 True 只需要动一个字符，而后果是
        「悬浮窗永远压在所有软件上面」，那是用户特意要求改掉的行为。
        """
        assert floating_service.get_config()["always_on_top"] is False
        assert config.FLOATING_ALWAYS_ON_TOP is False

    def test_default_text_is_readable_on_default_background(self, client):
        r"""★ 黑底必须配浅色字。

        这条是在防一种很蠢但很容易犯的错：
        改了背景色却没改文字色（或者反过来），
        结果"黑底黑字"—— 程序不报任何错，用户只看到一片漆黑。
        """
        from app.gui import window as gui

        cfg = floating_service.get_config()
        assert gui.contrast_text(cfg["bg_color"]) == cfg["fg_color"]

    def test_default_opacity_is_within_limits(self):
        """默认值必须落在允许范围里，否则"恢复默认"会立刻变成一个非法值。"""
        assert (config.FLOATING_MIN_OPACITY
                <= config.FLOATING_DEFAULT_OPACITY
                <= config.FLOATING_MAX_OPACITY)

    def test_default_font_size_is_within_limits(self):
        assert (config.FLOATING_MIN_FONT_SIZE
                <= config.FLOATING_DEFAULT_FONT_SIZE
                <= config.FLOATING_MAX_FONT_SIZE)

    def test_default_font_is_one_of_the_presets(self):
        r"""默认字体必须是预设之一，否则下拉框会显示空白（用户看着像坏了）。"""
        assert config.FLOATING_DEFAULT_FONT_FAMILY in config.FLOATING_FONT_PRESETS

    def test_presets_contain_the_four_chinese_fonts(self):
        r"""★ 用户点名要的四款中文字体必须在预设里。"""
        for name in ("宋体", "楷体", "仿宋", "黑体"):
            assert name in config.FLOATING_FONT_PRESETS, name

    def test_themes_only_carry_colors(self):
        r"""★ 主题只带三个颜色，【不带透明度】（v0.3.4 改，见 BUG-042）。

        为什么专门钉住这一条？
            原来主题里带 opacity，于是用户点一下配色，
            他辛苦调好的透明度就被改掉了 —— 而且 v0.3.3 把默认改成
            "透明度 30%"（不透明度 0.70）之后，「黑玻璃」里那个旧的 0.92
            会让透明度直接变成 8%，和用户的要求正好相反。

            把"主题不含透明度"写成断言，以后有人想加回来就会立刻被这条挡住，
            逼他先想清楚"用户调好的透明度要不要被覆盖"。
        """
        for name, theme in config.FLOATING_THEMES.items():
            assert set(theme) == {"fg", "bg", "accent"}, (name, theme)
            for key in ("fg", "bg", "accent"):
                assert theme[key].startswith("#") and len(theme[key]) == 7, (name, key)

    def test_defaults_hidden_from_public_view(self):
        r"""pid / token 这些内部信息不该混在"设置"里给前端。"""
        values = floating_service.defaults()
        for name in floating_repo.INTERNAL_KEYS:
            assert name not in values, name


class TestOpacityRange:
    r"""
    ★ 不透明度的范围。

    【三个词的换算，务必记住（写反过一次）】
        不透明度 0.70  ==  透明度 30%
        不透明度 1.00  ==  透明度 0%
        不透明度 0.30  ==  透明度 70%

    用户要的默认值是「透明度 30%」-> **不透明度 0.70**。
    下限 0.30 守的是**可读性**（再低就只剩一层影子），
    和"清晰度/锐利度"无关 —— 那是 DPI 感知管的事（见 BUG-030）。
    """

    def test_full_low_end_accepted(self, client):
        cfg = floating_service.update_config(
            {"opacity": config.FLOATING_MIN_OPACITY})["status"]["config"]
        assert cfg["opacity"] == config.FLOATING_MIN_OPACITY

    def test_full_high_end_accepted(self, client):
        cfg = floating_service.update_config({"opacity": 1.0})["status"]["config"]
        assert cfg["opacity"] == 1.0

    def test_every_step_accepted(self, client):
        r"""把允许范围内每一个整数百分比都试一遍。

        这条看着有点啰嗦，但它守住的是"滑块能从头拉到尾"——
        如果哪天有人把范围改窄了，用户拉到头会被接口拒掉，
        而滑块本身却还能拖（前后端不一致），那才是最难解释的 Bug。
        """
        low = int(config.FLOATING_MIN_OPACITY * 100)
        for percent in range(low, 101):
            value = round(percent / 100.0, 2)
            cfg = floating_service.update_config({"opacity": value})["status"]["config"]
            assert abs(cfg["opacity"] - value) < 0.011, percent

    @pytest.mark.parametrize("bad", [0.0, 0.1, 0.29, 1.01, 2])
    def test_out_of_range_rejected(self, client, bad):
        r"""越界一律拒绝 —— 包括"全透"和"只剩一层影子"的那些值。"""
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError):
            floating_service.update_config({"opacity": bad})


class TestFontChoices:
    def test_all_presets_accepted(self, client):
        for name in config.FLOATING_FONT_PRESETS:
            cfg = floating_service.update_config({"font_family": name})["status"]["config"]
            assert cfg["font_family"] == name

    def test_wide_font_size_range(self, client):
        """字号也能从很小调到很大（用户要求"给用户设置的空间"）。"""
        for size in (config.FLOATING_MIN_FONT_SIZE, 24, 48,
                     config.FLOATING_MAX_FONT_SIZE):
            cfg = floating_service.update_config({"font_size": size})["status"]["config"]
            assert cfg["font_size"] == size

    def test_status_exposes_font_presets(self, client):
        r"""字体预设列表要通过接口给前端，这样加一款字体只改 config.py。"""
        data = floating_service.status()
        assert "宋体" in data["fonts"]
        assert "黑体" in data["fonts"]


class TestLegacyMigration:
    r"""
    ★ 数据迁移：旧版本的透明度是"百分比整数"（30~100），新版本是小数（0.05~1.0）。

    【为什么必须有这组测试？】
        因为不迁移的后果非常隐蔽：
        数据库里存着 92，直接喂给 tkinter 的 -alpha 参数会抛异常，
        表现是"窗口突然开不出来了"，而用户完全想不到是透明度的问题 ——
        他上一次动透明度可能是几个月前。
        这类"老数据 + 新格式"的问题，只有靠测试和一次主动迁移才能根治。
    """

    def test_migrates_percentage_to_fraction(self, client):
        # 手工模拟"旧版本留下的值"
        setting_repo.set_value(floating_repo.FIELDS["opacity"][0], "92")
        assert floating_repo.get_config()["opacity"] == 92      # 读出来还是旧值

        result = floating_service.migrate_legacy_settings()
        assert result["migrated"] is True
        assert result["old"] == 92
        assert result["new"] == 0.92
        assert floating_repo.get_config()["opacity"] == 0.92

    @pytest.mark.parametrize("old,new", [(100, 1.0), (50, 0.5), (30, 0.3), (45, 0.45)])
    def test_various_legacy_values(self, client, old, new):
        setting_repo.set_value(floating_repo.FIELDS["opacity"][0], str(old))
        floating_service.migrate_legacy_settings()
        assert abs(floating_repo.get_config()["opacity"] - new) < 0.001

    def test_does_not_touch_new_format(self, client):
        r"""★ 新格式的值不能被"顺手再除一次 100"。

        如果判断条件写错了，0.3 会被当成"3%"再除一次变成 0.003 ——
        窗口就淡到看不见了。所以这条一定要有。
        """
        floating_repo.set_config(opacity=0.3)
        result = floating_service.migrate_legacy_settings()
        assert result["migrated"] is False
        assert floating_repo.get_config()["opacity"] == 0.3

    def test_idempotent(self, client):
        """跑两次和跑一次的结果一样（迁移必须是幂等的，否则重启就是灾难）。"""
        setting_repo.set_value(floating_repo.FIELDS["opacity"][0], "92")
        floating_service.migrate_legacy_settings()
        first = floating_repo.get_config()["opacity"]
        floating_service.migrate_legacy_settings()
        assert floating_repo.get_config()["opacity"] == first

    def test_no_value_is_fine(self, client):
        assert floating_service.migrate_legacy_settings()["migrated"] is False

    def test_garbage_value_is_not_fatal(self, client):
        r"""★ 坏值不能让启动流程崩掉。

        迁移是在服务启动时跑的。如果它抛异常，整个服务都起不来 ——
        而设置表里的值可能是被谁手改过的。宁可放过，不可崩溃。
        """
        setting_repo.set_value(floating_repo.FIELDS["opacity"][0], "不知道")
        result = floating_service.migrate_legacy_settings()
        assert result["migrated"] is False
        assert "不是数字" in result["reason"]


class TestUpdateConfig:
    def test_set_position(self, client):
        r"""改设置返回的是"改动之后的完整状态"，所以从 status.config 里取。"""
        result = floating_service.update_config({"x": 300, "y": 200})
        cfg = result["status"]["config"]
        assert cfg["x"] == 300
        assert cfg["y"] == 200

    def test_partial_update_keeps_other_fields(self, client):
        r"""★ PATCH 语义的核心断言。

        拖动一次窗口只该改 x/y —— 绝不能把用户的字号、颜色
        顺手带回默认值。这条测试就是守着这件事的。
        """
        floating_service.update_config({"font_size": 16, "bg_color": "#123456"})
        floating_service.update_config({"x": 10, "y": 20})
        cfg = floating_service.get_config()
        assert cfg["font_size"] == 16
        assert cfg["bg_color"] == "#123456"

    def test_enable_also_shows(self, client, no_window):
        r"""★ "点开启"的本意是"我要看到它"，所以 visible 会自动跟开。"""
        cfg = floating_service.update_config({"enabled": True})["status"]["config"]
        assert cfg["enabled"] is True
        assert cfg["visible"] is True

    def test_disable_also_hides(self, client, running_window):
        floating_service.update_config({"enabled": True, "visible": True})
        cfg = floating_service.update_config({"enabled": False})["status"]["config"]
        assert cfg["visible"] is False
        assert running_window["stopped"] >= 1

    def test_unknown_field_rejected(self, client):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError) as exc:
            floating_service.update_config({"偷偷加的字段": 1})
        assert "不支持" in str(exc.value)

    def test_bump_version_on_change(self, client):
        r"""改了设置要让"数据版本号"变，窗口才会重新拉数据。"""
        before = floating_repo.get_data_version()
        floating_service.update_config({"opacity": 0.85})
        assert floating_repo.get_data_version() > before


class TestValidation:
    @pytest.mark.parametrize("width", [10, 199, 2001, 99999])
    def test_width_out_of_range(self, client, width):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError):
            floating_service.update_config({"width": width})

    def test_width_boundaries_accepted(self, client):
        for width in (config.FLOATING_MIN_WIDTH, config.FLOATING_MAX_WIDTH):
            cfg = floating_service.update_config({"width": width})["status"]["config"]
            assert cfg["width"] == width

    def test_font_size_range(self, client):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError):
            floating_service.update_config({"font_size": config.FLOATING_MAX_FONT_SIZE + 1})
        with pytest.raises(app_errors.BusinessError):
            floating_service.update_config({"font_size": config.FLOATING_MIN_FONT_SIZE - 1})

    def test_bad_group_by(self, client):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError):
            floating_service.update_config({"group_by": "按星座"})

    def test_bad_scope(self, client):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError):
            floating_service.update_config({"scope": "随便"})


class TestThemeAndReset:
    def test_apply_theme(self, client):
        """套用主题：颜色要跟着变，而【透明度必须原样保留】（BUG-042）。"""
        floating_service.update_config({"opacity": 0.55})
        cfg = floating_service.apply_theme("墨黑")
        theme = config.FLOATING_THEMES["墨黑"]
        assert cfg["bg_color"] == theme["bg"]
        assert cfg["fg_color"] == theme["fg"]
        assert abs(cfg["opacity"] - 0.55) < 0.001, "套用配色不该改掉用户调好的透明度"

    def test_unknown_theme(self, client):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError) as exc:
            floating_service.apply_theme("买不到的主题")
        assert "配色" in str(exc.value)

    def test_reset_brings_everything_back(self, client, running_window, no_window):
        floating_service.update_config({"x": 999, "y": 888, "font_size": 20,
                                        "bg_color": "#000000"})
        floating_service.reset_config()
        cfg = floating_service.get_config()
        assert cfg["x"] == config.FLOATING_DEFAULT_X
        assert cfg["y"] == config.FLOATING_DEFAULT_Y
        assert cfg["font_size"] == config.FLOATING_DEFAULT_FONT_SIZE
        assert cfg["bg_color"] == config.FLOATING_DEFAULT_BG


# ===========================================================================
# 二、分块（"按日期 / 优先级 / 分类分块显示"的实现）
# ===========================================================================


class TestGrouping:
    def test_group_by_date_buckets(self, client, make_task, future_due_clock):
        """
        按日期分块：已过期 / 今天 / 明天 / 七天内 / 之后 / 未安排日期。

        【注意标题带日期，所以断言用"开头匹配"而不是"等于"】
            用户要求「今天 明天 七天内 之后（要附带具体日期）」，
            所以标题是「今天 9-14」这种形式。
            断言写成 startswith 既验证了档位，又不会因为"今天换了日期"而失败。
        """
        from app.core import timeutil

        # 【注意】业务规则不允许建"截止时间在过去"的任务，
        # 所以"已过期"这条要绕过接口、直接写库来造（测试里这是允许的，
        # 因为真实场景里确实会出现"昨天建的任务今天还没做完"）。
        from app.repositories import task_repo

        task_repo.create(title="昨天就该做的", due_at="2020-01-01 08:00")
        # 【不要写死"今天 23:00"】深夜跑会被业务规则拒绝（见 conftest.future_due 的说明）
        today_due, which = future_due_clock()
        make_task(title="今天做", due_at=today_due)
        # ★★（BUG-045）断言必须跟着 which 走！
        #   future_due_clock() 在【22:30 之后】会顺延到明天 ——
        #   那时这条任务落在「明天」档，写死 startswith("今天") 就会每天 22:30 起必红。
        #   这不是"偶尔红一次"，而是"每晚固定红"：属于同一类"只在某个时段通过"的缺陷。
        today_bucket = "今天" if which == "today" else "明天"
        make_task(title="没日期")
        make_task(title="很久以后", due_at="2099-01-01 08:00")

        tasks = floating_service.window_payload(force=True)["groups"]
        titles = [g["title"] for g in tasks]
        assert any(t.startswith("已过期") for t in titles)
        assert any(t.startswith(today_bucket) for t in titles),             "本该落在「%s」这一档的任务没出现（which=%s，说明它被顺延了）" % (
                today_bucket, which)
        assert any(t.startswith("未安排日期") for t in titles)
        # 顺序：已过期 在最前，未安排日期 在最后
        assert titles[0].startswith("已过期")
        assert titles[-1].startswith("未安排日期")

    def test_group_titles_carry_dates(self, client, make_task, future_due_clock):
        r"""★ 用户要求「要附带具体日期」——标题里必须真的有日期。

        只写「今天」的话，用户没法确认它指的是哪一天，
        尤其是窗口挂了好几天没关的时候（桌面便签很常见就是一直开着）。
        """
        from app.core import timeutil

        today_due, which = future_due_clock()
        make_task(title="今天的事", due_at=today_due)
        groups = floating_service.window_payload(force=True)["groups"]

        # 深夜时 future_due_clock() 会顺延到明天，所以按它给的 which 去找对应的块
        prefix = "今天" if which == "today" else "明天"
        matched = [g["title"] for g in groups if g["title"].startswith(prefix)]
        assert matched, "没找到以『%s』开头的分组：%s" % (prefix, [g["title"] for g in groups])

        # 标题里应该出现"月-日"（例如 "今天 9-14"）
        day = today_due[:10]
        expect = "%d-%d" % (int(day[5:7]), int(day[8:10]))
        assert expect in matched[0], matched[0]
        _ = timeutil  # 保留 import：上面的断言用到 today_prefix 的语义

    def test_tomorrow_bucket(self, client, make_task):
        """明天的任务要落在"明天"这一档，并且带上明天的日期。"""
        from datetime import timedelta
        from app.core import timeutil

        tomorrow = (timeutil.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        make_task(title="明天的事", due_at=tomorrow + " 09:00")

        groups = floating_service.window_payload(force=True)["groups"]
        titles = [g["title"] for g in groups]
        tomorrow_titles = [t for t in titles if t.startswith("明天")]
        assert tomorrow_titles, titles
        expect = "%d-%d" % (int(tomorrow[5:7]), int(tomorrow[8:10]))
        assert expect in tomorrow_titles[0]

    def test_within_seven_days_bucket(self, client, make_task):
        r"""★ 用户要求的那一档「七天内」。

        3~7 天后的任务落在"七天内"，且标题显示的是【日期范围】
        而不是单个日期 —— 因为这一档里可能装着好几个不同的日子。
        """
        from datetime import timedelta
        from app.core import timeutil

        in_five = (timeutil.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        make_task(title="五天后的事", due_at=in_five + " 09:00")

        groups = floating_service.window_payload(force=True)["groups"]
        titles = [g["title"] for g in groups]
        seven = [t for t in titles if t.startswith("七天内")]
        assert seven, titles
        assert "~" in seven[0], "七天内这一档应该显示日期范围：%s" % seven[0]

    def test_later_bucket_is_one_group_with_dates_on_items(self, client, make_task):
        r"""★ 超过七天的都归到【同一个】「之后」块，具体日期标在每条任务上。

        【为什么要专门测这一条？（BUG-038）】
            原来的实现是"每个远期日期各成一块"：「之后 9-22」「之后 10-24」…
            这带来两个毛病：
              ① 远期日期一多，块会刷出一长串；
              ② **顺序会乱** —— 同一档的排序号都相同，排序只好退化成
                 "比标题字符串"，于是 "之后 10-24" 排到了 "之后 9-22" 前面
                 （字符 '1' < '9'），日期越晚反而越靠前。

            用户 2026-09-14 确认：「之后：一档就可以，但每一项后面表明具体日期」。
            所以现在断言两件事：**只有一个「之后」块** + **每条任务自己带着日期**。
        """
        from datetime import timedelta
        from app.core import timeutil

        d1 = (timeutil.now() + timedelta(days=40)).strftime("%Y-%m-%d")
        d2 = (timeutil.now() + timedelta(days=60)).strftime("%Y-%m-%d")
        make_task(title="四十天后的事", due_at=d1 + " 09:00")
        make_task(title="六十天后的事", due_at=d2 + " 09:00")

        groups = floating_service.window_payload(force=True)["groups"]
        later = [g for g in groups if g["title"] == "之后"]
        assert len(later) == 1, "「之后」必须合成一档，实际：%s" % [g["title"] for g in groups]
        assert later[0]["count"] == 2

        # 每一项自己带着具体日期（不是靠块标题）
        labels = {t["title"]: t["due_label"] for t in later[0]["tasks"]}
        assert d1[:4] in labels["四十天后的事"], labels
        assert d2[:4] in labels["六十天后的事"], labels
        # 块内按日期从早到晚（不能出现"越晚越靠前"）
        order = [t["title"] for t in later[0]["tasks"]]
        assert order == ["四十天后的事", "六十天后的事"], order

    def test_group_order_is_stable(self, client, make_task):
        r"""★ 连着算两次，块的顺序必须一样。

        如果不稳定，窗口每 10 秒刷新一次就会"分组跳来跳去"，
        用户刚想点的东西跑掉了 —— 这类问题很烦人但很容易被忽略。
        """
        from app.core import timeutil
        from datetime import timedelta

        today = timeutil.today_prefix()
        make_task(title="a", due_at=today + " 23:00")
        make_task(title="b", due_at=(timeutil.now() + timedelta(days=4)).strftime("%Y-%m-%d") + " 09:00")
        make_task(title="c", due_at="2099-01-01 08:00")
        make_task(title="d")

        first = [g["title"] for g in floating_service.window_payload(force=True)["groups"]]
        floating_service._data_cache["at"] = 0
        second = [g["title"] for g in floating_service.window_payload(force=True)["groups"]]
        assert first == second

    def test_group_hint_present(self, client, make_task):
        """每一块都带一句说明（鼠标停上去显示）。"""
        make_task(title="随便一条")
        groups = floating_service.window_payload(force=True)["groups"]
        assert groups[0].get("hint"), "分组应该有 hint 字段"

    def test_group_by_priority(self, client, make_task):
        make_task(title="不重要", priority=0)
        make_task(title="一般", priority=1)
        make_task(title="很急", priority=2)
        floating_service.update_config({"group_by": "priority"})

        groups = floating_service.window_payload(force=True)["groups"]
        titles = [g["title"] for g in groups]
        assert titles == ["高优先级", "中优先级", "低优先级"]
        assert groups[0]["tasks"][0]["title"] == "很急"

    def test_group_by_category(self, client, make_task):
        make_task(title="写代码", category="工作")
        make_task(title="买菜", category="生活")
        make_task(title="不知道归哪", category=None)
        floating_service.update_config({"group_by": "category"})

        groups = floating_service.window_payload(force=True)["groups"]
        titles = [g["title"] for g in groups]
        assert "工作" in titles and "生活" in titles
        # ★ 未分类永远排最后（它是"兜底"，不该占据视线第一屏）
        assert titles[-1] == "未分类"

    def test_group_key_returns_three_items(self, client, make_task):
        task = make_task(title="随便")
        from app.repositories import task_repo
        obj = task_repo.get(task["id"])
        for mode in config.FLOATING_GROUP_BY_CHOICES:
            key = floating_service.group_key(obj, mode)
            assert len(key) == 3, mode

    def test_done_tasks_hidden_by_default(self, client, make_task):
        t = make_task(title="已经做完的")
        client.post("/api/tasks/%d/toggle" % t["id"])
        payload = floating_service.window_payload(force=True)
        titles = [x["title"] for g in payload["groups"] for x in g["tasks"]]
        assert "已经做完的" not in titles

    def test_done_tasks_shown_when_asked(self, client, make_task):
        t = make_task(title="已经做完的")
        client.post("/api/tasks/%d/toggle" % t["id"])
        floating_service.update_config({"show_completed": True})
        payload = floating_service.window_payload(force=True)
        titles = [x["title"] for g in payload["groups"] for x in g["tasks"]]
        assert "已经做完的" in titles


class TestScope:
    def test_scope_today_excludes_far_future(self, client, make_task, future_due_today_clock):
        r"""
        "今天"这个范围要把远期任务排除掉。

        【★ 这个测试曾经是"看时间才通过"的】
            原来写的是 `due_at=today_prefix() + " 20:00"` ——
            白天跑没问题，**晚上 20:00 之后再跑就会被业务规则拒绝**
            （"截止时间不能早于当前时间"），于是测试假失败。
            这个缺陷是 20:02 修别的东西时撞出来的。

            现在改用 `future_due_clock()`：它会算一个**保证还在未来**的时刻，
            深夜跑就自动顺延到明天。所以断言也要相应地看它给的 `which`。
        """
        # ★★（BUG-045）这里必须用 future_due_today_clock()，不能用 future_due_clock()：
        #   后者在 22:30 之后会把时间顺延到【明天】，那时"今天的事"这个名字
        #   就名不副实了，断言必然失败（每天 22:30 起固定红）。
        #   前者保证给出"今天之内且还没到"的时刻；实在没有（23:56 之后）
        #   就诚实地 skip —— 宁可跳过，也不要让它每天红一次。
        try:
            due = future_due_today_clock()
        except RuntimeError as exc:
            pytest.skip(str(exc))

        make_task(title="今天的事", due_at=due)
        make_task(title="明年的事", due_at="2099-01-01 08:00")

        floating_service.update_config({"scope": "today"})
        payload = floating_service.window_payload(force=True)
        titles = [x["title"] for g in payload["groups"] for x in g["tasks"]]

        assert "今天的事" in titles, "本该在今天范围内的任务被漏掉了"
        assert "明年的事" not in titles, "远期任务不该出现在『今天』范围里"

    def test_scope_today_includes_overdue(self, client, make_task):
        r"""★ "今天"范围必须包含**已过期还没做**的任务。

        为什么不藏着过期的？—— 待办清单的价值恰恰在于"提醒你漏了什么"。
        这条和上面那条是一对：一个测"排除太远的"，一个测"包含漏掉的"。
        """
        from app.repositories import task_repo

        task_repo.create(title="早就该做的", due_at="2020-01-01 08:00")
        floating_service.update_config({"scope": "today"})
        payload = floating_service.window_payload(force=True)
        titles = [x["title"] for g in payload["groups"] for x in g["tasks"]]
        assert "早就该做的" in titles, "过期的任务被藏起来了 —— 那样的清单会『骗人』"

    def test_scope_active_keeps_undated(self, client, make_task):
        r""""未完成"这个默认范围必须包含没有日期的任务。

        否则用户随手加一条"想到再说"的待办，在悬浮窗里就消失了 ——
        他会以为"程序把我写的东西弄丢了"，这是最伤信任的一类 Bug。
        """
        make_task(title="没日期的事")
        payload = floating_service.window_payload(force=True)
        titles = [x["title"] for g in payload["groups"] for x in g["tasks"]]
        assert "没日期的事" in titles


class TestPayload:
    def test_payload_shape(self, client, make_task):
        make_task(title="看看结构")
        payload = floating_service.window_payload(force=True)
        for key in ("config", "groups", "stats", "next", "version", "signature", "now"):
            assert key in payload, key

    def test_signature_changes_with_content(self, client, make_task):
        r"""★ 指纹要能反映内容变化，否则窗口会一直不重画（界面看起来"卡住"）。"""
        make_task(title="第一版")
        first = floating_service.window_payload(force=True)["signature"]

        floating_service._data_cache["at"] = 0        # 清缓存，强制重算
        t = client.get("/api/tasks").json()[0]
        client.patch("/api/tasks/%d" % t["id"], json={"title": "改了个名字"})
        second = floating_service.window_payload(force=True)["signature"]
        assert first != second

    def test_decorated_fields(self, client, make_task, future_due_clock):
        due, which = future_due_clock()
        make_task(title="带日期的", due_at=due)
        payload = floating_service.window_payload(force=True)
        task = payload["groups"][0]["tasks"][0]
        # due_label 是人话标签（"今天 23:50" / 深夜时是"明天 23:50"）
        assert task["due_label"].startswith("今天" if which == "today" else "明天")
        assert task["due_raw"] == due            # 原始字符串要原样保留（编辑预填用）
        assert task["is_done"] is False
        assert task["overdue"] is False
        assert task["priority_name"] in config.PRIORITY_NAMES.values()

    def test_stats_counts(self, client, make_task):
        make_task(title="一")
        make_task(title="二")
        payload = floating_service.window_payload(force=True)
        assert payload["stats"]["total"] == 2
        assert payload["stats"]["todo"] == 2

    def test_next_reminder(self, client, make_task):
        from app.core import timeutil
        soon = (timeutil.now().replace(second=0, microsecond=0))
        from datetime import timedelta
        remind = (soon + timedelta(minutes=30)).strftime(config.DATETIME_FORMAT)
        make_task(title="待会儿提醒我", remind_at=remind, remind_enabled=True)

        payload = floating_service.window_payload(force=True)
        assert payload["next"] is not None
        assert payload["next"]["title"] == "待会儿提醒我"
        assert 0 <= payload["next"]["in_minutes"] <= 31


# ===========================================================================
# 三、窗口里的操作（新建 / 勾选 / 编辑 / 稍后提醒）
# ===========================================================================


class TestWindowActions:
    def test_quick_add(self, client):
        from app.schemas import TaskCreate
        result = floating_service.quick_add(TaskCreate(title="从窗口加的"))
        assert result["ok"] is True
        assert result["task"]["title"] == "从窗口加的"
        assert client.get("/api/tasks").json()[0]["title"] == "从窗口加的"

    def test_quick_add_reuses_business_rules(self, client):
        r"""★ 关键：窗口新建待办必须和网页共用同一套业务规则。

        否则会出现"网页里不让填过去时间，窗口里却能填"这种不一致 ——
        而"两个入口行为不同"是最难查的一类 Bug。
        """
        from app.core import errors as app_errors
        from app.schemas import TaskCreate

        with pytest.raises(app_errors.BusinessError):
            floating_service.quick_add(TaskCreate(
                title="提醒时间在昨天", remind_at="2020-01-01 08:00", remind_enabled=True))

    def test_toggle_then_untoggle(self, client, make_task):
        task = make_task(title="勾我")
        r1 = floating_service.toggle(task["id"])
        assert r1["task"]["status"] == config.STATUS_DONE
        r2 = floating_service.toggle(task["id"])
        assert r2["task"]["status"] == config.STATUS_TODO

    def test_update_task(self, client, make_task):
        task = make_task(title="旧标题")
        result = floating_service.update_task(task["id"], {"title": "新标题", "priority": 2})
        assert result["task"]["title"] == "新标题"
        assert result["task"]["priority"] == 2


class TestComputeRemindAt:
    def test_minutes(self, client):
        from app.core import timeutil
        result = floating_service.compute_remind_at("30")
        delta = timeutil.parse(result) - timeutil.now()
        assert 28 <= delta.total_seconds() / 60 <= 31

    def test_today(self, client):
        r"""★ 提醒时间必须【晚于当前】。

        注意"今天 18:00"这个选项：如果现在已经是晚上 8 点，
        直接算出来就是过去的时间，接口会以"提醒时间不能早于当前"拒绝 ——
        用户点了半天加不上，会以为是 Bug。所以服务端会自动顺延到明天。
        """
        from app.core import timeutil
        result = floating_service.compute_remind_at("today")
        assert timeutil.parse(result) > timeutil.now()

    def test_tomorrow(self, client):
        from app.core import timeutil
        result = floating_service.compute_remind_at("tomorrow")
        assert timeutil.parse(result) > timeutil.now()

    def test_invalid_choice(self, client):
        from app.core import errors as app_errors
        with pytest.raises(app_errors.BusinessError):
            floating_service.compute_remind_at("什么时候都行")


# ===========================================================================
# 四、窗口进程的座位管理（"不要开出两个窗口"）
# ===========================================================================


class TestWindowSeat:
    def test_status_when_off(self, client):
        """★ 明确关掉之后，状态里也要如实反映"关着、且没有窗口在跑"。"""
        floating_service.update_config({"enabled": False, "visible": False})
        status = floating_service.status()
        assert status["config"]["enabled"] is False
        assert status["window"]["running"] is False

    def test_no_spawn_when_disabled(self, client, no_window):
        result = floating_service.ensure_running()
        assert result["started"] is False
        assert no_window == []          # ★ 没开就不许启动进程

    def test_no_duplicate_window(self, client, no_window, running_window):
        floating_service.update_config({"enabled": True, "visible": True})
        result = floating_service.ensure_running()
        assert result["started"] is False
        assert no_window == []

    def test_supervisor_tick_opens_window(self, client, no_window):
        floating_service.update_config({"enabled": True, "visible": True})
        action = floating_service._supervisor_tick()
        assert "启动" in action

    def test_supervisor_tick_closes_window(self, client, running_window):
        r"""
        ★ 守护线程的核心职责之一：设置关掉之后，真的把窗口停掉。

        【注意这里为什么直接写 floating_repo，而不是调 update_config】
            update_config 自己就带了"关闭时顺手停窗口"的联动逻辑，
            用它来布置场景的话，窗口在"布置阶段"就已经被停了 ——
            那这条测试就变成了"在测 update_config"，而不是"在测守护线程"。
            测试要盯住一个东西，所以布置场景时绕开被测的那条路径。
        """
        floating_repo.set_config(enabled=False, visible=False)
        assert floating_service._supervisor_tick() == "已停止窗口"
        assert running_window["stopped"] >= 1

    def test_supervisor_tick_noop_when_running(self, client, running_window):
        floating_service.update_config({"enabled": True, "visible": True})
        assert floating_service._supervisor_tick() == "运行中"


class TestWindowLock:
    def test_acquire_then_second_fails(self, client):
        r"""★ 核心断言：同一个锁文件，第二个进程抢不到。

        这就是"点两次开启不会出现两个窗口"的技术保证。
        """
        from app.services import floating_service as svc

        first = svc.acquire_window_lock()
        assert first is not None, "第一次应该能拿到锁"
        try:
            second = svc.acquire_window_lock()
            assert second is None, "第二次必须拿不到（否则会开出两个窗口）"
            assert svc._lock_held() is True
        finally:
            svc.release_window_lock(first)

        third = svc.acquire_window_lock()
        assert third is not None, "释放之后应该又能拿到了"
        svc.release_window_lock(third)

    def test_lock_released_after_release(self, client):
        from app.services import floating_service as svc
        handle = svc.acquire_window_lock()
        svc.release_window_lock(handle)
        assert svc._lock_held() is False


class TestSupervisorBackoff:
    r"""
    ★★ 守护线程的"失败退避"（OPT-025，由 BUG-027 复盘而来）。

    【要解决的场景】
        BUG-027 里窗口因为一个配置错误（忘传令牌）反复启动失败，
        守护线程每 3 秒就再拉一个 —— 变成无限重启 + 进程堆积 + 日志刷屏。
        自愈本身是对的，**缺的是刹车**。

    【这三层保护各是干什么的】
        · 连续失败 3 次  -> 间隔从 3 秒放慢到 30 秒（少刷日志、少拉进程）
        · 连续失败 10 次 -> 停下自动重试，并在设置页把原因报给用户
        · 用户手动重试   -> 立刻解除退避（留一个"我现在就想试"的出口）
    """

    @pytest.fixture(autouse=True)
    def clean_state(self, client):
        """每个测试前后都把退避状态清干净，避免互相影响。"""
        floating_service.reset_supervisor_backoff()
        yield
        floating_service.reset_supervisor_backoff()

    @pytest.fixture
    def failing_spawn(self, monkeypatch):
        """让"启动窗口"始终失败，并且失败得像个真事儿（返回 ok=False + 原因）。"""
        def fake_ensure_running(force=False):
            return {"ok": False, "started": False,
                    "message": "启动失败：模拟的故障（比如令牌不对）"}
        monkeypatch.setattr(floating_service, "ensure_running", fake_ensure_running)
        # 设置成"开启 + 显示，但没有窗口"
        floating_repo.set_config(enabled=True, visible=True)
        monkeypatch.setattr(floating_service, "is_window_running", lambda: False)
        return fake_ensure_running

    def test_counts_failures(self, client, failing_spawn):
        floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["failures"] == 1
        floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["failures"] == 2

    def test_backs_off_after_three_failures(self, client, failing_spawn):
        r"""★ 第 3 次失败之后必须进入"放慢"状态。"""
        for _ in range(3):
            floating_service._supervisor_tick()

        state = floating_service.supervisor_last_run()
        assert state["backing_off"] is True
        assert state["given_up"] is False
        # 间隔要从 3 秒变成 30 秒
        assert floating_service._next_interval() == floating_service.BACKOFF_INTERVAL_SECONDS

    def test_gives_up_after_ten_failures(self, client, failing_spawn):
        r"""★ 连续失败 10 次之后必须停下 —— 这就是那道"刹车"。"""
        for _ in range(floating_service.GIVE_UP_AFTER_FAILURES):
            floating_service._supervisor_tick()

        assert floating_service.supervisor_last_run()["given_up"] is True

        # 再 tick 一次，不应该再去尝试启动了
        before = floating_service.supervisor_last_run()["failures"]
        action = floating_service._supervisor_tick()
        assert "等待用户处理" in action
        assert floating_service.supervisor_last_run()["failures"] == before, \
            "放弃之后不该再累加失败次数（也不该再拉起进程）"

    def test_health_reports_problem_and_advice(self, client, failing_spawn):
        r"""★ 出问题时必须给出"原因 + 该怎么办"。

        只说"坏了"是没用的 —— 用户需要知道下一步做什么。
        """
        for _ in range(floating_service.GIVE_UP_AFTER_FAILURES):
            floating_service._supervisor_tick()

        health = floating_service.supervisor_health()
        assert health["ok"] is False
        assert health["level"] == "error"
        assert health["given_up"] is True
        assert "模拟的故障" in health["reason"]      # 原因要带上
        assert health["advice"]                      # 建议要有
        assert "重启窗口" in health["advice"]

    def test_health_warns_during_backoff(self, client, failing_spawn):
        """放慢阶段是"警告"级别，不是"错误"（因为还在自动重试）。"""
        for _ in range(floating_service.BACKOFF_AFTER_FAILURES):
            floating_service._supervisor_tick()
        health = floating_service.supervisor_health()
        assert health["ok"] is False
        assert health["level"] == "warn"
        assert health["given_up"] is False

    def test_health_ok_when_normal(self, client):
        health = floating_service.supervisor_health()
        assert health["ok"] is True
        assert health["level"] == "ok"
        assert health["advice"] == ""

    def test_success_resets_counter(self, client, failing_spawn, monkeypatch):
        r"""★ 成功一次就把计数清零 —— 否则"偶尔失败一次"累积起来也会误判成故障。

        （真实场景：网络抖动导致一两次失败很正常，不该因此进入退避。）
        """
        floating_service._supervisor_tick()
        floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["failures"] == 2

        # 这次让它成功
        monkeypatch.setattr(floating_service, "is_window_running", lambda: True)
        floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["failures"] == 0
        assert floating_service.supervisor_last_run()["backing_off"] is False

    def test_manual_retry_clears_backoff(self, client, failing_spawn):
        r"""★ 用户主动重试必须能解除退避（否则"修好了也等不到重试"）。"""
        for _ in range(floating_service.GIVE_UP_AFTER_FAILURES):
            floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["given_up"] is True

        floating_service.reset_supervisor_backoff()
        state = floating_service.supervisor_last_run()
        assert state["failures"] == 0
        assert state["given_up"] is False
        assert floating_service._next_interval() == config.FLOATING_SUPERVISOR_SECONDS

    def test_interval_after_giving_up_is_even_longer(self, client, failing_spawn):
        r"""放弃之后间隔更长（不是停止，而是几乎不消耗资源地"打卡"）。

        为什么不让线程直接退出？因为用户可能随后手动重试或改设置 ——
        线程退出了就没人接着管，得重启程序才行。
        """
        for _ in range(floating_service.GIVE_UP_AFTER_FAILURES):
            floating_service._supervisor_tick()
        assert floating_service._next_interval() > floating_service.BACKOFF_INTERVAL_SECONDS

    def test_disabling_clears_backoff(self, client, failing_spawn):
        r"""用户关掉悬浮窗 = 情况变了，顺手清掉退避状态。

        否则他下次打开时可能一上来就撞上"已经放弃"，什么都没试就报错。
        """
        for _ in range(floating_service.GIVE_UP_AFTER_FAILURES):
            floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["given_up"] is True

        floating_repo.set_config(enabled=False, visible=False)
        floating_service._supervisor_tick()
        assert floating_service.supervisor_last_run()["given_up"] is False

    def test_status_exposes_supervisor_health(self, client):
        """接口要把它暴露出去，否则界面上没法显示。"""
        assert "supervisor" in floating_service.status()
        assert "summary" in floating_service.status()["supervisor"]


class TestSubprocessDecoding:
    r"""★★ 【v0.4.4 新增】调 Windows 自带命令时必须"宽容解码"。

    【为什么要专门钉住 errors='replace' 这一个参数？】
        实测：带 CREATE_NO_WINDOW 去调 taskkill / tasklist 时，
        Windows 返回的是【本地化中文】字节（GBK，例如 "成功: 已终止 PID …"），
        而本程序在 PYTHONUTF8=1 下是按 UTF-8 解码的 →
        读取线程抛 UnicodeDecodeError → 那一路的 stdout 直接变成 None。

        后果有两个，而且都不报错：
          ① 每次强制结束悬浮窗，日志里都会多一条 CRITICAL「未捕获的异常」——
             假崩溃把真问题淹没了；
          ② _process_alive() 永远判定"这个进程不在"。
        这种"少写一个参数"的缺陷光读代码很难发现，得让测试看着。
    """

    def _skip_if_not_windows(self):
        import sys

        if not sys.platform.startswith("win"):
            pytest.skip("这条测的是 Windows 自带命令的输出编码")

    def test_process_alive_decodes_leniently(self, monkeypatch):
        self._skip_if_not_windows()
        captured = {}

        class _Result:
            stdout = "pythonw.exe   1234 Console   1   10,000 K"

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return _Result()

        monkeypatch.setattr(floating_service.subprocess, "run", fake_run)
        assert floating_service._process_alive(1234) is True, (
            "输出里明明有这个 pid，却判定进程不在")
        assert captured.get("errors") == "replace", (
            "tasklist 在中文 Windows 上输出 GBK，必须 errors='replace'")

    def test_kill_decodes_leniently(self, monkeypatch):
        self._skip_if_not_windows()
        captured = {}

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return _Result()

        monkeypatch.setattr(floating_service.subprocess, "run", fake_run)
        floating_service._kill(4321)
        assert captured.get("errors") == "replace", (
            "taskkill 成功时输出的是中文（GBK），必须 errors='replace'")


class TestWindowProcessProbe:
    def test_own_process_is_alive(self):
        import os
        assert floating_service._process_alive(os.getpid()) is True

    def test_absurd_pid_is_not_alive(self):
        assert floating_service._process_alive(0) is False
        assert floating_service._process_alive(-1) is False

    def test_seat_roundtrip(self, client):
        """pid 文件的写入 / 读出 / 清理。"""
        import json

        config.FLOAT_DIR.mkdir(parents=True, exist_ok=True)
        config.FLOATING_PID_FILE.write_text(
            json.dumps({"pid": 12345, "started_at": time.time()}),
            encoding="utf-8")
        try:
            assert floating_service.read_seat()["pid"] == 12345
        finally:
            floating_service.clear_pid_file()
        assert floating_service.read_seat() == {}

    def test_broken_pid_file_is_tolerated(self, client):
        r"""★ 坏掉的 pid 文件不能让守护线程崩掉。

        后台线程一旦抛异常就会安静地死掉，而程序表面一切正常 ——
        那就是"悬浮窗再也起不来了"（这类静默死亡最难查，见 BUG-012）。
        """
        config.FLOAT_DIR.mkdir(parents=True, exist_ok=True)
        config.FLOATING_PID_FILE.write_text("这不是 JSON {{{", encoding="utf-8")
        try:
            assert floating_service.read_seat() == {}
        finally:
            floating_service.clear_pid_file()


class TestSpawnEnvironment:
    r"""
    ★ 这一组测试是【为一个真实踩到的 BUG】补的（BUG-023）。

    症状：设置里点"开启悬浮窗"，窗口闪一下就没了，
          任务管理器里却堆出一串 pythonw 进程。

    根因：启动子进程时忘了把【访问令牌】放进它的环境变量。
          窗口起来后第一次调接口就被 403 拒绝，于是自己退出；
          守护线程 3 秒后又把它拉起来 —— 无限循环。

    【为什么这个 Bug 特别值得写测试？】
        因为它【不报错】：接口返回 403 是"正常的错误处理"，
        日志里也没有崩溃堆栈，只是在反复重启。
        靠肉眼看日志很难一眼看出问题，靠自动测试却一测就中。
    """

    def _capture(self, monkeypatch, client):
        """把"真正启动进程"这一步换成假的，捕获它收到的参数。

        ⚠️ 只替换最后一步（_popen），不替换 _spawn ——
           因为启动参数是在 _spawn 里组装的，替换了整个 _spawn
           就等于把要测的代码一起跳过了（这个坑见 conftest 里的说明）。
        """
        captured = {}

        class _FakeProc:
            pid = 777

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env") or {}
            captured["cwd"] = kwargs.get("cwd")
            # 顺手把日志文件的句柄关掉，否则 Windows 上文件会被占住
            stream = kwargs.get("stdout")
            if hasattr(stream, "close"):
                try:
                    stream.close()
                except OSError:
                    pass
            return _FakeProc()

        monkeypatch.setattr(floating_service, "_popen", fake_popen)
        return captured

    def test_token_is_passed_to_child(self, monkeypatch, client):
        r"""★★ 核心断言：子进程的环境变量里必须有令牌。"""
        captured = self._capture(monkeypatch, client)
        floating_service._spawn()

        token = floating_repo.get_token()
        assert token, "服务端应该已经自动生成了令牌"
        assert captured["env"].get("TODO_FLOAT_TOKEN") == token

    def test_address_is_passed_to_child(self, monkeypatch, client):
        """服务的地址和端口也要传过去 —— 否则子进程会去连默认的 8000 端口。"""
        captured = self._capture(monkeypatch, client)
        floating_service._spawn()
        assert captured["env"]["TODO_PORT"] == str(config.PORT)
        assert captured["env"]["TODO_HOST"] == config.HOST
        assert captured["env"]["TODO_DATA_DIR"] == str(config.DATA_DIR)

    def test_child_is_started_with_floating_flag(self, monkeypatch, client):
        r"""命令行里必须带 --floating。

        不带的话，子进程会把自己当成"第二个服务"去抢 8000 端口 ——
        直接因为端口被占用而崩掉（用户看到的是"窗口打不开"）。
        """
        captured = self._capture(monkeypatch, client)
        floating_service._spawn()
        assert "--floating" in captured["cmd"]

    def test_child_starts_in_project_dir(self, monkeypatch, client):
        """工作目录要对，否则子进程 import app 会找不到。"""
        captured = self._capture(monkeypatch, client)
        floating_service._spawn()
        assert captured["cwd"] == str(config.BASE_DIR)

    def test_token_is_stable_across_calls(self, client):
        r"""令牌不能每次调用都重新生成 —— 否则重启窗口时新旧令牌对不上。"""
        first = floating_repo.get_token()
        second = floating_repo.get_token()
        assert first == second

    def test_token_gate_matches_generated_token(self, client):
        r"""★ 端到端半程验证：服务端生成的那个令牌，接口真的认。

        这条把"生成令牌"和"校验令牌"两个地方串起来 ——
        如果哪天有人改了令牌的存储方式（比如加了前后缀），
        但只改了其中一处，这里会立刻失败。
        """
        token = floating_repo.get_token()
        r = client.get("/api/floating/data", headers={"X-Float-Token": token})
        assert r.status_code == 200
