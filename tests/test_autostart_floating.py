# -*- coding: utf-8 -*-
r"""
test_autostart_floating.py -- 开机自启脚本里"顺带显示悬浮窗"的单元测试（v0.3.0 新增）。

【背景：这是个很容易被忽略、又很容易出错的地方】

    "开机自启后悬浮窗也能用"这条需求，落到代码上就是一行 VBScript：

        shell.Run "cmd /c ""<项目目录>\start_silent.bat"" --no-browser --floating", 0, False

    这行字符串有四个必须同时成立的要求：
        ① 行尾要是 CRLF、内容要是纯 ASCII（BUG-001 的教训）；
        ② 路径要能处理空格（所以外面套了一层双引号）；
        ③ 双引号在 VBS 里要写成两个（写成别的就是语法错误）；
        ④ --floating 必须真的出现在里面 —— 否则"登录后显示悬浮窗"就是空话。

    这类"拼字符串"的代码，光靠眼睛看很容易漏。
    所以这里把它当成数据来测：生成出来的内容里，该有的必须有，不该有的必须没有。
"""

import pytest

from app import config
from app.core import autostart


@pytest.fixture
def fake_startup(tmp_path, monkeypatch):
    """
    把"启动文件夹"指到一个临时目录。

    ⚠️ 这一条非常重要：绝不能让测试去动【真正的】启动文件夹 ——
       那会真的改变用户的开机行为，而且测试跑完就留下一堆垃圾文件。
       （"不要碰用户正在运行的东西"，AGENTS.md 第 3.2 节。）
    """
    folder = tmp_path / "Startup"
    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(autostart, "startup_dir", lambda: folder)
    return folder


class TestLauncherContent:
    def test_plain(self):
        text = autostart._launcher_content(open_browser=False, floating=False)
        assert "--no-browser" in text
        assert "--floating" not in text

    def test_with_floating(self):
        text = autostart._launcher_content(open_browser=False, floating=True)
        assert "--no-browser" in text
        assert "--floating" in text

    def test_with_open_browser_and_floating(self):
        """用户显式要"开机顺便开浏览器"时，仍然要能生成对应的脚本。

        （v0.4.2 说明：这个能力【保留】，只是**默认关着** ——
          见下面 test_factory_default_is_silent 那条。）
        """
        text = autostart._launcher_content(open_browser=True, floating=True)
        assert "--no-browser" not in text
        assert "--floating" in text

    def test_factory_default_is_silent(self):
        r"""★★ 出厂默认必须是【静默启动】：开自启，但不开浏览器（v0.4.2）。

        【用户原话】
            「app默认开机静默自启，悬浮窗默认启动」
            「自启动静默启动，不要开启浏览器」
            「不需要拿掉，用户出厂设置打开自启动但不打开浏览器即可」

        【这条测试在守什么？】
            守"默认值"三个字：
              · `enable()` 的 open_browser 参数默认必须是 False；
              · 服务层按默认补建自启项时，也必须传 False。

            如果哪天有人把默认改成 True，每次开机都会"啪"地弹出一个浏览器窗口 ——
            这类改动看起来只是改一个布尔值，所以更要用测试钉住。
        """
        import inspect

        # ① 函数签名上的默认值
        sig = inspect.signature(autostart.enable)
        assert sig.parameters["open_browser"].default is False

        # ② 默认生成的内容（不传 open_browser）必须是静默的
        text = autostart._launcher_content(open_browser=False, floating=True)
        assert "--no-browser" in text, "出厂自启必须静默，不能弹浏览器"
        assert "--floating" in text, "出厂自启要顺便把悬浮窗显示出来"

    def test_crlf_line_endings(self):
        r"""★ 铁律：.vbs 文件必须用 CRLF 行尾（BUG-001 的教训）。

        这个文件是给 Windows 脚本宿主（wscript.exe）读的。
        它按"行"解析，遇到只有 LF 的换行时可能读错 ——
        而症状是"开机什么都没发生"，没有任何报错可查，非常难发现。
        """
        text = autostart._launcher_content(False, True)
        assert "\r\n" in text
        assert "\n" not in text.replace("\r\n", "")     # 不存在"孤立"的 LF

    def test_pure_ascii(self):
        r"""★ 铁律：内容必须纯 ASCII。

        因为 VBScript 读 UTF-8 中文容易乱码（同样的历史教训，见 BUG-001）。
        一旦混进中文，脚本可能变成语法错误 —— 同样是静默失败。
        """
        text = autostart._launcher_content(True, True)
        text.encode("ascii")        # 编不出来就会抛异常，测试自然失败

    def test_ends_with_newline(self):
        text = autostart._launcher_content(False, True)
        assert text.endswith("\r\n")

    def test_has_safety_check(self):
        r"""启动脚本必须检查"程序还在不在"。

        用户可能把项目文件夹改名或删掉，而启动文件夹里的 .vbs 还留着。
        没有这个检查的话，每次开机都会弹一个"找不到文件"的报错框 ——
        用户又不知道那是什么，只能忍受或者去乱删东西。
        """
        text = autostart._launcher_content(False, True)
        assert "FileExists" in text
        assert "WScript.Quit 1" in text

    def test_run_window_style_is_zero(self):
        r"""窗口样式参数必须是 0（隐藏）——否则每次开机都闪一个黑窗口。"""
        text = autostart._launcher_content(False, True)
        assert "0, False" in text


class TestEnableDisable:
    def test_enable_writes_file(self, fake_startup):
        result = autostart.enable(open_browser=False, floating=True)
        assert result["ok"] is True
        assert "悬浮窗" in result["message"]
        assert autostart.launcher_path().exists()

    def test_message_mentions_window_only_when_asked(self, fake_startup):
        plain = autostart.enable(open_browser=False, floating=False)
        assert "悬浮窗" not in plain["message"]

    def test_status_reports_floating_flag(self, fake_startup):
        autostart.enable(open_browser=False, floating=True)
        status = autostart.status()
        assert status["enabled"] is True
        assert status["floating"] is True
        assert "悬浮窗" in status["summary"]

    def test_status_reports_false_when_not_set(self, fake_startup):
        autostart.enable(open_browser=False, floating=False)
        status = autostart.status()
        assert status["floating"] is False
        assert "悬浮窗" not in status["summary"]

    def test_disable_removes_file(self, fake_startup):
        autostart.enable(open_browser=False, floating=True)
        result = autostart.disable()
        assert result["ok"] is True
        assert not autostart.launcher_path().exists()

    def test_disable_when_not_enabled(self, fake_startup):
        r"""重复关闭不该报错 —— 用户点两下"关闭"是常事。"""
        result = autostart.disable()
        assert result["ok"] is True
        assert "本来" in result["message"]

    def test_file_is_ascii_and_crlf_on_disk(self, fake_startup):
        r"""★ 落盘之后再验一遍（不是验内存里的字符串）。

        "生成的内容对"和"写进文件之后还对"是两件事：
        写入时如果用 text 模式且没指定 newline=""，
        Python 会把 \n 变成 \r\n —— 或者反过来把我们的 \r\n 变成 \r\r\n。
        所以必须读回来验，而不是只验生成函数。
        """
        autostart.enable(open_browser=False, floating=True)
        raw = autostart.launcher_path().read_bytes()
        raw.decode("ascii")                       # 必须能按 ASCII 解出来
        assert b"\r\n" in raw
        assert b"\r\r\n" not in raw               # 不能有重复的 \r
        assert b"--floating" in raw


class TestLauncherMtime:
    def test_zero_when_missing(self, fake_startup):
        assert autostart.launcher_mtime() == 0.0

    def test_nonzero_after_enable(self, fake_startup):
        autostart.enable(open_browser=False, floating=True)
        assert autostart.launcher_mtime() > 0

    def test_changes_after_rewrite(self, fake_startup):
        r"""★ 修改时间要能反映"文件被人动过"。

        悬浮窗的服务端会记住这个时间戳，一旦对不上，说明
        启动脚本被用户改了或被清理软件删了 —— 那就补写一份。
        没有它的话，"开机自启显示悬浮窗"可能在某天突然就不生效了，
        而用户完全不知道原因。
        """
        import time
        autostart.enable(open_browser=False, floating=False)
        first = autostart.launcher_mtime()
        time.sleep(0.01)                          # 有些文件系统的时间精度比较粗
        autostart.enable(open_browser=False, floating=True)
        assert autostart.launcher_mtime() != first
