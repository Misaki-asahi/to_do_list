# -*- coding: utf-8 -*-
r"""
test_gui_utils.py -- 悬浮窗界面里的"纯函数"单元测试（v0.3.0 新增）。

【这里测什么、不测什么】

    测：不依赖窗口就能算出来的东西 —— 颜色换算、对比色、混色、可滚动容器以外的逻辑。
    不测：窗口长什么样、拖动跟不跟手 —— 那些只能靠人眼看着确认
          （tkinter 没有像样的自动化测试手段，硬测得不偿失）。

【为什么"纯函数"值得单独测？】

    因为它们是【会悄悄出错、而且不明显】的那一类：
        背景调成浅色时，如果对比色算错了，文字就变成"白底白字" ——
        程序不报任何错，用户只看到一片空白，还以为是程序卡了。
    这种"看得见但说不清"的 Bug，最适合用单元测试钉死。
"""

import pytest

from app.gui import window as gui


class TestHexToRgb:
    def test_basic(self):
        assert gui._hex_to_rgb("#000000") == (0, 0, 0)
        assert gui._hex_to_rgb("#FFFFFF") == (255, 255, 255)
        assert gui._hex_to_rgb("#2F6FED") == (47, 111, 237)

    def test_case_insensitive(self):
        assert gui._hex_to_rgb("#abcdef") == gui._hex_to_rgb("#ABCDEF")

    def test_garbage_does_not_crash(self):
        r"""★ 坏值不能让程序崩。

        这个函数在画界面时被调用。如果它抛异常，
        整个窗口就画不出来 —— 而配置值是用户能输的，
        一个手滑的错字不该让窗口彻底打不开。
        """
        assert gui._hex_to_rgb("") == (0, 0, 0)
        assert gui._hex_to_rgb("red") == (0, 0, 0)
        assert gui._hex_to_rgb("#12345") == (0, 0, 0)
        assert gui._hex_to_rgb(None) == (0, 0, 0)


class TestNormalizeDatetime:
    r"""
    ★ 用户在编辑框里手打时间的"体贴补全"（v0.3.1 新增）。

    【为什么要有这组测试？】
        因为他最自然的改时间动作是【只改时间那一半】：
        想把 18:00 的截止时间改成 20:00，他会把 "18:00" 删掉打上 "20:00"，
        而不是把 "2026-09-14 18:00" 整串重新敲一遍。
        如果这里补全做错了，用户会遇到"改个时间都不行"（接口报格式错误），
        而错误信息指向的却是"格式"，跟他的操作对不上，非常困惑。
    """

    def test_empty_stays_empty(self):
        assert gui.normalize_datetime("") == ""
        assert gui.normalize_datetime(None) == ""
        assert gui.normalize_datetime("   ") == ""

    def test_full_datetime_untouched(self):
        assert gui.normalize_datetime("2026-09-20 08:30") == "2026-09-20 08:30"

    def test_only_time_gets_todays_date(self):
        r"""只打 "23:59" -> 补成今天的 23:59（如果还没到）。"""
        from datetime import datetime

        result = gui.normalize_datetime("23:59")
        assert len(result) == 16, result
        assert result.endswith("23:59")
        # 必须是"今天"或"明天"（见下面那条测试解释为什么可能顺延）
        today = datetime.now().strftime("%Y-%m-%d")
        assert result.startswith(today) or result[:10] > today

    def test_only_time_in_the_past_rolls_to_tomorrow(self):
        r"""★ 打了一个"今天已经过了"的时间 -> 自动顺延到明天。

        否则接口会以"截止时间不能早于当前时间"拒绝，
        而用户明明只是想把时间设成"今晚那个点"，反复点却怎么都加不上。
        """
        from datetime import datetime, timedelta

        past = (datetime.now() - timedelta(hours=1)).strftime("%H:%M")
        result = gui.normalize_datetime(past)
        future = datetime.now() + timedelta(minutes=5)
        assert result[:10] >= future.strftime("%Y-%m-%d"), result

    def test_month_day_gets_current_year(self):
        from datetime import datetime

        result = gui.normalize_datetime("9-16 09:00")
        assert result == "%d-09-16 09:00" % datetime.now().year, result

    def test_date_only_gets_midnight(self):
        assert gui.normalize_datetime("2026-09-16") == "2026-09-16 00:00"

    def test_garbage_passes_through(self):
        r"""★ 真认不出来的东西原样返回，别在这里瞎猜。

        这样服务端才能给出准确的报错（"看不懂这个时间：xxx"），
        而不是我们"猜"出一个用户没想要的时间、然后默默存进去。

        【★ v0.4.3：这个用例里原来还列着这些东西】
            "下周三"、"2026/09/16"、"9月16日"
        它们当时被当成"垃圾" —— 而用户后来说：

            「放宽日期格式，如：中英文皆可，以用户当前系统时间为准」

        所以它们现在**都是合法输入**了（见 test_flexible_formats_* 那几个用例）。
        这条测试只留真正无法理解的东西。
        """
        for bad in ("瞎写的东东", "25:00", "2026-13-45", "third"):
            assert gui.normalize_datetime(bad) == bad

    def test_flexible_formats_are_understood(self):
        r"""★★ 放宽后的日期格式（v0.4.3，用户点名要的）。

        用户原话：「放宽日期格式，如：中英文皆可，
                  以用户当前系统时间为准，允许输入"某某分钟后"提醒」

        这些串以前会被原样退回、让服务端报错（而服务端一报错，
        整条待办就存不进去）—— 现在都认得出来。
        """
        import re
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        # 中英文 + 各种分隔符：都要能被翻译成规范格式（16 个字符、带日期）
        for text in ("2026/09/16 15:00", "2026.09.16 15:00", "2026年9月16日 15:00",
                     "9月16日 15:00", "Sep 16 15:00", "9-16 15:00"):
            got = gui.normalize_datetime(text)
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", got), (text, got)
            assert got == "2026-09-16 15:00", (text, got)

        # 相对时间：以【当前系统时间】为基准（结果随运行时刻变化，只断言形状和"在未来"）
        for text in ("30分钟后", "半小时后", "2小时后", "in 30 minutes",
                     "30 minutes later", "1天后"):
            got = gui.normalize_datetime(text)
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", got), (text, got)
            assert got >= today, (text, got)          # 不能算出过去的时间

        # 日期词
        for text in ("明天", "tomorrow", "下周三", "今晚"):
            got = gui.normalize_datetime(text)
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", got), (text, got)
            assert got[:10] >= today, (text, got)

        # 只写时刻：今天还没到就用今天，已经过了就顺延到明天（老行为，别改坏）
        assert gui.normalize_datetime("15:00").endswith("15:00")
        assert gui.normalize_datetime("下午3点").endswith("15:00")


class TestRelayoutOnResize:
    r"""
    ★ 用户要求：「调整大小后里面内容排版相应改变」。

    实现方式是给每个任务行的 Label 重设 wraplength（见 _reflow）。
    这里用"假 label / 假行"验证宽度会被重算 ——
    不需要真的开窗口，因为 _reflow 只碰 label.configure。

    ★★ 【v0.4.4 改过算法】老公式是"窗口宽 - 80"（一个拍脑袋的余量），
       它漏算了右边那列（时间 + 优先级箭头）的真实宽度 ——
       实测窗口 320 宽、字号 10 磅时，标题只分到 177 像素，
       而 wraplength 被设成 240，于是文字不折行、右边几个字被硬裁
       （BUG-046 的同类没修干净）。
       现在改成"按这一行真正被别的控件占掉多少"来算，
       所以下面的断言也跟着变，并新增一条把"右边很宽"的场景钉住。
    """

    class _FakeLabel:
        def __init__(self, master=None, reqwidth=0):
            self.kwargs = {}
            self.master = master
            self._reqwidth = reqwidth

        def configure(self, **kwargs):
            self.kwargs.update(kwargs)

        def winfo_reqwidth(self):
            return self._reqwidth

        def winfo_children(self):
            # _reflow 现在会递归问"子控件要多宽"（Frame 的 reqwidth 不可靠），
            # 所以假的 Label 也要答得上"我没有子控件"。
            return []

    class _FakeRow:
        def __init__(self, children, width):
            self._children = children
            self._width = width

        def winfo_children(self):
            return self._children

        def winfo_width(self):
            return self._width

    def _window_with_rows(self, count=3, used=0, row_width=0):
        labels = []
        for _ in range(count):
            label = TestRelayoutOnResize._FakeLabel()
            siblings = ([TestRelayoutOnResize._FakeLabel(reqwidth=used)]
                        if used else [])
            row = TestRelayoutOnResize._FakeRow([label] + siblings, row_width)
            label.master = row
            labels.append(label)

        class _Fake:
            _task_rows = labels
            _reflow = gui.FloatingWindow._reflow

        return _Fake()

    def test_wrap_length_follows_width(self):
        window = self._window_with_rows()
        window._reflow(400)
        for label in window._task_rows:
            assert label.kwargs["wraplength"] == 400 - 20

    def test_wrap_length_updates_on_shrink(self):
        r"""★ 变窄之后要重新折行，否则右边几个字看不见。"""
        window = self._window_with_rows()
        window._reflow(400)
        window._reflow(220)
        for label in window._task_rows:
            assert label.kwargs["wraplength"] == 220 - 20

    def test_wrap_length_leaves_room_for_the_right_column(self):
        r"""★★ 新增（v0.4.4）：右边那列占掉多少，标题就要让出多少。

        这就是实测到的那个缺陷：右边有"今天 9-15"和一个 ↑ 箭头时，
        标题只剩不到 180 像素，而 wraplength 还是按"窗口宽 - 80"算的，
        于是文字不折行、右边直接被裁。
        """
        window = self._window_with_rows(1, used=120)
        window._reflow(320)
        assert window._task_rows[0].kwargs["wraplength"] == 320 - 120 - 20, (
            "折行宽度必须减掉右边那一列真正占用的宽度")

    def test_wrap_length_has_floor(self):
        r"""★ 窗口极窄时也要留一个下限。

        不留下限的话，wraplength 会变成 0 或负数 ——
        tkinter 会把它当成"不折行"，于是文字直接冲出窗口。
        """
        window = self._window_with_rows(1)
        window._reflow(50)
        assert window._task_rows[0].kwargs["wraplength"] >= 80


class TestResizeEdges:
    r"""
    ★ 用户要求：「鼠标位于悬浮窗边缘即可移动与调整悬浮窗大小」。

    边缘感应区是靠 place(anchor=...) 摆的，anchor 算错会让感应条
    跑到窗口外面去（表现为"鼠标要移到窗口外一点才能拖"，很别扭）。
    """

    def test_all_eight_edges_exist(self):
        assert set(gui.FloatingWindow._anchor_for("n") for _ in [0]) == {"nw"}
        for edge in ("n", "s", "e", "w", "nw", "ne", "sw", "se"):
            assert gui.FloatingWindow._anchor_for(edge)

    def test_anchor_mapping(self):
        r"""每条边该用哪个角当锚点（错了感应条就会偏出去）。"""
        assert gui.FloatingWindow._anchor_for("n") == "nw"
        assert gui.FloatingWindow._anchor_for("s") == "sw"
        assert gui.FloatingWindow._anchor_for("e") == "ne"
        assert gui.FloatingWindow._anchor_for("w") == "nw"
        assert gui.FloatingWindow._anchor_for("se") == "se"
        assert gui.FloatingWindow._anchor_for("ne") == "ne"

    def test_edge_grip_is_clickable_size(self):
        r"""感应区不能太窄（点不中），也不能太宽（会挡住内容里的点击）。"""
        assert 3 <= gui.EDGE_GRIP <= 12


class TestTimeHelpers:
    r"""
    ★★ 测试辅助函数的单元测试（BUG-032 的防复发）。

    【为什么"测试的辅助函数"也值得测？】
        因为 BUG-032 就出在这里：`conftest.future_due()` 之前是
        "日期动态、时刻写死"，导致某些测试**只在一天的某个时段通过**。
        而"只在某个时段通过"的测试最糟 —— 它会训练你忽略红色。

        更麻烦的是：**这种缺陷没法靠"跑一遍测试"发现**，
        因为跑的时候时钟是固定的。所以必须**把"现在几点"注入进去**，
        用一个循环把所有时段都覆盖一遍。

        这正是给 `future_due(now=...)` 留那个参数的原因：
        **为了让时间相关的逻辑可测，必须能注入时间。**
    """

    def _parse(self, text):
        from datetime import datetime
        return datetime.strptime(text, "%Y-%m-%d %H:%M")

    def test_future_due_is_always_in_the_future(self):
        r"""遍历一天里的各个时刻，算出来的时间**必须严格在未来**。"""
        from datetime import datetime

        from tests.conftest import future_due

        for hour in range(24):
            for minute in (0, 15, 30, 45, 59):
                now = datetime(2026, 9, 14, hour, minute)
                due, which = future_due(now=now)
                assert self._parse(due) > now, (
                    "%02d:%02d 算出来的 %s 不在未来（which=%s）"
                    % (hour, minute, due, which))

    def test_future_due_keeps_today_when_possible(self):
        r"""白天要尽量保住"今天"这个语义（否则分组断言会全线失效）。"""
        from datetime import datetime

        from tests.conftest import future_due

        for hour in (0, 6, 12, 18, 21, 22):
            now = datetime(2026, 9, 14, hour, 0)
            due, which = future_due(now=now)
            assert which == "today", "%02d:00 不该顺延到明天" % hour

    def test_future_due_rolls_to_tomorrow_when_too_late(self):
        r"""22:30 之后必须顺延到明天 —— 否则今天已经没有合法时刻了。"""
        from datetime import datetime

        from tests.conftest import future_due

        for hour, minute in ((22, 40), (23, 0), (23, 30), (23, 59)):
            now = datetime(2026, 9, 14, hour, minute)
            due, which = future_due(now=now)
            assert which == "tomorrow", "%02d:%02d 应该顺延到明天" % (hour, minute)
            assert due.startswith("2026-09-15"), due

    def test_future_due_today_is_in_the_future(self):
        from datetime import datetime

        from tests.conftest import future_due_today

        for hour in range(0, 23):
            now = datetime(2026, 9, 14, hour, 30)
            due = future_due_today(now=now)
            assert due.startswith("2026-09-14"), due
            assert self._parse(due) > now

    def test_future_due_today_raises_near_midnight(self):
        r"""★ 23:56 之后必须**抛异常**（让调用方诚实地 skip）。

        为什么不是"返回一个过去的时间然后让它失败"？
        因为那样又会变回"只在深夜红一次"的坏测试。
        宁可明确地 skip，也不要偶尔红 —— 见 BUG-032 的经验 1。
        """
        import pytest
        from datetime import datetime

        from tests.conftest import future_due_today

        for hour, minute in ((23, 56), (23, 57), (23, 59)):
            with pytest.raises(RuntimeError):
                future_due_today(now=datetime(2026, 9, 14, hour, minute))

    def test_future_due_today_boundary_is_safe(self):
        """23:55 还应该能用（边界不能画错，否则平白少掉一分钟的可用时间）。"""
        from datetime import datetime

        from tests.conftest import future_due_today

        due = future_due_today(now=datetime(2026, 9, 14, 23, 55))
        assert due == "2026-09-14 23:59"


class TestContrastText:
    def test_light_background_gets_black_text(self):
        assert gui.contrast_text("#FFFFFF") == "#000000"
        assert gui.contrast_text("#E8F5E9") == "#000000"     # 护眼绿

    def test_dark_background_gets_white_text(self):
        assert gui.contrast_text("#000000") == "#FFFFFF"
        assert gui.contrast_text("#16181D") == "#FFFFFF"     # 墨黑主题

    def test_all_themes_are_readable(self):
        r"""★ 遍历所有内置主题，保证"文字都看得清"。

        这条测试的价值：以后有人加了一个"深紫色"主题，
        如果它恰好落在对比色的分界上、导致字看不清，
        这里会立刻失败 —— 而不用等到用户截图来问"为什么是糊的"。
        """
        from app import config

        for name, theme in config.FLOATING_THEMES.items():
            color = gui.contrast_text(theme["bg"])
            assert color in ("#000000", "#FFFFFF"), name

            # 再看"指定的文字色"和"背景色"的亮度差是否够大
            bg_lum = _luminance(theme["bg"])
            fg_lum = _luminance(theme["fg"])
            assert abs(bg_lum - fg_lum) > 0.3, "主题 %s 的文字和背景太接近了" % name


def _luminance(color: str) -> float:
    """自己算一遍亮度（故意不用被测函数，避免"用错的尺子量自己"）。"""
    r, g, b = gui._hex_to_rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


class TestMix:
    def test_mix_with_self_is_self(self):
        assert gui.mix("#123456", "#123456") == "#123456"

    def test_mix_extremes(self):
        assert gui.mix("#000000", "#FFFFFF", 0.0) == "#000000"
        assert gui.mix("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
        assert gui.mix("#000000", "#FFFFFF", 0.5) == "#808080"

    def test_result_is_always_valid_hex(self):
        r"""★ 混出来的颜色必须是合法的 #RRGGBB。

        因为它的结果会被直接喂给 tkinter 当颜色用 ——
        格式错了就是"窗口创建失败"，用户只看到窗口一闪而过。
        所以这个函数必须保证输出格式永远合法。
        """
        import re
        for ratio in (0.0, 0.06, 0.1, 0.25, 0.45, 0.5, 0.99, 1.0):
            color = gui.mix("#1F2933", "#FFFFFF", ratio)
            assert re.fullmatch(r"#[0-9A-Fa-f]{6}", color), (ratio, color)


class TestClientIsTheOnlyExit:
    def test_window_does_not_import_urllib_directly(self):
        r"""
        ★ 架构约束的自动化检查：窗口代码里不许自己写网络请求。

        项目约定是"网络请求只能有一个出口"：
            网页  -> app/static/js/api.js
            悬浮窗 -> app/gui/client.py
        如果哪天有人图省事在 window.py 里直接 urlopen(...)，
        那条约束就悄悄破了 —— 而它破了不会有任何报错，
        只会在"以后要改服务地址"时才发现有两个地方要改。

        这种"约定"最好的守护方式就是让测试来读源码。
        同样是"读源码"的思路，本项目还用过一次抓前端 Bug（OPT-017）。
        """
        from pathlib import Path

        source = Path(gui.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib.request", "urlopen", "requests.get", "requests.post"):
            assert forbidden not in source, (
                "window.py 里出现了 %s —— 网络请求必须走 app/gui/client.py" % forbidden)

    def test_client_does_not_touch_database(self):
        r"""
        ★ 悬浮窗进程绝不允许直接碰数据库。

        它跑在另一个进程里，直连 SQLite 会有两个后果：
            ① 两个进程同时写库，可能撞上 "database is locked"；
            ② 数据逻辑（models / repositories）就要在两个进程里各维护一份。

        这条测试守着第一道门；第二道门是"窗口进程不 import repositories"。
        """
        from pathlib import Path

        from app.gui import client

        source = Path(client.__file__).read_text(encoding="utf-8")
        for forbidden in ("from app.repositories", "from app.models",
                          "sqlalchemy", "get_session"):
            assert forbidden not in source, (
                "client.py 里出现了 %s —— 窗口进程只能通过 HTTP 取数据" % forbidden)


class TestWindowModuleLoads:
    def test_tkinter_available_in_this_environment(self):
        r"""
        本机（开发环境）应该能 import tkinter。

        这条测试在"装了 Python 但没装 tkinter"的机器上会失败 —— 那就对了：
        Windows 官方 Python 自带 tkinter，缺了它说明环境不规范，
        与其等到用户点"开启悬浮窗"才发现，不如在测试阶段就暴露。
        （而且它失败时给的是"tkinter 缺失"这个明确信号，不用猜。）
        """
        assert gui.tk is not None

    def test_module_has_expected_entrypoint(self):
        assert callable(gui.main)


class TestConfigChangeDetection:
    r"""
    ★ 这一组测试守着 BUG-024（"改了窗口大小不生效"）。

    【那个 Bug 是怎么回事？】
        窗口每 3 秒问一次服务端"设置变了没"，判断依据是一个字段清单。
        当时那个清单里【漏了 width / height】。
        结果：用户改了窗口尺寸 -> 清单比对认为"什么都没变" ->
        窗口既不重建也不调整外框 -> 改动石沉大海。

        更难受的是它的伪装：改透明度是生效的（在清单里），
        所以用户会得出"这软件有时候灵有时候不灵"的结论，
        而不会想到"是尺寸这个字段没被监听"。

    【为什么能不启动窗口就测它？】
        因为这两个判断函数只读 self.config_data 这个字典，
        不碰任何 tkinter 对象。所以拿一个"假 self"就能测。
        —— 这也是"把纯逻辑和界面操作分开"带来的直接好处。
    """

    class _Fake:
        """一个只为调用这两个方法而存在的最小对象。"""
        config_data = {}

    @classmethod
    def _call(cls, method, old, new):
        fake = cls._Fake()
        fake.config_data = dict(old)
        return getattr(gui.FloatingWindow, method)(fake, new)

    @pytest.mark.parametrize("field,old,new", [
        ("width", 320, 500),
        ("height", 420, 600),
        ("opacity", 0.3, 0.9),
        ("always_on_top", True, False),
    ])
    def test_geometry_changes_are_detected(self, field, old, new):
        r"""★ 位置/大小/透明度/置顶 的变化必须被认出来。"""
        assert self._call("_geometry_changed", {field: old}, {field: new}) is True

    @pytest.mark.parametrize("field,old,new", [
        ("bg_color", "#000000", "#FFFFFF"),
        ("fg_color", "#FFFFFF", "#000000"),
        ("accent_color", "#FFD166", "#2F6FED"),
        ("font_family", "宋体", "黑体"),
        ("font_size", 10, 16),
    ])
    def test_appearance_changes_are_detected(self, field, old, new):
        r"""★ 配色和字体变化必须触发界面重建（不然颜色不会变）。"""
        assert self._call("_appearance_changed", {field: old}, {field: new}) is True

    def test_no_change_is_not_detected(self):
        r"""★ 反过来也要验：没变就别乱重建。

        这一条防的是"每次轮询都重建整个界面"——
        那样窗口会每 3 秒闪一次，而且用户拖滚动条会被打断。
        （这正是"指纹/差异比对"这套机制存在的意义。）
        """
        same = {"width": 320, "opacity": 0.3, "bg_color": "#000000",
                "font_family": "宋体", "font_size": 10}
        assert self._call("_geometry_changed", same, same) is False
        assert self._call("_appearance_changed", same, same) is False

    def test_x_y_changes_are_detected(self):
        r"""位置变化也要被认出来 —— 否则从设置页挪不动窗口。

        （顺带说明：窗口自己拖动时是【先改自己再存到服务端】，
          所以那条路径不依赖这个判断；这里守的是"别人改了位置"的情况，
          比如设置页提供了坐标、或者用户点了「恢复默认设置」。）
        """
        assert self._call("_geometry_changed", {"x": 80}, {"x": 600}) is True
        assert self._call("_geometry_changed", {"y": 80}, {"y": 300}) is True

    def test_first_call_never_counts_as_change(self):
        r"""窗口刚建好时还没有旧值，不该被判定成"变了"（否则会白重建一次）。"""
        assert self._call("_appearance_changed", {}, {"font_size": 12}) is False
        assert self._call("_geometry_changed", {}, {"width": 500}) is False

    def test_every_config_field_is_watched_by_something(self):
        r"""★★ 这条是"防复发"的关键：新增设置项必须被某个判断函数盯上。

        BUG-024 的本质不是"漏写了 width"，而是
        "漏写了也不会有人发现"。这条测试把这件事变成自动检查：
        凡是会影响窗口长相或外框的设置项，都必须至少被
        _appearance_changed 或 _geometry_changed 里的一个覆盖到。

        【怎么判断"会影响"？】用一个显式的清单列出来 ——
        新增设置时，如果它是纯行为（比如 group_by：只影响内容不影响外框），
        就把它加进 EXEMPT；如果它会改变外观，就必须加进判断函数。
        两种情况的处理都是"显式声明"，而不是"忘了就算"。
        """
        # 这些是"只影响内容、不影响窗口长相/外框"的设置，允许不被监听
        EXEMPT = {
            "enabled", "autostart", "visible", "show_completed",
            "highlight_reminders", "group_by", "scope", "data_version",
        }

        from app.repositories import floating_repo

        watched = set()
        # 从判断函数里把字段名读出来（源码即规格，避免维护两份清单）
        import inspect
        for method in ("_appearance_changed", "_geometry_changed"):
            source = inspect.getsource(getattr(gui.FloatingWindow, method))
            for name in floating_repo.FIELDS:
                if '"%s"' % name in source:
                    watched.add(name)

        missing = set(floating_repo.FIELDS) - watched - EXEMPT
        assert not missing, (
            "这些设置项改了窗口却不会响应，请把它们加进 "
            "_appearance_changed 或 _geometry_changed：%s" % sorted(missing))
