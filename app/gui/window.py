# -*- coding: utf-8 -*-
"""
window.py -- 桌面悬浮窗的界面（v0.3.0 → v0.3.1 按用户反馈重做）。

【它长什么样（v0.3.1）】

    ╔══════════════════════════════════════════╗ ← 拖这里 / 拖任意边缘移动
    ║ 📝 待办 3          [日期 ▾] ⟳ ＋ ⚙ ✕   ║ ← 上方：分类切换 + 编辑 + 设置
    ╟──────────────────────────────────────────╢
    ║ ▌已过期 9-11  1                          ║ ← 分组条：带 ★具体日期★
    ║   ☐ 交周报        9-11 ⚠                 ║
    ║ ▌今天 9-14  2                            ║
    ║   ☐ 买牛奶        18:00                  ║ ← 点整行 = 打开编辑弹窗
    ║   ☐ 交作业        ↑ ⏰25 分钟后提醒       ║
    ║ ▌七天内 9-16 ~ 9-20  1                   ║
    ║   ☐ 开会          9-16 09:00             ║
    ╚══════════════════════════════════════════╝ ← 内容放不下时滚轮下拉
              ◢                                  ← 右下角可以拖

【v0.3.1 根据用户反馈做的六处改动】

    ① 「悬浮窗要设于桌面，任何窗口都能覆盖」
       -> 默认【不置顶】（FLOATING_ALWAYS_ON_TOP = False）。
          它的定位从"浮在最上层的工具窗"改成"贴在桌面上的便签"。

    ② 「最下面部分编辑和分类排版有误且功能过于简单，两功能放于上方」
       -> 编辑入口和分类切换都搬到【顶部工具栏】；
          底部只留一个"快速添加"输入框。

    ③ 「分类默认按日期分，今天 明天 七天内 之后（要附带具体日期）」
       -> 分组标题变成「今天 9-14」「明天 9-15」「七天内 9-16 ~ 9-20」。
          档位：已过期 → 今天 → 明天 → 七天内 → 之后 → 未安排日期。

    ④ 「若一个界面足够则将这几个日期紧密排列 … 直至一个页面放不下，
        这时可以鼠标滚轮下拉」
       -> 行高、边距、分组条全部收紧（"紧密排列"）；
          滚动条做成【需要时才出现】，内容放得下时不占地方。

    ⑤ 「分类旁边设定一个三角点击即可更换分类类别」
       -> 顶部工具栏有一个「[日期 ▾]」按钮（三角就是 ▾），
          点一下弹出菜单：按日期 / 按重要度 / 按待办类型。

    ⑥ 「整个界面排版有点拥挤，字号默认小一点，不要占满」
       -> 默认字号 10 → 9 磅，默认透明度 30% → 50%（看得清又不糊）。

    ⑦ 「右上角要有设置按钮，可以调节字号 背景颜色 透明度 字体和字体颜色
        是否关闭悬浮窗」
       -> 右上角 ⚙ 打开【设置弹窗】，里面就是这几项 + 一个关闭按钮。

    ⑧ 「鼠标位于悬浮窗边缘即可移动与调整悬浮窗大小，
        调整大小后里面内容排版相应改变」
       -> 四条边 + 四个角都是感应区：光标会变成 ↔ / ↕ / ⤡，
          拖边缘改大小、拖角等比改大小、按住边缘空白处也能移动；
          改完大小后内容会重新折行（见 _reflow）。

【为什么用 tkinter 而不是"网页 + 无边框浏览器窗口"？】
    因为 tkinter 是 Python 标准库自带的：
        1. 不需要用户装任何东西，也不需要 Chrome/Edge 的 --app 参数；
        2. 它天生支持"无边框 + 总在最前 + 整窗透明度"这三件悬浮窗最核心的事；
        3. 它只在【用户开了这个功能】时才被启动（一个独立进程），
           不用它的人完全感觉不到它的存在。

【为什么要单独一个进程，而不是在服务进程里开个线程画窗口？】
    tkinter 的界面必须在【主线程】里跑 mainloop()，
    而服务进程的主线程被 uvicorn 占着。塞进子线程在 Windows 上会闪退。
    独立进程还顺带解决了两件事：
        · 窗口卡住不会拖累服务（服务还在发提醒）；
        · 服务重启时窗口可以不跟着死（用户感觉不到"重启"）。

【窗口怎么知道自己该开还是该关？】
    它每 3 秒问一次服务端"设置变了没"，同时看一个"停止标志文件"。
    服务端要关窗口时只是写个文件，窗口自己收拾好东西再退出 ——
    这样窗口位置、大小一定被保存下来，不会丢。
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import tkinter as tk
import tkinter.font as tkfont

from app import config
from app.core import logging_setup, timeutil
from app.gui.client import FloatingApi, FloatingApiError

logger = logging_setup.get_logger("floating.window")

# ---------------------------------------------------------------------------
# 界面尺寸常量
#
# 【为什么这些不放 app/config.py？】
#   项目约定"所有常量只写在 config.py"针对的是【业务/运行参数】
#   （端口、路径、提醒间隔……）。而下面这些是"某个窗口控件有多宽"这种
#   纯界面细节，它们只会被这一个文件用到，跟着界面代码放更好读。
#   判断标准：改它的人会不会需要同时改别的地方？会 -> 进 config.py。
# ---------------------------------------------------------------------------
EDGE_GRIP = 6          # 边缘感应区的宽度（像素）：鼠标进入这么窄的一条就能拖
# 顶部工具栏的高度。
# ⚠️【v0.4.1 起不再"写死"】（用户反馈"上边框过于拥挤、内容显示不全"）：
#   原来固定 22 像素，而 9 磅宋体的一个按钮实际需要 24 像素 ——
#   于是按钮上下各被切掉 1 像素。现在工具栏按内容自己长高，
#   这个常量只当"参考下限"，真正决定上下留白的是 TOOLBAR_PAD_Y。
TITLE_ROW_HEIGHT = 22
# 工具栏上下各留几像素空白（让内容不贴边）
# ★ v0.4.2：3 -> 6。用户反馈"上边标题部分留白需要放得更宽一些"。
TOOLBAR_PAD_Y = 6
GROUP_ROW_HEIGHT = 16  # 分组标题条的高度
MARGIN = 6             # 左右内边距

# 光标形状：告诉用户"这里可以拖"
CURSOR_MOVE = "fleur"      # 四向箭头（移动）
CURSOR_H = "sb_h_double_arrow"   # 左右箭头（改宽度）
CURSOR_V = "sb_v_double_arrow"   # 上下箭头（改高度）
CURSOR_DIAG = "size_nw_se"       # 斜向箭头（改大小）


# ===========================================================================
# 一、小工具
# ===========================================================================


def enable_dpi_awareness() -> str:
    r"""
    让进程"感知 DPI"。

    ★★★ 这是【字体清晰度】最关键的一步，值得仔细读 ★★★

    【不声明会发生什么？】
        在缩放 125% / 150% 的显示器上（现在的笔记本几乎都是），
        Windows 会把我们这种"没声明 DPI 感知"的老式程序：
            ① 先按 96 DPI 渲染出一个低分辨率的窗口位图；
            ② 再把这个位图【拉伸】到实际需要的尺寸。
        拉伸就是插值，插值就是模糊 ——
        **字看起来发虚、发毛、笔画糊在一起，全是这一步造成的。**

    【怎么修？】
        在【创建 Tk 窗口之前】调用 Win32 的 SetProcessDpiAwarenessContext，
        告诉 Windows："我自己会按真实 DPI 画，你别帮我拉伸"。
        之后 Tk 用的是屏幕的真实像素，ClearType 才能在最合适的像素点上渲染，
        笔画边缘就是锐的。

    【为什么必须"在创建窗口之前"？】
        因为 DPI 感知是【进程级】设置，Windows 在进程第一次创建窗口时
        就把这个属性记下来了。窗口已经建好之后再声明，对当前进程无效
        （会返回 ERROR_ACCESS_DENIED，代码里也如实标注了这一点）。

    【为什么全用 getattr 包起来，还写了这么多兜底？】
        因为这里要调的是**没有 Python 绑定**的 Win32 API，只能靠 ctypes。
        而不同的 Windows 版本能用的函数不一样：
            · Windows 10 1703+  : SetProcessDpiAwarenessContext（最好）
            · Windows 8.1 - 10  : SetProcessDpiAwareness（shcore.dll）
            · 更早 / 服务器版    : SetProcessDPIAware（user32.dll）
        三个逐个尝试，哪个能用用哪个。全部失败也不报错 ——
        大不了回到"稍微有点糊"的状态，绝不能让窗口开不出来。
        （"优雅降级"：一个只影响观感的功能，不该有能力让主功能失效。）

    返回一句说明（会给用户看，也写进日志），例如 "per-monitor-v2"。
    """
    if not sys.platform.startswith("win") or not config.FLOATING_DPI_AWARE:
        return "未启用"

    try:
        import ctypes
    except ImportError:
        return "没有 ctypes"

    # ---- ① 首选：Windows 10 1703+ 的"每显示器 DPI 感知 v2" ----
    #   为什么首选它？因为它支持"拖动窗口在不同 DPI 的显示器之间移动时
    #   自动重新按新 DPI 渲染" —— 多屏用户（比如笔记本 + 外接 4K）最受益。
    try:
        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError) as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("当前系统或环境不支持这个调用，降级处理：%s", exc, exc_info=True)


    # ---- ② 次选：Windows 8.1+ 的 shcore.dll ----
    try:
        shcore = ctypes.windll.shcore
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        if shcore.SetProcessDpiAwareness(2) == 0:      # S_OK
            return "per-monitor"
    except (AttributeError, OSError) as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("当前系统或环境不支持这个调用，降级处理：%s", exc, exc_info=True)


    # ---- ③ 兜底：Windows Vista+ 的 SetProcessDPIAware（系统级 DPI 感知）----
    #   它不如上面两个精确（一个进程只能有一个 DPI 值），
    #   但"不糊"这个最低目标能达成。
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError) as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("当前系统或环境不支持这个调用，降级处理：%s", exc, exc_info=True)


    return "不支持（可能已经声明过了）"


def _needed_width(widget) -> int:
    r"""
    算一个控件"自己需要多宽"（Frame 就递归累加它的子控件）。

    【为什么不能直接用 winfo_reqwidth()？】
        实测踩过：在同一轮布局里，一个刚建好、还没来得及重新计算几何的 Frame，
        winfo_reqwidth() 会返回旧值（常常是 1）。用它去算"右边那列占了多宽"，
        算出来的数会小得离谱 —— 于是折行宽度又设大了，标题照样被裁。
        子控件（Label）的 reqwidth 是当场就准的，所以递归问它们。

    本行的布局是这个形状（见 _render_task）：
        [☐] [标题]                    [截止时间 / ⏰ / ↑]
    右边的控件都是 pack(side="right")，横向排开 —— 所以"加起来"就是它占的宽度。
    """
    try:
        children = widget.winfo_children()
    except Exception as exc:                           # noqa: BLE001
        logger.debug("读子控件失败，按 0 计：%s", exc, exc_info=True)
        return 0
    if not children:
        try:
            return int(widget.winfo_reqwidth())
        except Exception as exc:                       # noqa: BLE001
            logger.debug("读宽度失败，按 0 计：%s", exc, exc_info=True)
            return 0
    return sum(_needed_width(child) for child in children)


def _hex_to_rgb(color: str) -> tuple:
    """把 "#1F2933" 变成 (31, 41, 51)。算"对比色"要用它。"""
    text = (color or "#000000").lstrip("#")
    if len(text) != 6:
        return (0, 0, 0)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def contrast_text(bg: str) -> str:
    """
    根据背景色给出"看得清"的文字颜色（黑或白）。

    【为什么要算这个？】
        用户可以把背景设成纯黑，也可以设成纯白。
        如果文字颜色是"跟着主题写死的"，那么换主题后很可能变成
        "深色字配深色底"—— 一片糊，用户以为程序坏了。
        用亮度公式自动挑黑或白，任何配色都不会看不清文字。
        （这是无障碍设计里很常见的一招，叫"对比度自适应"。）
    """
    r, g, b = _hex_to_rgb(bg)
    # 人眼对绿色最敏感、对蓝色最不敏感，所以加权系数不一样（国际标准公式）
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if luminance > 0.6 else "#FFFFFF"


def mix(color_a: str, color_b: str, ratio: float = 0.5) -> str:
    """
    把两个颜色按比例混合，用来算"比背景稍深一点"的标题栏底色。

    【为什么要混色而不是让用户选标题栏颜色？】
        少一个要调的东西 = 少一次"调坏了"的机会。
        标题栏只要"和背景有明显区分"就够了，机器算得比人准。
    """
    a = _hex_to_rgb(color_a)
    b = _hex_to_rgb(color_b)
    mixed = tuple(round(a[i] * (1 - ratio) + b[i] * ratio) for i in range(3))
    return "#%02X%02X%02X" % mixed


def normalize_datetime(text: str):
    """
    把用户手打的时间"尽量"整理成接口要的 `YYYY-MM-DD HH:MM`。

    【为什么需要它？】—— 这是从真实使用习惯出发的：
        用户想在编辑框里把截止时间从 18:00 改成 20:00 时，
        最自然的动作是删掉后面的时间、只打 "20:00"，
        而不是把 "2026-09-14 18:00" 这一整串重新敲一遍。
        但接口只认完整格式 —— 直接提交就会被拒绝（提示格式错误），
        用户会觉得"这软件怎么改个时间都不行"。

    支持的输入形式（其它一律原样返回，交给服务端去报错）：

        "20:00"        -> "今天 20:00"（今天这时刻已过则顺延到明天）
        "9-16 09:00"   -> "今年 9-16 09:00"
        "9-16"         -> "今年 9-16 00:00"
        "2026-09-16"   -> "2026-09-16 00:00"
        "2026-9-6 8:5" -> "2026-09-06 08:05"（顺手补零）

    ★★ 关于"回环校验"，这里有一条踩过的教训 ★★
        （见 BUG-028）第一版是靠 `len(...)` 和 `text[4] == "-"` 这类
        **位置判断**来分辨输入的，结果有三个毛病：
            ① "9-16 09:00" 的长度是 11，被误判成"完整时间"直接放行，
               而它根本不是规范格式（年份缺失）；
            ② "25:00" 这种非法时间被当成"只打了时间"，补成
               "2026-09-14 25:00" —— 一个看起来像时间、其实无法解析的字符串；
            ③ "2026-9-6" 补出来的月份没补零，仍然不是规范格式。
        现在改成用**正则精确匹配形状**，再用 datetime **回环校验**
        （解析出来再按标准格式格式化，和原字符串逐字符比对）——
        和 app/core/timeutil.py 里 parse() 用的是同一招。
        凡是"必须保证输出是唯一规范形式"的地方，这一招都适用。
    """
    r"""
    ★★【v0.4.3：实现换成了 timeutil.parse_flexible()，不再自己写一套】

        用户原话：「放宽日期格式，如：中英文皆可，以用户当前系统时间为准，
                  允许输入"某某分钟后"提醒」

        原来这里手写了 6 条正则，只认 "20:00" / "9-16 09:00" / "2026-09-16" 这几种，
        别的（"30分钟后"、"9月16日"、"Sep 16"、"3pm"）一律原样返回，
        服务端再报错 —— 而**报错就意味着整条待办存不进去**。

        现在这段时间解析只有**一个实现**：timeutil.parse_flexible()。
        理由和"所有时间转换集中在 timeutil.py"是同一条铁律（见该文件开头）：
        两处各写一套，早晚会不一致 —— 而这次的不一致已经让用户吃过亏了。

    【返回值约定（保持不变，调用方依赖它）】
        空输入        -> ""（空字符串，不是 None）
        认得出来      -> 规范格式 "YYYY-MM-DD HH:MM"
        认不出来      -> **原样返回**
                         （这样服务端能拿原串去生成"看不懂这个时间：xxx"的提示，
                           而不是我们瞎猜一个时间默默存进去）
    """
    text = (text or "").strip()
    if not text:
        return ""
    canonical = timeutil.parse_flexible(text)
    return canonical if canonical else text


# ===========================================================================
# 二、可滚动区域（tkinter 没有现成的，得自己拿 Canvas 搭）
# ===========================================================================


class ScrollFrame(tk.Frame):
    """
    一个"内容比窗口高时能滚动"的容器。

    【tkinter 的坑，务必知道】
        tkinter 里【没有】滚动容器这个东西。标准做法是：
            ① 放一个 Canvas（画布）；
            ② 在画布里"嵌入"一个 Frame 当内容区；
            ③ 放一根 Scrollbar，把它和画布的滚动位置绑起来；
            ④ 内容大小变化时，告诉画布"内容有多高"（这步最容易漏！）。

        第 ④ 步漏了会怎样？滚不动，而且看不出为什么 ——
        画布不知道内容有多高，就认为"没什么可滚的"。

    【v0.3.1 的改进：滚动条"需要时才出现"】
        用户反馈「若一个界面足够则将这几个日期紧密排列」——
        也就是说内容少的时候不该有一根用不上的灰条占地方。
        所以下面每次内容或尺寸变化时都会重新判断：
        内容高度 > 可视高度 才 pack 滚动条，否则 pack_forget。

    还有两个 Windows 特有的细节：
        · 鼠标滚轮事件是 <MouseWheel>，delta 是 120 的倍数，要除以它；
        · 得把滚轮绑到每一个子控件上，否则鼠标停在标签上时滚不动。
          这里用 bind_all 一次性绑到整个应用上，简单可靠。
    """

    def __init__(self, master, bg: str, on_resize=None, **kwargs):
        super().__init__(master, bg=bg, **kwargs)
        # 画布宽度变化时的回调（窗口用它重算文字折行宽度，见 _on_canvas_resize）
        self._on_resize = on_resize
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical",
                                      command=self.canvas.yview)
        self._scrollbar_visible = False

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window_id = self.canvas.create_window((0, 0), window=self.inner,
                                                    anchor="nw")

        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        # 先只 pack 画布；滚动条由 _update_scrollbar() 按需 pack。
        #
        # ⚠️【重新 pack 时一定要带 before=self.canvas（BUG-036 的教训）】
        #   pack 是"按顺序分地盘"：先 pack 的控件先占，后来者只能捡剩下的。
        #   画布带 expand=True，一旦它先占了，整个容器就被它吃光 ——
        #   后 pack 的滚动条只能分到 1×1，于是出现
        #   "代码里明明 pack 了、代码里 _scrollbar_visible=True，
        #     用户却根本看不见滚动条"这种怪事。
        #   所以"显示滚动条"那一步必须插到画布【前面】去。
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    def _on_scroll_set(self, first, last):
        """把画布的滚动位置同步给滚动条（tkinter 要求的标准接法）。"""
        self.scrollbar.set(first, last)
        # 当首尾都在可见范围内（first=0.0 / last=1.0）时说明"全看得到"，不需要滚动条
        try:
            need = not (float(first) <= 0.0 and float(last) >= 1.0)
        except (TypeError, ValueError):
            need = True
        self._update_scrollbar(need)

    def _update_scrollbar(self, need: bool):
        """按需显示/隐藏滚动条。"""
        if need == self._scrollbar_visible:
            return
        self._scrollbar_visible = need
        try:
            if need:
                # ★ before=self.canvas 不能省：否则滚动条会被排到画布后面，
                #   而画布带 expand=True 会把空间吃光 —— 滚动条又变成 1×1（BUG-036）
                self.scrollbar.pack(side="right", fill="y", before=self.canvas)
            else:
                self.scrollbar.pack_forget()
        except tk.TclError:
            # 不许静默失败（AGENTS.md 3.2 / BUG-044）：
            # 这里出错的表现是"滚动条该出不出现"，很难联想回这一行，所以记一笔。
            logger.warning("切换滚动条显示状态失败（窗口可能正在销毁）", exc_info=True)

    def _on_inner_resize(self, event=None):
        """内容变了 -> 告诉画布"可滚动区域有多大"。"""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        """
        画布变宽了 -> 让内容区跟着变宽（否则文字挤在左边一小条里）。

        ★ 这就是"调整大小后里面内容排版相应改变"的实现：
          内容区的宽度始终等于画布宽度，于是每个 Label 的 wraplength
          也会跟着更新（见 FloatingWindow._reflow），文字就会重新折行。

        ★★【v0.4.1 补的一处（用户反馈"内容显示不全"）】
            画布宽度变化时**必须回头通知窗口重算折行宽度**。
            因为"滚动条出现 / 消失"本身就会改变画布宽度 ——
            而滚动条是 layout 过程中自己冒出来的，窗口那边根本不知道。
            不通知的话，每个 Label 的折行宽度就停在旧值上：
            变窄时文字被裁掉、变宽时右边空一大片。
        """
        self.canvas.itemconfigure(self._window_id, width=event.width)
        if self._on_resize is not None:
            self._on_resize(event.width)

    def bind_wheel(self, handler):
        """把滚轮事件绑到整个应用（见类注释里"为什么不能只绑 Canvas"）。"""
        for widget in (self.canvas, self.inner, self):
            widget.bind("<MouseWheel>", handler, add="+")

    def clear(self):
        """清空内容区（重新画之前调用）。"""
        for child in self.inner.winfo_children():
            child.destroy()


# ===========================================================================
# 三、悬浮窗主体
# ===========================================================================


class FloatingWindow:
    """桌面悬浮窗。用法：FloatingWindow(api, config_data).run()"""

    def __init__(self, api: FloatingApi, config_data: dict = None):
        """
        参数 config_data 是服务端给的窗口设置（位置、大小、配色……）。

        【为什么从外面传进来，而不是构造函数里自己去拉？】
            因为"能不能画出这个窗口"和"从哪拿设置"是两件事。
            分开之后，测试里可以塞一份假的设置进来直接构造窗口，
            不需要真的起一个 Web 服务 —— 这就是"依赖注入"带来的好处。
        """
        self.api = api
        self.config_data = config_data or {}
        self.payload = {}
        self._last_signature = None

        # 拖动 / 缩放的状态
        self._mode = None            # None / "move" / "resize"
        self._edge = ""              # 正在拖的是哪条边/哪个角：n/s/e/w/ne/...
        self._start_pointer = (0, 0)
        self._start_geometry = (0, 0, 0, 0)     # x, y, w, h

        self._save_job = None        # 位置/大小保存的防抖定时器
        # ★ 拖动/缩放开始时"服务端说的坐标"，回写前拿它比对（修 BUG-039）
        self._geometry_baseline = None
        self._stop = False
        self._stop_file = config.FLOAT_DIR / "stop.flag"
        self._toast = None
        self._reminder_ids = set()   # 已经弹过提示框的提醒，防止刷屏
        self._task_rows = []         # 当前画出来的任务行（重新折行时要改它们的 wraplength）
        self.selected_task_id = None  # "最近点过的那条"（工具栏 ✎ 按钮的目标）

        # ★ DPI 感知必须在【创建 Tk 窗口之前】声明（原因见 enable_dpi_awareness）。
        #   放在这里、而不是 FloatingWindow 里，就是因为构造函数的
        #   下一行就是 tk.Tk() —— 晚一步就来不及了。
        self.dpi_mode = enable_dpi_awareness()
        logger.info("DPI 感知：%s（这决定了字体是否清晰）", self.dpi_mode)

        self.root = tk.Tk()
        self.root.title(config.APP_NAME + " 悬浮窗")
        self._setup_window()
        self._build()
        self._bind_window_level_events()

    # ------------------------------------------------------------------
    # 窗口本身
    # ------------------------------------------------------------------
    def _setup_window(self):
        """
        设置窗口的"身份"：无边框、层级、透明度、位置大小。

        【只做一次的事都放这里】——位置和大小除外，那两个归 apply_geometry()。
        混在一起就会出现"想重复做的做不了"（BUG-026 的教训）。
        """
        cfg = self.config_data

        self.root.overrideredirect(True)          # 去掉系统标题栏（无边框）

        # ★【用户要求：悬浮窗要设于桌面，任何窗口都能覆盖】
        #   所以默认【不】置顶（配置里 FLOATING_ALWAYS_ON_TOP = False）。
        #   这行代码本身没变 —— 变的是配置里的默认值，
        #   以及"用户可以随时在设置弹窗里把它打开"这个入口。
        self.root.attributes("-topmost", bool(cfg.get("always_on_top", False)))
        self.root.attributes("-alpha", float(cfg.get("opacity", 0.5)))

        # 【toolwindow 的作用】让窗口不出现在任务栏和 Alt+Tab 列表里。
        # 悬浮窗的正确形态是"贴在桌面上的便签"，不是"一个正经的应用窗口"。
        try:
            self.root.attributes("-toolwindow", True)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


        width = int(cfg.get("width", config.FLOATING_DEFAULT_WIDTH))
        height = int(cfg.get("height", config.FLOATING_DEFAULT_HEIGHT))
        x, y = int(cfg.get("x", 0)), int(cfg.get("y", 0))
        x, y = self._clamp_position(x, y, width, height)
        self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close_clicked)

    def _clamp_position(self, x, y, width, height) -> tuple:
        """
        把位置限制在屏幕可见范围内。

        留 40 像素的余量：让用户至少还能看见一条边，能用鼠标抓到它。
        （完全对齐屏幕边缘的话，窗口边框会贴着显示器边框，很难拖。）
        """
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        margin = 40

        x = max(margin - width, min(x, screen_w - margin))
        y = max(0, min(y, screen_h - margin))
        return x, y

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _palette(self) -> dict:
        """
        算出这套配色要用到的所有颜色。

        【为什么要"算"而不是直接用设置里的三个颜色？】
            因为界面上有十几种元素（标题栏、分组条、分隔线、过期文字……），
            让用户一个个去选是不现实的。用户只需要给三个"基调"颜色，
            剩下的深浅层次由程序算出来 —— 这样换配色时整体感觉是一致的，
            不会出现"背景换了但分隔线还是旧主题的颜色"这种违和。
        """
        cfg = self.config_data
        bg = cfg.get("bg_color", config.FLOATING_DEFAULT_BG)
        fg = cfg.get("fg_color", config.FLOATING_DEFAULT_FG)
        accent = cfg.get("accent_color", config.FLOATING_DEFAULT_ACCENT)

        return {
            "bg": bg,
            "fg": fg,
            "accent": accent,
            "head_bg": mix(bg, fg, 0.08),        # 顶部工具栏：比背景稍深一点
            "group_bg": mix(bg, fg, 0.12),       # 分组标题条
            "line": mix(bg, fg, 0.25),           # 分隔线
            "muted": mix(fg, bg, 0.45),          # 次要文字（时间、分类）
            "done_fg": mix(fg, bg, 0.55),        # 已完成的文字（更淡）
            "warn": "#FF6B6B" if _hex_to_rgb(bg)[0] < 120 else "#D93025",   # 过期
            "head_fg": contrast_text(mix(bg, fg, 0.08)),
        }

    def _font(self, size_delta: int = 0, bold: bool = False) -> tuple:
        """
        造一个字体描述元组 (字体名, 字号, 样式)。

        为什么要包一层？因为字号会在好几个地方被"加减一点"
        （标题大一号、说明小两号），写在一处就不会出现
        "某个地方忘了改，字号和别处不一致"。
        """
        base = int(self.config_data.get("font_size", config.FLOATING_DEFAULT_FONT_SIZE))
        size = max(config.FLOATING_MIN_FONT_SIZE - 2,
                   min(base + size_delta, config.FLOATING_MAX_FONT_SIZE + 6))
        family = self.config_data.get("font_family", config.FLOATING_DEFAULT_FONT_FAMILY)
        return (family, size, "bold") if bold else (family, size)

    def _build(self):
        """
        把整个界面搭出来（每次换配色都会重新跑一遍）。

        【注意：这里刻意不去动窗口的位置和大小】
            位置和大小统一由 apply_geometry() 负责。
            如果两处都设置，就会出现"谁最后执行谁说了算"的混乱 ——
            而这正是 BUG-026（改了尺寸不生效）的成因之一。
            一句话：**一个东西只有一个地方负责设置它。**
        """
        pal = self._palette()
        self.pal = pal
        self._task_rows = []

        self.root.configure(bg=pal["bg"])

        # 【pack 的顺序决定了谁在最上面】
        #   toolbar 先 pack 到 top -> 它在最上面
        #   addbar  先 pack 到 bottom -> 它在最下面
        #   body 最后 pack 并 expand -> 它吃掉中间所有剩余空间
        # 这个顺序不能乱，否则会出现"工具栏跑到下面去了"这种怪现象。
        self._build_toolbar(pal)
        self._build_addbar(pal)
        self._build_body(pal)

    # ---- 顶部工具栏（用户要求：编辑和分类放上方，右上角有设置按钮）----
    def _build_toolbar(self, pal):
        r"""
        顶部工具栏。

        ★★【v0.4.1 修了两个"挤"的问题（用户反馈"上边框过于拥挤"）】

            ① **高度不再写死。** 原来固定 22 像素且 pack_propagate(False)，
               而 9 磅宋体的一个按钮实际需要 24 像素 ——
               于是按钮上下各被切掉 1 像素，看起来就是"挤、显示不全"。
               现在让工具栏按内容自己长高，TITLE_ROW_HEIGHT 只当最小高度。

            ② **pack 顺序反过来了。** 原来是"先 pack 标题（side=left）再 pack 按钮"，
               标题一上来就要走 162 像素（"📝 离线待办清单 6"），
               右侧那组按钮只能捡剩下的 —— 加了 ✎ 之后，
               最后 pack 的「日期 ▾」被挤成了 **1 像素宽**（实测），
               等于分类切换功能直接"消失"。
               现在改成：**按钮先占够，标题吃剩下的、并按可用宽度自己缩写**。
        """
        bar = tk.Frame(self.root, bg=pal["head_bg"])
        bar.pack(fill="x", side="top")
        self.toolbar = bar

        # ---- ① 先 pack 右边那组按钮（从右往左，视觉上就是右对齐）----
        #   顺序（从最右开始）：✕ 关闭 / ⚙ 设置 / ＋ 新建 / ✎ 编辑 / ⟳ 刷新 / 分类
        #   顺序（从最右开始）：✕ 关闭 / ⚙ 设置 / ＋ 新建 / ⟳ 刷新 / 分类
        self._icon_button(bar, "✕", self.on_close_clicked, pal,
                          "隐藏窗口（设置里可以再叫出来）").pack(side="right", padx=(0, 4))
        self._icon_button(bar, "⚙", self.open_settings_dialog, pal,
                          "设置：字号 / 颜色 / 透明度 / 字体 / 关闭悬浮窗").pack(side="right")
        self._icon_button(bar, "＋", self.open_add_dialog, pal,
                          "新建待办").pack(side="right")
        # ★ v0.3.4 新增：顶部工具栏的「编辑」按钮（用户 2026-09-14 确认要加）。
        #   它编辑的是【最近点过的那一条】——没有选中过就提示一句。
        #   为什么要有"选中"这个概念？
        #     编辑弹窗必须知道"编辑哪一条"，而工具栏按钮本身不携带这个信息。
        #     点任务行时顺手记下它是哪一条，这个按钮才有明确的目标。
        #   （点任务行直接弹出编辑窗这个行为保持不变 —— 用户当初明确要的就是它。）
        self._icon_button(bar, "✎", self.open_selected_edit, pal,
                          "编辑：先点选一条待办，再点这里").pack(side="right")
        self._icon_button(bar, "⟳", lambda: self.refresh(force=True), pal,
                          "立刻刷新").pack(side="right")

        # ---- 分类切换（用户要求：分类旁边设一个三角，点击即可更换）----
        #
        # 【为什么做成"按钮 + 弹出菜单"而不是下拉框？】
        #   tkinter 的 OptionMenu 在深色主题下很丑（它内部是一个原生 Menu，
        #   要额外改配色），而且它默认比较宽。用一个小按钮 + tk.Menu，
        #   样式能跟主题走，宽度也能压到最小。
        group_name = config.FLOATING_GROUP_BY_NAMES.get(
            self.config_data.get("group_by", "date"), "按日期分块")
        # 按钮上只显示"短名 + ▾"（"日期 ▾"），省地方
        short = group_name.replace("按", "").replace("分块", "")
        self.group_button = self._text_button(bar, "%s ▾" % short, self.open_group_menu, pal)
        self.group_button.pack(side="right", padx=(4, 4))

        # ---- ② 最后 pack 标题：它吃剩下的空间，并按可用宽度自己缩写 ----
        self.title_label = tk.Label(bar, text="📝 " + config.APP_NAME,
                                    bg=pal["head_bg"], fg=pal["head_fg"],
                                    font=self._font(-1, bold=True),
                                    anchor="w", padx=MARGIN, pady=TOOLBAR_PAD_Y)
        self.title_label.pack(side="left", fill="both", expand=True)
        # 工具栏尺寸一变就重新决定标题写多详细（窄了自动缩成 "📝 3"）
        bar.bind("<Configure>", lambda _e: self._fit_toolbar_title())

        # 拖动：按住工具栏空白处也能移动窗口（标题和按钮上不拖，避免误触）
        for widget in (bar, self.title_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", self._show_menu)

    def _icon_button(self, master, text, command, pal, tooltip=""):
        """工具栏上的小按钮（tkinter 的 Button 在无边框窗口里样式很难看，用 Label 代替）。"""
        label = tk.Label(master, text=text, bg=pal["head_bg"], fg=pal["head_fg"],
                         font=self._font(0, bold=True), padx=4,
                         pady=max(1, TOOLBAR_PAD_Y - 1), cursor="hand2")
        label.bind("<Button-1>", lambda _e: command())
        label.bind("<Enter>", lambda _e: self._hover_btn(label, pal, True, tooltip))
        label.bind("<Leave>", lambda _e: self._hover_btn(label, pal, False))
        return label

    def _text_button(self, master, text, command, pal):
        """带文字的按钮（分类切换用）。"""
        label = tk.Label(master, text=text, bg=pal["group_bg"], fg=pal["accent"],
                         font=self._font(-2, bold=True), padx=6,
                         pady=TOOLBAR_PAD_Y, cursor="hand2")
        label.bind("<Button-1>", lambda _e: command())
        label.bind("<Enter>", lambda _e: label.configure(bg=pal["accent"],
                                                        fg=contrast_text(pal["accent"])))
        label.bind("<Leave>", lambda _e: label.configure(bg=pal["group_bg"],
                                                        fg=pal["accent"]))
        return label

    def _hover_btn(self, label, pal, entering, tooltip=""):
        """按钮的悬停效果 + 底部提示文字。"""
        try:
            if entering:
                label.configure(bg=pal["accent"], fg=contrast_text(pal["accent"]))
                if tooltip:
                    self._set_hint(tooltip)
            else:
                label.configure(bg=pal["head_bg"], fg=pal["head_fg"])
                if tooltip:
                    self._set_hint("")
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def _fit_toolbar_title(self):
        r"""
        按工具栏的【剩余宽度】决定标题写多详细。

        ★ 为什么需要它？（v0.4.1，用户反馈"上边框过于拥挤"）
            工具栏里除了标题，还挤着 6 个控件（分类 / 刷新 / 新建 / 编辑 / 设置 / 关闭）。
            在默认的 320 像素宽窗口里，这些按钮本身就要 230 像素左右 ——
            留给标题的只有 90 像素，而"📝 离线待办清单 6"要 162 像素。
            写死全名的话，要么标题被截断、要么按钮被挤没（实测「日期 ▾」被挤成 1 像素）。

            所以让它"能屈能伸"：
                放得下 -> 「📝 离线待办清单 6」（信息最全）
                放不下 -> 「📝 6」（只留最关键的"还有几条没做"）
                再窄   -> 「📝」

            这个做法在界面里很常见，叫"响应式标题"：
            **不是把东西删掉，而是让它在空间不够时换个更短的表达。**
        """
        try:
            stats = (self.payload or {}).get("stats") or {}
            count = stats.get("todo", 0)
            # 三级：够宽给全名，中等给"待办"，窄了只留图标和条数
            full = ("📝 %s %d" % (config.APP_NAME, count)) if count else ("📝 " + config.APP_NAME)
            mid = ("📝 待办 %d" % count) if count else "📝 待办"
            short = ("📝 %d" % count) if count else "📝"
            font = tkfont.Font(root=self.root, family=self._font(-1, bold=True)[0],
                               size=self._font(-1, bold=True)[1], weight="bold")
            used = 0
            for child in self.toolbar.winfo_children():
                if child is self.title_label:
                    continue
                used += max(child.winfo_reqwidth(), child.winfo_width())
            # 【为什么减 MARGIN * 3？】
            #   标题 Label 自己左右各有一圈内边距（padx=MARGIN），
            #   算可用宽度时必须把它扣掉 —— 否则会出现
            #   "文本量出来 44 像素、可用 44 像素"这种刚好相等的情况，
            #   判断 <= 通过、但 Label 实际需要 44+12=56 像素，还是被挤窄。
            #   （这就是第一次没收缩成功的原因，差的就是这 12 像素。）
            #   再多留一个 MARGIN 当安全缝。
            avail = self.toolbar.winfo_width() - used - MARGIN * 3
            text = full
            for candidate in (mid, short, "📝"):
                if font.measure(text) > avail:
                    text = candidate
            self.title_label.configure(text=text)
        except (tk.TclError, KeyError, IndexError) as exc:
            logger.debug("工具栏标题自适应失败，保持原样：%s", exc, exc_info=True)

    def open_group_menu(self):
        """
        点分类按钮弹出的菜单：按日期 / 按重要度 / 按待办类型。

        【为什么标签用"重要度 / 待办类型"而不是"优先级 / 分类"？】
            因为用户原话就是这么说的：
            「分类也可按照重要度或待办类型分类」。
            界面上用用户的话，代码里仍用 date / priority / category 这三个键 ——
            "界面文字"和"程序标识"分开，以后想改文案不用动逻辑。
        """
        pal = self.pal
        menu = tk.Menu(self.root, tearoff=0)
        for key in config.FLOATING_GROUP_BY_CHOICES:
            label = config.FLOATING_GROUP_BY_NAMES[key]
            menu.add_command(label=label, command=lambda k=key: self.on_group_change(k))
        try:
            menu.tk_popup(self.group_button.winfo_rootx(),
                          self.group_button.winfo_rooty() + self.group_button.winfo_height())
        finally:
            menu.grab_release()

    # ---- 底部：快速添加（用户要求：底部的"编辑"和"分类"搬走上去了，
    #       这里只留"打一句话回车就添加"这一件事，所以它很小）----
    def _build_addbar(self, pal):
        bar = tk.Frame(self.root, bg=pal["head_bg"])
        bar.pack(fill="x", side="bottom")
        self.addbar = bar

        self.entry = tk.Entry(bar, bg=pal["bg"], fg=pal["fg"],
                              insertbackground=pal["fg"], font=self._font(),
                              relief="flat", highlightthickness=1,
                              highlightbackground=pal["line"],
                              highlightcolor=pal["accent"])
        # 【上下留白别太抠（v0.4.1，用户反馈"下边框过于拥挤"）】
        #   原来的 (3, 1) 让它紧贴着输入框下沿和提示行，视觉上很挤。
        self.entry.pack(fill="x", padx=MARGIN, pady=(9, 5))
        self.entry.bind("<Return>", self.on_quick_add)
        self.entry.bind("<Escape>", lambda _e: self.entry.delete(0, "end"))

        self.hint = tk.Label(bar, text="输入后回车添加", bg=pal["head_bg"],
                             fg=pal["muted"], font=self._font(-3),
                             anchor="w", padx=MARGIN)
        self.hint.pack(fill="x", pady=(0, 8))

    # ---- 主体：分组 + 任务行 ----
    def _build_body(self, pal):
        wrapper = tk.Frame(self.root, bg=pal["bg"])
        wrapper.pack(fill="both", expand=True)

        # 把"画布变宽 / 变窄了"接到窗口的 _reflow 上（不接的话文字会被裁，v0.4.1 修）
        self.scroll = ScrollFrame(wrapper, bg=pal["bg"], on_resize=self._reflow)
        self.scroll.pack(fill="both", expand=True)
        self.scroll.bind_wheel(self._on_wheel)

        self.body = self.scroll.inner
        self._render_groups()

    def _render_groups(self):
        """按服务端给的分块数据，把每一块和每一条任务画出来。"""
        pal = self.pal
        self.scroll.clear()
        self._task_rows = []

        groups = (self.payload or {}).get("groups") or []
        stats = (self.payload or {}).get("stats") or {}

        if not groups:
            tk.Label(self.body, text="🎉 这里空的，没有待办", bg=pal["bg"], fg=pal["muted"],
                     font=self._font(), pady=14).pack(fill="x")
            return

        for group in groups:
            self._render_group_header(group)
            for task in group["tasks"]:
                self._render_task(task)

        self._render_summary_line(stats)

        # ★★【画完立刻按当前宽度算一次折行（v0.4.1 修"内容显示不全"）】
        #   原来只在 apply_geometry()（改窗口大小时）里调 _reflow，
        #   而【启动时根本走不到那里】—— 于是刚打开窗口的那批任务
        #   全部是 wraplength=0（等于不折行），标题一长就被硬裁掉。
        #   实测：控件宽 283、文字需要 540，右边一半直接看不见。
        self._reflow(self.scroll.canvas.winfo_width() or self.root.winfo_width())

    def _render_group_header(self, group):
        """
        一块的标题条，例如「▌今天 9-14   2」。

        【为什么要用"▌"这个竖条？】
            纯文字的标题条在密集列表里"抓不住眼睛"。
            左边加一个强调色的竖条之后，视线往下扫就能立刻分清
            "哪些是分组、哪些是任务"—— 这叫视觉锚点，
            在信息密集的界面上是非常便宜又有效的做法。

        【为什么高度是 16 像素这种很小的值？】
            用户要求「若一个界面足够则将这几个日期紧密排列」。
            所以分组条刻意做得比任务行还矮一点，它只是"分隔符"，
            不该抢任务本身的注意力。
        """
        pal = self.pal
        bar = tk.Frame(self.body, bg=pal["group_bg"], height=GROUP_ROW_HEIGHT)
        bar.pack(fill="x", pady=(2, 0))
        bar.pack_propagate(False)

        # 左边的强调色竖条
        tk.Label(bar, text="▌", bg=pal["group_bg"], fg=pal["accent"],
                 font=self._font(-2)).pack(side="left")

        title = tk.Label(bar, text=group["title"], bg=pal["group_bg"],
                         fg=pal["accent"], font=self._font(-2, bold=True), anchor="w")
        title.pack(side="left")

        # 右边的条数（很淡，只是为了"一眼看出这块有几件事"）
        tk.Label(bar, text=str(group["count"]), bg=pal["group_bg"],
                 fg=pal["muted"], font=self._font(-3)).pack(side="right", padx=4)

        # 分组条的说明文字（鼠标停上去才显示）
        if group.get("hint"):
            for widget in (bar, title):
                widget.bind("<Enter>", lambda _e, h=group["hint"]: self._set_hint(h))
                widget.bind("<Leave>", lambda _e: self._set_hint(""))

    def _render_task(self, task):
        """
        画一条任务。

        整行的布局（**很紧凑**，用户要求"不要占满"）：
            [☐] [标题]                        [右侧：时间 / ⏰提醒 / ↑优先级]

        【为什么"右侧信息"要右对齐？】
            因为它们都是短标签（"18:00"、"高"、"⏰"），
            左对齐的话会和标题挤在一起，一长一短很难扫读。
            右对齐之后，视线沿着右边一列往下扫，就能快速看出"哪条最急"。

        【为什么点"整行任意位置"都能打开编辑？】
            v0.3.1 之前只有点"标题文字"才能编辑，而标题往往很短，
            右半行是空的 —— 用户点了没反应，会以为坏了。
            现在整行都绑了点击（方框除外，它留给"切换完成"）。
        """
        pal = self.pal
        row_bg = pal["bg"] if not task["overdue"] else mix(pal["bg"], pal["warn"], 0.10)

        row = tk.Frame(self.body, bg=row_bg)
        row.pack(fill="x")

        # ---- 左：完成框 ----
        #
        # ⚠️ 注意这里 padx 是【一个整数】，不是元组。
        #    (左右, 上下) 这种两元素的写法只有 grid / pack 支持；
        #    直接传给 tk.Label 会被 Tcl 当成"6 2"这种非法的屏幕距离，
        #    报 `TclError: bad screen distance "6 2"` —— 于是整行画不出来。
        #    这正是本次开发踩到的 BUG-028（窗口起来后一片空白）。
        box = tk.Label(row, text="☑" if task["is_done"] else "☐", bg=row_bg,
                       fg=pal["accent"] if task["is_done"] else pal["muted"],
                       font=self._font(1), cursor="hand2", padx=MARGIN)
        box.pack(side="left", fill="y")
        box.bind("<Button-1>", lambda _e, t=task: self.on_toggle(t))

        # ---- 右：时间 / 提醒 / 优先级（从右往左 pack，视觉上就是右对齐）----
        right = tk.Frame(row, bg=row_bg)
        right.pack(side="right", fill="y")

        if task["due_label"]:
            color = pal["warn"] if task["overdue"] else pal["muted"]
            tk.Label(right, text=task["due_label"], bg=row_bg, fg=color,
                     font=self._font(-3), padx=3).pack(side="right")

        if task["remind_label"] and self.config_data.get("highlight_reminders", True):
            color = pal["accent"] if task["remind_soon"] else pal["muted"]
            tk.Label(right, text="⏰" + task["remind_label"], bg=row_bg, fg=color,
                     font=self._font(-3), padx=2).pack(side="right")

        if task["priority"] != config.PRIORITY_NORMAL:
            marker = "↑" if task["priority"] == config.PRIORITY_HIGH else "↓"
            tk.Label(right, text=marker, bg=row_bg,
                     fg=pal["warn"] if task["priority"] == config.PRIORITY_HIGH else pal["muted"],
                     font=self._font(-2, bold=True), padx=2).pack(side="right")

        # ---- 中：标题（占满剩下的空间）----
        # 【为什么要 truncate？】
        #   有的待办标题很长。如果不截断，它会挤掉右边的时间标签，
        #   甚至把整行撑出窗口 —— 而窗口可能只有 200 像素宽（最小尺寸）。
        text = task["title"]
        if len(text) > 60:
            text = text[:59] + "…"
        if task["category"] and self.config_data.get("group_by") != "category":
            text = "[%s] %s" % (task["category"], text)

        label = tk.Label(row, text=text, bg=row_bg,
                         fg=pal["done_fg"] if task["is_done"] else pal["fg"],
                         font=self._font(0, bold=task["priority"] == config.PRIORITY_HIGH),
                         anchor="w", justify="left", padx=1, pady=1)
        label.pack(side="left", fill="both", expand=True)

        # 记下来：窗口改大小时要给它们重算折行宽度（见 _reflow）
        self._task_rows.append(label)

        # 整行都能点：单击=编辑，双击=切换完成，右键=更多操作
        for widget in (row, label, right):
            widget.bind("<Button-1>", lambda _e, t=task: self.open_edit_dialog(t))
            widget.bind("<Double-Button-1>", lambda _e, t=task: self.on_toggle(t))
            widget.bind("<Button-3>", lambda e, t=task: self._show_task_menu(e, t))
            widget.bind("<Enter>", lambda _e, r=row, bg=row_bg: self._hover(r, pal["accent"], bg, True))
            widget.bind("<Leave>", lambda _e, r=row, bg=row_bg: self._hover(r, None, bg, False))

    def _hover(self, row, accent, original_bg, entering):
        """
        整行悬停高亮。

        【为什么只改背景色，不改前景色？】
            因为这一行里的"完成框"用的是强调色（☑）。
            如果连前景色一起覆盖，勾选状态就看不出来了 ——
            高亮是为了帮忙，不是为了把信息抹掉。
        """
        color = mix(original_bg, accent, 0.18) if entering and accent else original_bg
        try:
            row.configure(bg=color)
            for child in row.winfo_children():
                child.configure(bg=color)
                for grand in child.winfo_children():
                    grand.configure(bg=color)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def _show_task_menu(self, event, task):
        """任务的右键菜单：完成 / 稍后提醒 / 删除（移到回收站）。"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="✎ 编辑…", command=lambda: self.open_edit_dialog(task))
        menu.add_command(label="✓ 标记为" + ("未完成" if task["is_done"] else "已完成"),
                         command=lambda: self.on_toggle(task))
        if task["remind_enabled"] and not task["is_done"]:
            for minutes in config.FLOATING_QUICK_REMIND_MINUTES:
                menu.add_command(label="⏰ %d 分钟后再提醒" % minutes,
                                 command=lambda m=minutes: self.on_snooze(task, m))
        menu.add_separator()
        menu.add_command(label="🗑 删除（可从回收站还原）",
                         command=lambda: self.on_delete(task))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _render_summary_line(self, stats):
        """底部那行"共 N 条 · 未完成 N · 过期 N"（很淡，只是参考信息）。"""
        pal = self.pal
        parts = ["共 %d 条" % stats.get("total", 0),
                 "未完成 %d" % stats.get("todo", 0)]
        if stats.get("overdue"):
            parts.append("过期 %d" % stats["overdue"])
        if stats.get("today"):
            parts.append("今天 %d" % stats["today"])

        tk.Label(self.body, text=" · ".join(parts), bg=pal["bg"], fg=pal["muted"],
                 font=self._font(-3), pady=4).pack(fill="x")

    # ------------------------------------------------------------------
    # 移动 / 缩放：鼠标移到窗口【边缘】即可（用户要求）
    # ------------------------------------------------------------------
    def _bind_window_level_events(self):
        """窗口级别的事件绑定（见下面每个函数的说明）。"""
        # ---- ① 边缘感应区 ----
        #
        # 【为什么要做这个？】用户的要求是
        #   「鼠标位于悬浮窗边缘即可移动与调整悬浮窗大小」。
        #
        # 去掉系统标题栏之后，系统提供的"拖边缘缩放"就没了
        # （overrideredirect 的代价）。所以这里在四条边和四个角上
        # 各铺一个透明的小条 —— 鼠标进入它们时：
        #     · 光标变成对应的箭头（告诉用户"这里可以拖"）
        #     · 按下拖动 = 改大小；按住中间空白处拖 = 移动窗口
        #
        # 【为什么用 place 而不是 pack？】
        #   place 是"绝对定位"，可以精确贴在边上、浮在其它控件上面，
        #   而且用 relx/rely 能自动跟着窗口大小走。
        #   用 pack 的话这些细条会去抢空间，把界面挤变形。
        self._build_edge_grips()

        # ---- ② 快捷键 ----
        self.root.bind("<Control-q>", lambda _e: self.on_close_clicked())
        self.root.bind("<Escape>", lambda _e: self.entry.focus_set())
        self.root.bind("<Control-n>", lambda _e: self.open_add_dialog())
        self.root.bind("<F5>", lambda _e: self.refresh(force=True))

        # ---- ③ 滚轮 ----
        # bind_all：绑到整个应用，这样鼠标停在"任务文字"上时也能滚
        #（只绑 Canvas 的话，事件发给文字就不会冒泡上来 —— 表现为"滚不动"）
        self.root.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _build_edge_grips(self):
        """
        在四条边和四个角上铺"感应条"。

        布局（每条边的宽度都是 EDGE_GRIP = 6 像素）：
            左上角 ───────── 上边 ───────── 右上角
              │                                │
            左边                             右边
              │                                │
            左下角 ───────── 下边 ───────── 右下角

        角落的感应区比边略大一点（10 像素），因为对角拖拽最难瞄准。
        """
        grip = EDGE_GRIP
        corner = grip + 4

        # 四条边：pack 到对应的方向，然后"填满"这个方向上的长度。
        # 用 place 更直接：relwidth=1.0 表示"横向上占满整个窗口"。
        self._grips = []
        specs = [
            # (方向, relx, rely, relwidth, relheight, 光标, 尺寸文字)
            ("n", 0.0, 0.0, 1.0, None, CURSOR_V, "上边"),
            ("s", 0.0, 1.0, 1.0, None, CURSOR_V, "下边"),
            ("w", 0.0, 0.0, None, 1.0, CURSOR_H, "左边"),
            ("e", 1.0, 0.0, None, 1.0, CURSOR_H, "右边"),
            ("nw", 0.0, 0.0, None, None, CURSOR_DIAG, "左上角"),
            ("ne", 1.0, 0.0, None, None, CURSOR_DIAG, "右上角"),
            ("sw", 0.0, 1.0, None, None, CURSOR_DIAG, "左下角"),
            ("se", 1.0, 1.0, None, None, CURSOR_DIAG, "右下角"),
        ]

        for edge, relx, rely, relw, relh, cursor, _name in specs:
            strip = tk.Frame(self.root, bg=self.pal["bg"], cursor=cursor)
            kwargs = {"relx": relx, "rely": rely,
                      "anchor": self._anchor_for(edge)}
            if edge in ("n", "s"):
                kwargs["relwidth"] = relw
                kwargs["height"] = grip
            elif edge in ("w", "e"):
                kwargs["relheight"] = relh
                kwargs["width"] = grip
            else:
                kwargs["width"] = corner
                kwargs["height"] = corner
            strip.place(**kwargs)

            strip.bind("<Button-1>", lambda e, ed=edge: self._resize_start(e, ed))
            strip.bind("<B1-Motion>", self._resize_move)
            strip.bind("<ButtonRelease-1>", self._resize_end)
            # 【为什么边缘也能移动窗口？】
            #   用户说"鼠标位于悬浮窗边缘即可移动与调整大小"。
            #   而"边缘按下 + 不拖 = 移动"会和"边缘拖 = 缩放"冲突 ——
            #   所以这里让右键按住边缘来移动（左键留给缩放，那个更常用）。
            strip.bind("<Button-3>", self._drag_start)
            strip.bind("<B3-Motion>", self._drag_move)
            strip.bind("<ButtonRelease-3>", self._drag_end)

            self._grips.append(strip)

    @staticmethod
    def _anchor_for(edge: str) -> str:
        """
        每个感应条该以哪个角为锚点。

        【为什么要 anchor？】
            place 的 relx/rely 是"锚点相对窗口的位置"。
            比如"右边"这条：relx=1.0 表示"锚点贴在窗口最右边"，
            这时锚点必须是 'ne'（右上角）才能让这条竖条正好压在最右侧。
            搞错 anchor 的话，感应条会跑到窗口外面去 6 像素 ——
            表现就是"鼠标要移到窗口外面一点才能拖"，非常别扭。
        """
        return {
            "n": "nw", "s": "sw", "w": "nw", "e": "ne",
            "nw": "nw", "ne": "ne", "sw": "sw", "se": "se",
        }[edge]

    def _on_wheel(self, event):
        """鼠标滚轮（delta 在 Windows 上是 120 的整数倍）。"""
        try:
            self.scroll.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    # ---- 移动 ----
    def _drag_start(self, event):
        if self._mode is not None:
            return
        self._mode = "move"
        # 记录"鼠标相对窗口左上角的偏移"。
        # 【为什么不能用鼠标坐标直接当窗口坐标？】
        #   那样窗口会"跳"一下：你按窗口右边，窗口左上角就跑到鼠标那儿去了。
        #   记住偏移量，拖动时保持这个偏移不变，手感才对。
        self._start_pointer = (event.x_root, event.y_root)
        self._start_geometry = (self.root.winfo_x(), self.root.winfo_y(),
                                self.root.winfo_width(), self.root.winfo_height())
        # 记住"动手之前服务端说的坐标"，回写时用它判断有没有被别人改过（BUG-039）
        self._geometry_baseline = (self.config_data.get("x"), self.config_data.get("y"))

    def _drag_move(self, event):
        if self._mode != "move":
            return
        x0, y0, _w, _h = self._start_geometry
        dx = event.x_root - self._start_pointer[0]
        dy = event.y_root - self._start_pointer[1]
        try:
            self.root.geometry("+%d+%d" % (x0 + dx, y0 + dy))
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def _drag_end(self, _event):
        if self._mode == "move":
            self._mode = None
            self._schedule_save_position()

    # ---- 缩放 ----
    def _resize_start(self, event, edge: str):
        self._mode = "resize"
        self._edge = edge
        self._start_pointer = (event.x_root, event.y_root)
        self._start_geometry = (self.root.winfo_x(), self.root.winfo_y(),
                                self.root.winfo_width(), self.root.winfo_height())
        # 同上：缩放也要防止"回写把设置页改的坐标吃掉"（BUG-039）
        self._geometry_baseline = (self.config_data.get("x"), self.config_data.get("y"))

    def _resize_move(self, event):
        """
        拖动边缘时改窗口大小。

        【两条边同时动的逻辑（角落）】
            比如拖"右下角"：右边和下边都要跟着鼠标走。
            拖"左上角"：左边动的同时，窗口的 x/y 也要跟着变 ——
            不然窗口会"一边缩一边往右跑"，手感很怪。
            这段代码把"动的方向"和"不动的那条边固定住"分开处理，
            读起来比一堆 if 清楚。
        """
        if self._mode != "resize":
            return

        x0, y0, w0, h0 = self._start_geometry
        dx = event.x_root - self._start_pointer[0]
        dy = event.y_root - self._start_pointer[1]

        x, y, w, h = x0, y0, w0, h0

        # ---- 左/右：改宽度（同时可能要改 x）----
        if "e" in self._edge:
            w = w0 + dx
        if "w" in self._edge:
            w = w0 - dx
            x = x0 + dx          # 左边跟着走，右边界保持不动

        # ---- 上/下：改高度（同时可能要改 y）----
        if "s" in self._edge:
            h = h0 + dy
        if "n" in self._edge:
            h = h0 - dy
            y = y0 + dy

        # ---- 夹在允许范围内 ----
        # 【夹范围时为什么还要回退 x/y？】
        #   如果只夹 w 而不修 x，那么"往右拖左边、超过最小宽度"时，
        #   窗口会继续往右跑而宽度不变 —— 看起来像"窗口在漂移"。
        w = max(config.FLOATING_MIN_WIDTH, min(w, config.FLOATING_MAX_WIDTH))
        h = max(config.FLOATING_MIN_HEIGHT, min(h, config.FLOATING_MAX_HEIGHT))
        if "w" in self._edge:
            x = x0 + (w0 - w)      # 由"最终的宽度"反推 x，保证右边界不动
        if "n" in self._edge:
            y = y0 + (h0 - h)

        try:
            self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except tk.TclError:
            return
        self._reflow(w)

    def _resize_end(self, _event):
        if self._mode == "resize":
            self._mode = None
            self._edge = ""
            self._schedule_save_position()

    def _reflow(self, width: int):
        """
        窗口宽度变了之后，让里面的内容重新排版。

        ★ 这就是用户要求的「调整大小后里面内容排版相应改变」。

        【为什么需要它？】
            Canvas 里的内容区宽度会自动跟着变（见 ScrollFrame._on_canvas_resize），
            但**每个 Label 自己的折行宽度是固定值**，它不会自己更新。
            所以文字会出现两种毛病：
                · 窗口变窄 -> 文字被截断（右边几个字看不见）
                · 窗口变宽 -> 文字还在中间断行，右边空一大片
            解决办法就是给每个 Label 重设 wraplength = 当前可用宽度。

        【为什么留 80 像素的余量？】
            因为每行右边还有"时间 / ⏰ / ↑"这些标签，它们也要地方。
            不留给它们的话，标题会被挤成"一行一个字"的竖条。
        """
        # ★★ 【v0.4.4 修】折行宽度不能"按窗口宽减 80"猜（那是上一版的写法）。
        #    实测：窗口 320 宽、字号 10 磅时，右边那列（时间 + 优先级箭头）
        #    实际占了 120 多像素，标题只分到 177 像素，而 wraplength 被设成 240
        #    → 文字不折行 → 右边几个字被【硬裁掉】（BUG-046 的同类没修干净）。
        #    现在按"这一行真正被右边占用多少"来算，逐个标题设。
        #    【★ 实测踩到的第二个坑】不能直接问那个"右边那一列 Frame"要宽度：
        #    Frame 的 winfo_reqwidth() 在同一轮布局里可能还是旧值（1 像素），
        #    于是 used 算出来只有 39，折行宽度又变回 261 —— 等于没修。
        #    所以用 _needed_width() 递归问"它里面的控件"，那些值当场就是准的。
        for label in self._task_rows:
            try:
                row = getattr(label, "master", None)
                used = 0
                if row is not None:
                    used = sum(_needed_width(child)
                               for child in row.winfo_children() if child is not label)
                base = width
                if row is not None and row.winfo_width() > 1:
                    base = row.winfo_width()
                wrap = max(80, base - used - 20)
                label.configure(wraplength=wrap)
            except tk.TclError as exc:
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def _schedule_save_position(self):
        """
        拖动/缩放结束后 0.4 秒再保存位置。

        【为什么要"延迟保存"而不是马上保存？】
            用户拖动时会连续触发几十次移动事件，
            每次都写一次数据库 = 几十次磁盘写 + 几十次版本号 +1，
            纯属浪费。延迟到"手停下来"再存一次，
            这个过程叫【防抖 debounce】，是界面编程里非常常用的手法。
        """
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception as exc:                  # noqa: BLE001
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("这里出错不影响主流程，按可忽略处理：%s", exc, exc_info=True)

        self._save_job = self.root.after(400, self._save_position)

    def _save_position(self):
        r"""
        把位置和大小保存到服务端（下次开机窗口还在原地）。

        ★★【回写前必须先确认"服务端这段时间没被别人改过"（BUG-039）】

            这里有一条很容易忽略的竞态：
                ① 用户用鼠标把窗口拖到 (500,500)，松手 -> 0.4 秒后回写；
                ② 就在这 0.4 秒里，他在设置页把坐标改成了 (700,100)；
                ③ 回写发生 -> 服务端最终记的是 (500,500)，
                   **设置页那次修改被无声无息地吃掉了**。

            两条写入没有先后关系，谁最后写谁赢 —— 而窗口这条是【延迟 0.4 秒】发的，
            所以只要用户在这段时间里改设置，就一定会被覆盖。

            解决办法：拖动开始时先记住"当时服务端说的坐标"(self._geometry_baseline)，
            回写前再问一次服务端 —— 如果它已经变了，说明是别人改的，就放弃本次回写。
            宁可这次位置没存上（下次拖动还会存），也不能覆盖用户的显式设置。
        """
        self._save_job = None
        try:
            changes = {
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y(),
                "width": max(config.FLOATING_MIN_WIDTH, self.root.winfo_width()),
                "height": max(config.FLOATING_MIN_HEIGHT, self.root.winfo_height()),
            }
        except tk.TclError:
            return

        baseline = self._geometry_baseline
        if baseline is not None:
            try:
                server = (self.api.get_config() or {}).get("config") or {}
            except FloatingApiError as exc:
                # 问不到服务端 -> 不写。宁可这次不保存，也不要盲写覆盖别人的修改。
                logger.warning("回写窗口位置前读设置失败，本次跳过：%s", exc)
                return
            if (server.get("x"), server.get("y")) != tuple(baseline):
                logger.info("服务端坐标已被改动（%s -> %s），放弃本次拖动回写",
                            baseline, (server.get("x"), server.get("y")))
                self._set_hint("位置已按设置页的值为准")
                self._geometry_baseline = None
                return
        self._geometry_baseline = None
        self._patch_config(changes, quiet=True)

    def _patch_config(self, changes: dict, quiet: bool = False):
        """改设置（失败不弹窗，只写日志 —— 界面上的动作不该因为一次网络抖动就中断）。"""
        try:
            result = self.api.set_config(changes)
            fresh = (result or {}).get("status") or {}
            if isinstance(fresh.get("config"), dict):
                self.config_data = fresh["config"]
        except FloatingApiError as exc:
            if not quiet:
                self._set_hint("保存失败：%s" % exc)
            logger.warning("保存悬浮窗设置失败：%s", exc)

    # ------------------------------------------------------------------
    # 交互：勾选 / 稍后提醒 / 删除 / 快速添加 / 换分类
    # ------------------------------------------------------------------
    def on_toggle(self, task):
        """勾选 / 取消勾选。"""
        try:
            result = self.api.toggle(task["id"])
        except FloatingApiError as exc:
            self._set_hint("操作失败：%s" % exc)
            return
        self._set_hint(result.get("message", "已更新"))
        self.refresh(force=True)

    def on_snooze(self, task, minutes):
        try:
            result = self.api.snooze(task["id"], minutes)
        except FloatingApiError as exc:
            self._set_hint("稍后提醒失败：%s" % exc)
            return
        self._set_hint(result.get("message", "已推迟"))
        self.refresh(force=True)

    def on_delete(self, task):
        """删除 = 移到回收站（可以还原），所以不用二次确认弹窗。"""
        try:
            self.api.delete_task(task["id"])
        except FloatingApiError as exc:
            self._set_hint("删除失败：%s" % exc)
            return
        self._set_hint("已移到回收站")
        self.refresh(force=True)

    def on_quick_add(self, _event=None):
        """回车：用输入框里那句话新建一条待办。"""
        text = self.entry.get().strip()
        if not text:
            return
        try:
            result = self.api.quick_add(text)
        except FloatingApiError as exc:
            self._set_hint("添加失败：%s" % exc)
            return
        self.entry.delete(0, "end")
        self._set_hint(result.get("message", "已添加"))
        self.refresh(force=True)

    def on_group_change(self, key: str):
        """
        切换分类方式（按日期 / 按重要度 / 按待办类型）。

        改完立刻重新拉一次数据 —— 因为分组是【服务端】算的
        （那里才有"哪天算今天"这类规则），客户端只负责画。
        """
        if key == self.config_data.get("group_by"):
            return
        self._patch_config({"group_by": key})
        self.config_data["group_by"] = key
        # 按钮上的文字也要跟着变
        short = config.FLOATING_GROUP_BY_NAMES.get(key, "").replace("按", "").replace("分块", "")
        try:
            self.group_button.configure(text="%s ▾" % short)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        self._set_hint("已切换为：%s" % config.FLOATING_GROUP_BY_NAMES.get(key, key))
        self.refresh(force=True)

    def toggle_on_top(self):
        """右键菜单：总在最前 开/关（改了立刻生效，不用重启）。"""
        new_value = not bool(self.config_data.get("always_on_top", False))
        self.config_data["always_on_top"] = new_value
        try:
            self.root.attributes("-topmost", new_value)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        self._patch_config({"always_on_top": new_value})
        self._set_hint("总在最前：" + ("开" if new_value else "关"))

    def on_close_clicked(self):
        """
        点 ✕：隐藏窗口，而不是"关掉悬浮窗功能"。

        【为什么这么设计？】
            用户点 ✕ 时的心理预期是"先让它消失一下"，
            如果连总开关也关掉，那么"下次开机自启它就不会出现"——
            这会让他觉得设置自己丢了。
            所以：✕ = 隐藏（设置里的"已开启"还留着），
            想真正关闭请去设置弹窗里点「关闭悬浮窗」。
        """
        self._patch_config({"visible": False}, quiet=True)
        try:
            self.api.quit_window()
        except FloatingApiError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("服务端调用失败（调用方负责提示用户）：%s", exc, exc_info=True)

        self.shutdown()

    def _show_menu(self, event):
        """工具栏右键菜单。"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_checkbutton(label="总在最前面（会被别的窗口盖住时打开它）",
                             command=self.toggle_on_top)
        menu.add_command(label="设置…", command=self.open_settings_dialog)
        menu.add_separator()
        menu.add_command(label="隐藏悬浮窗", command=self.on_close_clicked)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_hint(self, text: str):
        """底部那行提示文字（3 秒后自动恢复成默认提示）。"""
        try:
            self.hint.configure(text=text or "输入后回车添加")
            if text:
                self.root.after(3000, lambda: self._set_hint(""))
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    # ------------------------------------------------------------------
    # 对话框（编辑 / 设置 / 新建）—— 都用同一套"紧凑表单"搭法
    # ------------------------------------------------------------------
    def _make_dialog(self, title: str, width: int, height: int, resizable: bool = True):
        r"""
        造一个对话框窗口（编辑 / 设置 / 新建都用它）。

        【为什么要 topmost？】悬浮窗本身可能置顶，
        对话框若不置顶，会被父窗口压在下面 —— 点了没反应，很困惑。

        【★ 为什么默认可以调整大小？（v0.4.1，用户要求）】
            用户原话：「悬浮窗设置界面也需要能用鼠标调整大小」。
            原来的写法是 resizable(False, False) —— 完全拉不动。
            而弹窗内容的高度会随字号变化（字号调到 20 磅时内容要 700+ 像素），
            不给用户拉大的能力，他就只能去改字号。
            所以现在默认允许调整，并配一个 minsize 防止被拉到看不见内容。
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=self.pal["bg"])
        dialog.transient(self.root)
        dialog.attributes("-topmost", True)
        dialog.resizable(resizable, resizable)
        if resizable:
            # 下限比"内容自然尺寸"小一些：既不让内容被拉没，也不强迫用户只能往大拉
            dialog.minsize(280, 240)

        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_y() + 24
        # 别把对话框摆到屏幕外面去（窗口贴着屏幕右下角时很容易发生）
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, min(x, screen_w - width - 8))
        y = max(0, min(y, screen_h - height - 8))
        dialog.geometry("%dx%d+%d+%d" % (width, height, x, y))
        return dialog

    def _fit_dialog(self, dialog, width: int):
        """
        让对话框的高度"长得下内容"，并且不超出屏幕。

        ★ 为什么需要它？（BUG-037 的教训）

            编辑弹窗和设置弹窗的宽高原来是【写死的】（340×520 / 340×500）。
            可是里面的控件高度会随【字号】变大：
                字号 ≤12 磅 -> 内容 514 像素，正好装得下
                字号 13 磅  -> 内容 526 像素，超出 6 像素
                字号 20 磅  -> 内容 764 像素，超出一大截
            超出去之后，底部那排按钮就再也分不到空间 ——
            用户看到的现象是"删除 / 标记完成 / 保存 / 取消 全都不见了"。
            而设置弹窗里的字号滑块允许调到 72 磅，
            也就是说**用户完全可以在界面上把自己调进"无法保存"的状态**。

        【怎么算？】
            先在不动手的情况下让 Tk 算一遍"内容自然需要多高"
            （update_idletasks + winfo_reqheight），
            再取「内容高度」和「屏幕高度 − 120」里较小的那个。

        【为什么留 120 像素？】
            给任务栏、窗口边框和屏幕边缘留点余地 ——
            否则大字号下弹窗会顶出屏幕，底部按钮反而更点不到。
        """
        try:
            dialog.update_idletasks()
            need_h = dialog.winfo_reqheight()
            screen_w = dialog.winfo_screenwidth()
            screen_h = dialog.winfo_screenheight()
            height = max(360, min(int(need_h), screen_h - 120))
            x = max(0, min(dialog.winfo_x(), screen_w - width - 8))
            y = max(0, min(dialog.winfo_y(), screen_h - height - 8))
            dialog.geometry("%dx%d+%d+%d" % (width, height, x, y))
            logger.debug("对话框尺寸自适应：内容需要 %d，实际用 %d", need_h, height)
        except tk.TclError:
            # 不许静默失败（AGENTS.md 3.2）：出错时保持原来的大小，但要留下痕迹
            logger.warning("对话框自适应尺寸失败，保持原尺寸", exc_info=True)

    def _form_label(self, master, text, row, column=0, sticky="w"):
        """表单左边的小标签。"""
        pal = self.pal
        label = tk.Label(master, text=text, bg=pal["bg"], fg=pal["muted"],
                         font=self._font(-2), anchor="w")
        label.grid(row=row, column=column, sticky=sticky, padx=(MARGIN, 2), pady=(4, 0))
        return label

    def _form_entry(self, master, row, columnspan=2, value="", width=None):
        """表单里的输入框（统一样式）。"""
        pal = self.pal
        var = tk.StringVar(value=value or "")
        entry = tk.Entry(master, textvariable=var, font=self._font(),
                         bg=pal["bg"], fg=pal["fg"], insertbackground=pal["fg"],
                         relief="flat", highlightthickness=1,
                         highlightbackground=pal["line"], highlightcolor=pal["accent"])
        if width:
            entry.configure(width=width)
        entry.grid(row=row, column=0, columnspan=columnspan, sticky="ew",
                   padx=MARGIN, pady=(0, 1))
        return var, entry

    def _form_option(self, master, row, column, value, choices, width=None):
        """表单里的下拉框（用 OptionMenu，样式已跟主题）。"""
        pal = self.pal
        var = tk.StringVar(value=value or "")
        box = tk.OptionMenu(master, var, *choices)
        try:
            box.configure(bg=pal["group_bg"], fg=pal["fg"], activebackground=pal["accent"],
                          activeforeground=contrast_text(pal["accent"]),
                          highlightthickness=0, relief="flat", font=self._font(-2),
                          padx=4, anchor="w")
            if width:
                box.configure(width=width)
            menu = box["menu"]
            menu.configure(bg=pal["group_bg"], fg=pal["fg"],
                           activebackground=pal["accent"],
                           activeforeground=contrast_text(pal["accent"]),
                           font=self._font(-2))
        except (tk.TclError, KeyError) as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或取值失败，忽略这次界面操作：%s", exc, exc_info=True)

        box.grid(row=row, column=column, sticky="w", padx=(MARGIN, 2))
        return var

    # ---------------- 编辑 / 新建 ----------------
    def open_add_dialog(self):
        """工具栏 ＋：打开"新建待办"对话框。"""
        self._open_task_dialog(None)

    def open_edit_dialog(self, task):
        """点一行：打开"编辑待办"对话框，并把它记为"当前选中的那条"。"""
        # 记下来，工具栏上的 ✎ 按钮才有明确的目标（见 open_selected_edit）
        try:
            self.selected_task_id = task.get("id")
        except AttributeError:
            self.selected_task_id = None
        self._open_task_dialog(task)

    def open_selected_edit(self):
        r"""
        工具栏 ✎：编辑"最近点过的那一条"。

        【为什么需要一个"选中"的概念？】
            编辑弹窗必须知道编辑哪一条，而工具栏按钮本身不带这个信息。
            点任务行时顺手把它记下来（self.selected_task_id），这个按钮才有意义。

        【找不到就不乱猜】
            没选过、或者选的那条已经不在列表里（完成了 / 被删了 / 换了筛选范围），
            就明确提示一句，绝不"随便挑一条打开" ——
            那会让用户以为程序在乱改他的数据。
        """
        target = None
        if self.selected_task_id is not None:
            for group in (self.payload or {}).get("groups") or []:
                for task in group.get("tasks") or []:
                    if task.get("id") == self.selected_task_id:
                        target = task
                        break
                if target:
                    break
        if target is None:
            self._set_hint("请先点一条待办把它选中，再点这里编辑")
            return
        self._open_task_dialog(target)

    def _open_task_dialog(self, task):
        """
        编辑 / 新建待办对话框 —— 按用户要求做成"类似离线待办清单软件里的编辑"。

        【v0.3.0 的版本被用户批评"功能过于简单"】，当时只有：
            标题 / 优先级 / 分类 / 提醒
        【v0.3.1 补齐成和网页版编辑弹窗一致】：
            标题 / 分类 / 优先级 / 截止时间 / 提醒时间 / 详细描述，
            外加"删除"和"标记完成"两个动作按钮。

        【为什么要重新拉一次数据？】
            列表最多是 10 秒前的快照。用它预填再保存会覆盖掉
            这 10 秒内的改动（"丢失更新"）。所以打开时重新拉一次。
        """
        editing = task is not None
        # ---- 先拉最新数据（拿不到就退回用列表里的那份，保证还能编辑）----
        if editing:
            try:
                fresh = self.api.get_task(task["id"]).get("task") or {}
                if fresh:
                    task = dict(task)
                    task.update({
                        "title": fresh.get("title", task.get("title")),
                        "category": fresh.get("category"),
                        "priority": fresh.get("priority", task.get("priority")),
                        "due_at": fresh.get("due_at"),
                        "remind_at": fresh.get("remind_at"),
                        "remind_enabled": fresh.get("remind_enabled", False),
                        "description": fresh.get("description"),
                    })
            except FloatingApiError as exc:
                self._set_hint("读不到最新内容，显示的是列表里的旧值：%s" % exc)

        pal = self.pal
        dialog = self._make_dialog("编辑待办" if editing else "新建待办", 340, 520)

        # ★【pack 顺序很重要（BUG-037 的教训）】—— 顺序就是下面这两行的先后。
        #   pack 是"按顺序分地盘"：先 pack 的先占，后来者只能捡剩下的。
        #   所以【按钮区必须排在表单前面】：
        #     · 字号小、内容装得下时：两者都够用，看不出区别；
        #     · 字号大、内容装不下时：按钮区先拿到自己的位置（按钮永远可见），
        #       被裁掉的是中间的描述框 —— 这是正确的取舍，
        #       因为"看不到一段描述"还能忍，"找不到保存按钮"是彻底不能用。
        #   （修之前是表单先 pack 且 expand=True，于是按钮区整排消失。）
        buttons = tk.Frame(dialog, bg=pal["bg"])
        buttons.pack(fill="x", side="bottom", padx=MARGIN, pady=(0, MARGIN))

        form = tk.Frame(dialog, bg=pal["bg"])
        form.pack(fill="both", expand=True, padx=0, pady=(4, 0))
        form.columnconfigure(0, weight=1)

        row = 0
        # ---- 标题 ----
        self._form_label(form, "标题 *", row); row += 1
        title_var, title_entry = self._form_entry(
            form, row, value=(task or {}).get("title", "")); row += 1

        # ---- 分类（下拉选已有的 + 可以直接打字新建）----
        self._form_label(form, "分类（待办类型）", row); row += 1
        category_var, category_entry = self._form_entry(
            form, row, value=(task or {}).get("category") or ""); row += 1

        # ★ 这一行无论有没有已有分类都要显示（OPT-027）。
        #   否则"第一次用、还没有任何分类"的用户会看到一个光秃秃的输入框，
        #   完全不知道这里可以自己起名字。
        chips = tk.Frame(form, bg=pal["bg"])
        chips.grid(row=row, column=0, columnspan=2, sticky="ew",
                   padx=MARGIN, pady=(2, 0))
        self._quick_chip(chips, "＋ 新分类", lambda: (
            category_var.set(""),
            category_entry.focus_set(),
            self._set_hint("在分类框里打个新名字，比如「健身」，保存后就记住了"),
        ))
        for name in ((self.payload or {}).get("categories") or [])[:5]:
            self._quick_chip(chips, name, lambda n=name: category_var.set(n))
        row += 1

        # ---- 优先级 ----
        self._form_label(form, "重要度（优先级）", row); row += 1
        priority_var = self._form_option(
            form, row, 0, config.PRIORITY_NAMES.get((task or {}).get("priority", 1), "中"),
            list(config.PRIORITY_NAMES.values())); row += 1

        # ---- 截止时间（可手打，也可以点 📅 从日历里挑）----
        due_var, due_entry = self._form_date_row(
            form, row, "截止时间（可留空）", (task or {}).get("due_at") or "")
        row += 2
        tk.Label(form, text="格式 2026-09-16 18:00；也可以只打 18:00 或 9-16，或者点右边的 📅",
                 bg=pal["bg"], fg=pal["muted"], font=self._font(-3),
                 anchor="w").grid(row=row, column=0, columnspan=2, sticky="w",
                                  padx=MARGIN)
        row += 1

        # ---- 提醒时间 + 快捷按钮 ----
        remind_var, remind_entry = self._form_date_row(
            form, row, "提醒时间（可留空）", (task or {}).get("remind_at") or "")
        row += 2

        quick = tk.Frame(form, bg=pal["bg"])
        quick.grid(row=row, column=0, columnspan=2, sticky="ew", padx=MARGIN, pady=(2, 0))
        for minutes in config.FLOATING_QUICK_REMIND_MINUTES:
            self._quick_chip(quick, "%d 分钟后" % minutes,
                             lambda m=minutes: self._fill_remind(remind_var, str(m)))
        self._quick_chip(quick, "今天 " + config.FLOATING_REMIND_TODAY_AT,
                         lambda: self._fill_remind(remind_var, "today"))
        self._quick_chip(quick, "明天 " + config.FLOATING_REMIND_TOMORROW_AT,
                         lambda: self._fill_remind(remind_var, "tomorrow"))
        self._quick_chip(quick, "清除", lambda: remind_var.set(""))
        row += 1

        # ---- 详细描述 ----
        self._form_label(form, "详细描述", row); row += 1
        desc = tk.Text(form, height=3, font=self._font(-1), bg=pal["bg"], fg=pal["fg"],
                       insertbackground=pal["fg"], relief="flat", highlightthickness=1,
                       highlightbackground=pal["line"], wrap="word")
        desc.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=MARGIN, pady=(0, 2))
        desc.insert("1.0", (task or {}).get("description") or "")
        # ★ 弹窗被拉高时，多出来的高度给"详细描述"框（用户要求弹窗能用鼠标调整大小）
        #   不给它 weight 的话，拉高只会多出一片空白，等于白拉。
        form.rowconfigure(row, weight=1)
        row += 1

        # ---- 底部按钮 ----
        #   （buttons 这个 Frame 在函数开头就已经 pack 好了 —— 见那里的说明）
        self._dialog_error_label = tk.Label(buttons, text="", bg=pal["bg"],
                                            fg=pal["warn"], font=self._font(-3),
                                            anchor="w", wraplength=180)
        self._dialog_error_label.pack(side="left")

        def submit():
            r"""
            保存（编辑 / 新建共用）。

            ★★【v0.4.3：新建改成了"一次请求、要么全成要么全不成"】

                【用户报的问题】
                    「悬浮窗新建待办，设置提醒日期时日期格式错误会导致整个待办消失」

                【原来的写法为什么会"消失"】
                    新建分两次请求：先 quick_add 建任务，再 update_task 补
                    截止时间和描述。
                      · 第一次失败（提醒时间格式不对）-> 任务【根本没建】，
                        标题、分类、描述全白填 —— 这就是用户看到的"整个消失"；
                      · 第二次失败（截止时间格式不对）-> 任务建了但缺字段，
                        弹窗却关掉了，用户以为没存上，于是重新输一遍 -> 多出一条。

                    现在改成一次把所有字段都送过去：
                    **成功就是完整的一条，失败就一条都不产生，弹窗不关、输入不丢。**
            """
            changes = self._collect_task_form(
                dialog, task, title_var, category_var, priority_var,
                due_var, remind_var, desc)
            if changes is None:
                return
            try:
                if editing:
                    result = self.api.update_task(task["id"], changes)
                else:
                    result = self.api.quick_add(
                        changes.get("title"),
                        priority=changes.get("priority"),
                        category=changes.get("category") or None,
                        remind_at=changes.get("remind_at") or None,
                        due_at=changes.get("due_at") or None,
                        description=changes.get("description") or None,
                    )
            except FloatingApiError as exc:
                # ★ 失败时【不关弹窗、不清空输入】—— 用户改一处就能重试，
                #   而不是从头再打一遍（这正是"待办消失"让人恼火的地方）
                self._show_dialog_error(str(exc))
                return
            dialog.destroy()
            self._set_hint(result.get("message", "已保存"))
            self.refresh(force=True)

        # ---- 左下的动作按钮（编辑时才有）----
        if editing:
            tk.Button(buttons, text="删除", command=lambda: (dialog.destroy(),
                                                            self.on_delete(task)),
                      font=self._font(-1), bg=pal["group_bg"], fg=pal["warn"],
                      relief="flat", padx=8).pack(side="left", padx=(0, 4))
            tk.Button(buttons, text="标记完成" if not task.get("is_done") else "取消完成",
                      command=lambda: (dialog.destroy(), self.on_toggle(task)),
                      font=self._font(-1), bg=pal["group_bg"], fg=pal["fg"],
                      relief="flat", padx=8).pack(side="left")

        tk.Button(buttons, text="保存", command=submit, font=self._font(-1),
                  bg=pal["accent"], fg=contrast_text(pal["accent"]),
                  relief="flat", padx=12).pack(side="right")
        tk.Button(buttons, text="取消", command=dialog.destroy, font=self._font(-1),
                  bg=pal["group_bg"], fg=pal["fg"], relief="flat",
                  padx=10).pack(side="right", padx=6)

        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.bind("<Return>", lambda _e: submit())
        # ★ 内容都摆完了，最后按"实际需要的高度"把弹窗调整到装得下（BUG-037）
        self._fit_dialog(dialog, 340)
        title_entry.focus_set()
        return dialog

    def _quick_chip(self, master, text, command):
        """表单里的"快捷项"小标签（点一下就填进去）。"""
        pal = self.pal
        chip = tk.Label(master, text=text, bg=pal["group_bg"], fg=pal["accent"],
                        font=self._font(-3), padx=5, pady=1, cursor="hand2")
        chip.pack(side="left", padx=(0, 3))
        chip.bind("<Button-1>", lambda _e: command())
        chip.bind("<Enter>", lambda _e: chip.configure(bg=pal["accent"],
                                                       fg=contrast_text(pal["accent"])))
        chip.bind("<Leave>", lambda _e: chip.configure(bg=pal["group_bg"],
                                                       fg=pal["accent"]))
        return chip

    def _fill_remind(self, remind_var, choice: str):
        """
        把快捷选项换算成具体时间填进输入框。

        【为什么换算要问服务端？】
            因为接口会校验"提醒时间不能早于当前时间"。
            如果换算在窗口进程里做、校验在服务进程里做，
            两边时钟只要差几秒，就会出现
            "我明明选了 10 分钟后，却提示时间早于当前"这种莫名其妙的错误。
            用服务端的钟算，自相矛盾从结构上就不可能发生。
        """
        try:
            remind_var.set(self.api.remind_at(choice))
        except FloatingApiError as exc:
            self._set_hint("算不出提醒时间：%s" % exc)

    def _show_dialog_error(self, message: str):
        r"""
        在对话框里显示错误（不用 messagebox —— 那个会打断操作流程）。

        ★ v0.4.3：把 "HTTP 422：" 这类技术前缀去掉再显示。
          用户关心的是"我哪里写错了、该怎么改"，
          "HTTP 422" 对他来说只是噪音（真正的排查信息在日志里）。
        """
        text = re.sub(r"^HTTPs*d+s*[:：]s*", "", str(message or ""))
        try:
            self._dialog_error_label.configure(text=text)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def _collect_task_form(self, dialog, task, title_var, category_var, priority_var,
                           due_var, remind_var, desc_widget) -> dict:
        """
        把表单内容整理成"提交给接口的字段"。

        Returns:
            dict  -> 要提交的字段
            None  -> 有错（已经在界面上提示了）
        """
        changes = {}

        title = title_var.get().strip()
        if not title:
            self._show_dialog_error("标题不能为空")
            return None
        changes["title"] = title

        for value, name in config.PRIORITY_NAMES.items():
            if name == priority_var.get():
                changes["priority"] = value
                break

        changes["category"] = category_var.get().strip() or None

        # ---- 时间：先做"体贴的补全"，再交给服务端严格校验 ----
        due = normalize_datetime(due_var.get())
        remind = normalize_datetime(remind_var.get())
        changes["due_at"] = due or None
        changes["remind_at"] = remind or None
        # 【填了提醒时间就自动打开提醒开关】—— 和网页版的规则一致。
        # 【为什么"清空提醒时间"时也要显式传 remind_enabled=False？】
        #   不传的话，服务端会检查"开了提醒但没有提醒时间"然后报错：
        #   "开启了提醒，就必须填写提醒时间"—— 用户会莫名其妙，
        #   他明明是想取消提醒。
        changes["remind_enabled"] = bool(remind)

        description = desc_widget.get("1.0", "end").strip()
        changes["description"] = description or None

        return changes

    def _open_calendar_picker(self, target_var, anchor_widget, allow_date_only=False):
        """
        弹出一个小日历，点一个日期就填进 target_var。

        ★ 这是 OPT-029 的实现：用户反馈"编辑功能过于简单"之后补上的。
          网页版的截止/提醒时间用的是浏览器自带的 ``<input type="datetime-local">``，
          点一下就有日历。而 tkinter 没有现成控件 —— 只能自己画。

        【为什么自己做，而不是直接让用户手打？】
            手打有个绕不过去的问题：**用户不知道"今天是几号、这周几号是周五"。**
            他脑子里想的是"下周三"，但要手打成 ``2026-09-23``，
            中间那一步换算很容易算错（月末、跨月更容易错）。
            日历把这一步变成"看一眼、点一下"，是这类输入最实在的改进。

        【交互设计上做的两个取舍】
            ① **允许留空**：这一栏本来就是可选的，所以面板底部有「清空」。
            ② **时间不丢**：只选日期时，如果原值已经有时间，就保留原来的时刻。
               否则用户改个日期还得把 "18:00" 重新打一遍 —— 那比手打还烦。

        参数：
            target_var    —— 要把结果填进哪个 StringVar
            anchor_widget —— 面板弹在哪个控件旁边（用它的屏幕坐标）
            allow_date_only —— True 表示只填日期（"2026-09-16"），
                               False 表示带上时间（"2026-09-16 09:00"）
        """
        pal = self.pal
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)          # 无边框，像"弹出的面板"而不是窗口
        panel.configure(bg=pal["bg"])
        panel.attributes("-topmost", True)

        # ---- 起算的月份：优先用当前值里的日期，否则用今天 ----
        current = normalize_datetime(target_var.get())
        try:
            base = datetime.strptime(current[:10], "%Y-%m-%d") if current else datetime.now()
        except ValueError:
            base = datetime.now()

        state = {"year": base.year, "month": base.month,
                 "chosen": base.date(), "keep_time": current[11:16] if len(current) >= 16 else ""}

        header = tk.Frame(panel, bg=pal["group_bg"])
        header.pack(fill="x")
        title = tk.Label(header, text="", bg=pal["group_bg"], fg=pal["accent"],
                         font=self._font(-1, bold=True), width=10)
        title.pack(side="left", padx=2)

        grid_holder = tk.Frame(panel, bg=pal["bg"])
        grid_holder.pack(fill="both", expand=True, padx=4, pady=2)
        footer = tk.Frame(panel, bg=pal["bg"])
        footer.pack(fill="x", padx=4, pady=(0, 4))

        def close(_event=None):
            try:
                panel.grab_release()
                panel.destroy()
            except tk.TclError as exc:
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


        def shift_month(delta):
            month = state["month"] + delta
            year = state["year"]
            while month < 1:
                month += 12
                year -= 1
            while month > 12:
                month -= 12
                year += 1
            state["month"] = month
            state["year"] = year
            render()

        def pick(day: date):
            """选中某一天：拼成字符串填回输入框，然后关掉面板。"""
            if allow_date_only:
                value = day.strftime("%Y-%m-%d")
            else:
                clock = state["keep_time"] or config.FLOATING_REMIND_TOMORROW_AT
                value = day.strftime("%Y-%m-%d ") + clock
            target_var.set(value)
            close()

        def render():
            title.configure(text="%d 年 %d 月" % (state["year"], state["month"]))
            for child in grid_holder.winfo_children():
                child.destroy()

            today = date.today()
            # 【为什么用 weekday() 算偏移？】它返回 0=周一 … 6=周日。
            #   我们第一列是周一，所以偏移就是它的值，不用额外换算。
            first = date(state["year"], state["month"], 1)
            offset = first.weekday()
            days_in_month = (date(state["year"] + (state["month"] == 12),
                                  (state["month"] % 12) + 1, 1) - first).days

            for index, name in enumerate(("一", "二", "三", "四", "五", "六", "日")):
                tk.Label(grid_holder, text=name, bg=pal["bg"], fg=pal["muted"],
                         font=self._font(-3), width=3).grid(row=0, column=index)

            row, column = 1, offset
            for day_number in range(1, days_in_month + 1):
                day = date(state["year"], state["month"], day_number)
                is_today = (day == today)
                is_chosen = (day == state["chosen"])
                if is_chosen:
                    bg, fg = pal["accent"], contrast_text(pal["accent"])
                elif is_today:
                    bg, fg = pal["group_bg"], pal["warn"]
                else:
                    bg, fg = pal["bg"], pal["fg"]

                cell = tk.Label(grid_holder, text=str(day_number), bg=bg, fg=fg,
                                font=self._font(-2, bold=is_chosen),
                                width=3, cursor="hand2")
                cell.grid(row=row, column=column, padx=1, pady=1)
                cell.bind("<Button-1>", lambda _e, d=day: pick(d))
                # 悬停高亮（不改前景色，避免把"今天"的红色标记抹掉）
                if not is_chosen:
                    cell.bind("<Enter>", lambda _e, c=cell: c.configure(bg=pal["group_bg"]))
                    cell.bind("<Leave>", lambda _e, c=cell, b=bg: c.configure(bg=b))

                column += 1
                if column > 6:
                    column = 0
                    row += 1

        # ---- 左右翻月 ----
        for text, delta, side in (("‹", -1, "left"), ("›", 1, "left")):
            btn = tk.Label(header, text=text, bg=pal["group_bg"], fg=pal["accent"],
                           font=self._font(1, bold=True), padx=6, cursor="hand2")
            btn.pack(side=side, padx=(2 if side == "left" else 0, 2))
            btn.bind("<Button-1>", lambda _e, d=delta: shift_month(d))
        # 【为什么先 pack 右箭头会反？】tkinter 的 pack(side="left") 是"从左往右"排，
        #   所以上面先 pack 了 "‹" 再 pack "›"，顺序正好是 ‹ ›。标题在它们右边。

        # ---- 底部按钮 ----
        #
        # ⚠️【这里踩过一个坑，见 BUG-033】原来写的是链式调用：
        #       tk.Label(...).pack(side="left").bind("<Button-1>", ...)
        #   但 tkinter 的 pack()/grid()/place() 【不返回控件】，返回的是 None，
        #   于是 .bind() 变成"在 None 上调方法"—— 立刻抛 AttributeError。
        #   更要命的是异常发生在函数【中途】：面板已经建出来了，
        #   后面的 render()/定位/grab_set()/Esc 绑定全都没执行到，
        #   用户看到的是一个"空空的小方块"，还关不掉。
        #   规矩：先建控件、再布局、最后绑定，三步分开写。
        def footer_button(text, command, side="left", padx=0, fg=None):
            """底部快捷键按钮（建 -> 布局 -> 绑定，绝不链式调用）。"""
            label = tk.Label(footer, text=text, bg=pal["group_bg"],
                             fg=fg or pal["accent"], font=self._font(-3),
                             padx=6, cursor="hand2")
            label.pack(side=side, padx=padx)
            label.bind("<Button-1>", lambda _e: command())
            return label

        # 【"下周一"为什么不是 +7 天？】
        #   "下周一"在中文里指的是"下一个周一"，不是"七天后"。
        #   weekday() 是 0=周一，所以距离下一个周一 = (7 - 今天weekday) % 7，
        #   如果今天正好是周一，那"下周一"就是 7 天后（不能取 0）。
        def next_monday():
            today = date.today()
            delta = (7 - today.weekday()) % 7 or 7
            pick(today + timedelta(days=delta))

        footer_button("今天", lambda: pick(date.today()))
        footer_button("明天", lambda: pick(date.today() + timedelta(days=1)), padx=3)
        footer_button("下周一", next_monday)
        footer_button("清空", lambda: (target_var.set(""), close()),
                      side="right", fg=pal["muted"])

        # ---- 只有"带时间"的那种才显示时间输入 ----
        if not allow_date_only:
            time_row = tk.Frame(panel, bg=pal["bg"])
            time_row.pack(fill="x", padx=4, pady=(0, 4))
            tk.Label(time_row, text="时间", bg=pal["bg"], fg=pal["muted"],
                     font=self._font(-3)).pack(side="left")
            time_var = tk.StringVar(value=state["keep_time"] or
                                    config.FLOATING_REMIND_TOMORROW_AT)
            entry = tk.Entry(time_row, textvariable=time_var, width=6,
                             font=self._font(-1), bg=pal["bg"], fg=pal["fg"],
                             insertbackground=pal["fg"], relief="flat",
                             highlightthickness=1, highlightbackground=pal["line"])
            entry.pack(side="left", padx=4)
            tk.Label(time_row, text="HH:MM", bg=pal["bg"], fg=pal["muted"],
                     font=self._font(-3)).pack(side="left")

            def sync_time(*_args):
                text = (time_var.get() or "").strip()
                if re.fullmatch(r"\d{1,2}:\d{2}", text):
                    hour, minute = (int(x) for x in text.split(":"))
                    if hour <= 23 and minute <= 59:
                        state["keep_time"] = "%02d:%02d" % (hour, minute)

            time_var.trace_add("write", sync_time)

        render()

        # ---- 摆位置：贴着输入框的下方 ----
        panel.update_idletasks()
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height() + 2
        # 别把面板摆到屏幕外面（输入框在屏幕右下角时很容易发生）
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(panel.winfo_reqwidth(), 200)
        height = panel.winfo_reqheight()
        x = max(0, min(x, screen_w - width - 4))
        y = max(0, min(y, screen_h - height - 4))
        panel.geometry("+%d+%d" % (x, y))

        # 点面板外面就关掉（grab_set 让所有点击先到面板这里）
        try:
            panel.grab_set()
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        panel.bind("<FocusOut>", close)
        panel.bind("<Escape>", close)
        return panel

    def _form_date_row(self, master, row, label, value, allow_date_only=False):
        """
        一行"日期输入 + 日历按钮"。

        输入框本身仍然可以直接打字（老用户可能更习惯），
        但右边多了一个 📅 按钮 —— 想不起来"下周三几号"的时候点它。
        """
        self._form_label(master, label, row)
        var, entry = self._form_entry(master, row + 1, value=value)
        button = tk.Label(master, text="📅", bg=self.pal["group_bg"],
                          fg=self.pal["accent"], font=self._font(0),
                          padx=5, cursor="hand2")
        button.grid(row=row + 1, column=0, sticky="e", padx=(0, MARGIN))
        button.bind("<Button-1>",
                    lambda _e: self._open_calendar_picker(var, entry, allow_date_only))
        return var, entry

    # ---------------- 设置 ----------------
    def open_settings_dialog(self):
        """
        右上角 ⚙ 打开的设置弹窗 —— 用户点名要的那几项：

            字号 / 背景颜色 / 透明度 / 字体 / 字体颜色 / 是否关闭悬浮窗

        【为什么这些设置"改一下就能看到效果"？】
            因为每个控件改完都立刻调用 _patch_config() 写服务端，
            窗口每 3 秒的 poll_config() 会发现自己变了，然后重画 / 调透明度。
            所以用户拖动滑块时，能实时看到效果（最多 3 秒延迟）。
        """
        pal = self.pal
        cfg = self.config_data
        dialog = self._make_dialog("悬浮窗设置", 340, 500)

        # ★【pack 顺序（BUG-037）】按钮区先占位，表单吃剩下的。
        #   理由和编辑弹窗那边完全一样：字号调到很大时，
        #   如果表单先 pack 又带 expand，按钮区会分不到空间而整排消失。
        actions = tk.Frame(dialog, bg=pal["bg"])
        actions.pack(fill="x", side="bottom", padx=MARGIN, pady=MARGIN)

        form = tk.Frame(dialog, bg=pal["bg"])
        form.pack(fill="both", expand=True, padx=0, pady=(6, 0))
        form.columnconfigure(0, weight=1)

        # ★ 弹窗可以被鼠标拉大拉小（用户 2026-09-14 要求）。
        #   拉宽之后滑块跟着变长，不然右边会空出一大片、显得没做完。
        _scales = []

        def _on_dialog_resize(event):
            if event.widget is not dialog:
                return
            for _sc in _scales:
                try:
                    _sc.configure(length=max(120, event.width - 70))
                except tk.TclError as exc:
                    logger.debug("调整滑块长度失败：%s", exc, exc_info=True)

        dialog.bind("<Configure>", _on_dialog_resize)

        # 滑块旁边的实时数值
        def add_slider(row, label, from_, to, value, fmt, apply_):
            self._form_label(form, label, row)
            value_label = tk.Label(form, text=fmt(value), bg=pal["bg"], fg=pal["accent"],
                                   font=self._font(-2), anchor="e")
            value_label.grid(row=row, column=1, sticky="e", padx=(0, MARGIN))
            scale = tk.Scale(form, from_=from_, to=to, orient="horizontal",
                             resolution=1, showvalue=False, bg=pal["bg"], fg=pal["fg"],
                             troughcolor=pal["group_bg"], highlightthickness=0,
                             sliderrelief="flat", activebackground=pal["accent"],
                             length=190)
            scale.set(value)
            scale.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=MARGIN)

            def on_change(_value=None, commit=False):
                v = scale.get()
                value_label.configure(text=fmt(v))
                if commit:
                    apply_(v)

            scale.configure(command=lambda v: on_change(v, commit=False))
            scale.bind("<ButtonRelease-1>", lambda _e: on_change(None, commit=True))
            _scales.append(scale)
            return scale

        row = 0
        # ---- 字号 ----
        add_slider(row, "字号",
                   config.FLOATING_MIN_FONT_SIZE, config.FLOATING_MAX_FONT_SIZE,
                   int(cfg.get("font_size", config.FLOATING_DEFAULT_FONT_SIZE)),
                   lambda v: "%d 磅" % v,
                   lambda v: self._apply_setting({"font_size": int(v)}))
        row += 2

        # ---- 透明度 ----
        #
        # ★ 这一项旁边必须把"两个不同的概念"说清楚（用户专门澄清过）：
        #     清晰度（锐利）= 笔画边缘不糊 -> 由 DPI 感知保证，和透明度无关
        #     对比度（读得出）= 字和背景的亮度差 -> 会随透明度下降而降低
        #   所以文案不能说"调低会发虚"（那是错的），要说"桌面会透进来、
        #   字和背景的差别变小、变难读"。
        self._form_label(form, "背景不透明度（透明度 = 100% − 这个数）", row)
        opacity_value_label = tk.Label(form, text="", bg=pal["bg"], fg=pal["accent"],
                                       font=self._font(-2), anchor="e")
        opacity_value_label.grid(row=row, column=1, sticky="e", padx=(0, MARGIN))
        opacity_scale = tk.Scale(
            form, from_=int(config.FLOATING_MIN_OPACITY * 100), to=100,
            orient="horizontal", resolution=1, showvalue=False,
            bg=pal["bg"], fg=pal["fg"], troughcolor=pal["group_bg"],
            highlightthickness=0, sliderrelief="flat",
            activebackground=pal["accent"], length=190)
        opacity_scale.set(int(round(float(cfg.get("opacity", 0.70)) * 100)))
        opacity_scale.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=MARGIN)

        clarity_note = tk.Label(form, text="", bg=pal["bg"],
                                fg=pal["muted"], font=self._font(-3),
                                anchor="w", justify="left", wraplength=300)
        clarity_note.grid(row=row + 2, column=0, columnspan=2, sticky="w", padx=MARGIN)

        def refresh_opacity_note(value):
            """更新不透明度的数值显示和可读性提醒。"""
            opacity_value_label.configure(text="%d%%" % value)
            transparency = 100 - value
            if value < config.FLOATING_CLARITY_WARN_BELOW * 100:
                clarity_note.configure(
                    fg=pal["warn"],
                    text="⚠️ 现在透明度 %d%%：桌面会明显透进来，"
                         "字和背景的差别变小、读起来会吃力。"
                         "（字的笔画本身还是锐利的 —— 那种『糊』是缩放造成的，"
                         "已经修掉了；这里损失的是对比度。）" % transparency)
            else:
                clarity_note.configure(
                    fg=pal["muted"],
                    text="当前透明度 %d%%。字始终是锐利的；"
                         "调得越透，桌面透进来越多、字越难读（对比度下降）。"
                         % transparency)

        def on_opacity_change(_value=None, commit=False):
            v = opacity_scale.get()
            refresh_opacity_note(v)
            if commit:
                self._apply_setting({"opacity": round(v / 100.0, 2)})

        opacity_scale.configure(command=lambda v: on_opacity_change(v, commit=False))
        opacity_scale.bind("<ButtonRelease-1>", lambda _e: on_opacity_change(None, commit=True))
        refresh_opacity_note(opacity_scale.get())
        row += 3

        # ---- 字体 ----
        self._form_label(form, "字体", row)
        family_var = self._form_option(
            form, row, 1, cfg.get("font_family", config.FLOATING_DEFAULT_FONT_FAMILY),
            list(config.FLOATING_FONT_PRESETS))
        row += 1
        self._form_label(form, "（也可以直接手打别的字体名）", row)
        row += 1
        custom_var, custom_entry = self._form_entry(
            form, row, value=cfg.get("font_family", ""))
        row += 1

        def apply_font(*_args):
            name = (custom_var.get() or "").strip() or family_var.get()
            if name:
                self._apply_setting({"font_family": name})

        custom_entry.bind("<Return>", apply_font)
        custom_entry.bind("<FocusOut>", apply_font)
        # 下拉框选了就同步到输入框，然后应用
        _orig_trace = family_var.trace_add(
            "write", lambda *a: (custom_var.set(family_var.get()), apply_font()))

        # ---- 文字颜色 ----
        self._form_label(form, "文字颜色 / 背景颜色", row)
        colors = tk.Frame(form, bg=pal["bg"])
        colors.grid(row=row, column=1, sticky="e", padx=(0, MARGIN))
        row += 1
        self._color_picker(colors, cfg.get("fg_color", "#FFFFFF"),
                           lambda v: self._apply_setting({"fg_color": v}), "文字")
        self._color_picker(colors, cfg.get("bg_color", "#000000"),
                           lambda v: self._apply_setting({"bg_color": v}), "背景")

        # ---- 一个开关 + 两个动作 ----
        self._form_label(form, "总在最前面", row)
        top_var = tk.BooleanVar(value=bool(cfg.get("always_on_top", False)))
        chk = tk.Checkbutton(form, variable=top_var, bg=pal["bg"], fg=pal["fg"],
                             activebackground=pal["bg"], activeforeground=pal["fg"],
                             selectcolor=pal["group_bg"], highlightthickness=0,
                             command=lambda: self._apply_setting(
                                 {"always_on_top": top_var.get()}))
        chk.grid(row=row, column=1, sticky="e", padx=(0, MARGIN))
        row += 1
        tk.Label(form, text="关掉它，悬浮窗就会被别的窗口盖住（默认就是关的）",
                 bg=pal["bg"], fg=pal["muted"], font=self._font(-3),
                 anchor="w").grid(row=row, column=0, columnspan=2, sticky="w",
                                  padx=MARGIN)
        row += 1

        # （actions 这个 Frame 在函数开头就已经 pack 好了 —— 见那里的说明）
        tk.Button(actions, text="关闭悬浮窗", font=self._font(-1),
                  bg=pal["group_bg"], fg=pal["warn"], relief="flat", padx=10,
                  command=lambda: self._close_floating(dialog)).pack(side="left")
        tk.Button(actions, text="完成", font=self._font(-1),
                  bg=pal["accent"], fg=contrast_text(pal["accent"]),
                  relief="flat", padx=14,
                  command=dialog.destroy).pack(side="right")

        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        # ★ 同编辑弹窗：按内容实际需要的高度自适应（BUG-037）
        self._fit_dialog(dialog, 340)
        return dialog

    def _color_picker(self, master, current, apply_, label=""):
        """
        一个色块按钮。

        【为什么用"色块 + 弹出调色盘"而不是 tkinter 的 colorchooser？】
            tkinter 自带的 colorchooser 会弹出一个**系统对话框**，
            它会抢占焦点、而且每次都要点"确定"才能看到效果 ——
            调个颜色要开关好几次窗口，体验很差。
            这里改成：一点就弹出一排预设色块，点一下立刻生效、窗口自动关。
            代价是只能选预设色，但悬浮窗的配色本来就只需要几个基调色。
        """
        pal = self.pal
        btn = tk.Label(master, text="  ", bg=current, width=3,
                       relief="solid", borderwidth=1, cursor="hand2")
        btn.pack(side="left", padx=3)
        if label:
            btn.configure(text=label, fg=contrast_text(current), font=self._font(-3))

        def popup(_event=None):
            menu = tk.Menu(self.root, tearoff=0)
            presets = ["#FFFFFF", "#000000", "#1F2933", "#E8EAED", "#2F6FED",
                       "#FFD166", "#D93025", "#2E7D32", "#E8F5E9", "#FFF8E1"]
            for color in presets:
                menu.add_command(label=color, background=color,
                                 foreground=contrast_text(color),
                                 command=lambda c=color: (apply_(c),
                                                          btn.configure(bg=c,
                                                                        fg=contrast_text(c))))
            try:
                menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
            finally:
                menu.grab_release()

        btn.bind("<Button-1>", popup)
        return btn

    def _apply_setting(self, changes: dict):
        """
        改一项设置：立刻写到服务端，并【立刻】应用到窗口上。

        【为什么不只写服务端、等 3 秒后的 poll 生效？】
            因为用户是在设置弹窗里拖滑块，期待"所见即所得"。
            等 3 秒才变，他会以为滑块坏了、然后反复拖。
            所以：写服务端（持久化）+ 本地立刻应用（反馈），两件事都做。
            本地应用完，服务端那份也是同一个值，3 秒后 poll 不会产生冲突。
        """
        self.config_data.update(changes)
        # 立刻应用（不重建整个界面，避免设置弹窗背后的窗口闪一下）
        if "opacity" in changes:
            try:
                self.root.attributes("-alpha", float(changes["opacity"]))
            except tk.TclError as exc:
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        if "always_on_top" in changes:
            try:
                self.root.attributes("-topmost", bool(changes["always_on_top"]))
            except tk.TclError as exc:
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        # 字号 / 字体 / 颜色 需要重建界面才对 ——
        # 但重建会把设置弹窗背后的东西全部重画（视觉上会闪），
        # 所以只标记一下，等用户关掉设置弹窗后由 poll_config 统一重建。
        self._patch_config(changes, quiet=True)

    def _close_floating(self, dialog):
        """
        设置弹窗里的「关闭悬浮窗」= 真正关掉功能（不是隐藏）。

        【和标题栏那个 ✕ 的区别，一定要分清】
            ✕        -> 只是隐藏（设置里的"已开启"还留着，下次开机还会出现）
            「关闭悬浮窗」-> 关掉总开关（下次开机不会出现了）

        用户反馈里明确说"设置按钮…可以…是否关闭悬浮窗"，
        所以这个动作要放在设置里面 —— 因为它是"改功能状态"，
        而不是"收起窗口"这种日常动作。
        """
        try:
            dialog.destroy()
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        self._patch_config({"enabled": False, "visible": False}, quiet=True)
        try:
            self.api.quit_window()
        except FloatingApiError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("服务端调用失败（调用方负责提示用户）：%s", exc, exc_info=True)

        self.shutdown()

    # ------------------------------------------------------------------
    # 刷新与轮询
    # ------------------------------------------------------------------
    def refresh(self, force: bool = False):
        """
        重新拉数据并重画。

        【关键优化：指纹比较】
            只有"内容真的变了"才重画。没变就什么都不做。
            否则每 10 秒把上百个控件删掉重建一次 —— 费 CPU 不说，
            用户正在看/正在滚动的时候列表还会闪。
            （这就是为什么服务端要返回 signature 字段。）

        ★【v0.3.1 加的兜底：画错一行不能让整个窗口消失】
            BUG-028 的教训：_render_task 里一个参数写错（padx 传了元组），
            TclError 一路冒到 main()，窗口进程直接退出 ——
            用户看到的是"窗口闪一下就没了"，日志里才有原因。
            所以这里改成：**画出错只记日志 + 在界面上说一声**，
            窗口继续活着（能刷新、能拖动），用户有机会看到提示。
            一个"能报错的窗口"比一个"默默消失的窗口"有用得多。
        """
        try:
            payload = self.api.get_data(force=force)
        except FloatingApiError as exc:
            self._set_hint("连不上服务：%s" % exc)
            return

        signature = payload.get("signature")
        group_by = (payload.get("config") or {}).get("group_by")
        # 分块方式变了也要重画（title 里带了日期，指纹只覆盖任务内容）
        if not force and signature == self._last_signature and \
                group_by == self.config_data.get("group_by"):
            return
        self._last_signature = signature
        self.payload = payload

        # 服务端返回的设置是"权威版本"：用户可能在网页里改过外观了
        new_config = payload.get("config") or {}
        need_rebuild = self._appearance_changed(new_config)
        need_geometry = self._geometry_changed(new_config)
        self.config_data = new_config

        try:
            if need_rebuild:
                # 外观变了：整个界面重建（着色不能在原地改，重建最干净）
                for child in self.root.winfo_children():
                    child.destroy()
                self._build()
                self.apply_geometry()
                logger.info("设置变化，界面已重建：%s",
                            {k: new_config.get(k) for k in
                             ("bg_color", "fg_color", "font_family", "font_size")})
            else:
                self._update_toolbar()
                self._render_groups()
                if need_geometry:
                    self.apply_geometry()
        except tk.TclError:
            logger.exception("画界面时出错（窗口会继续运行，但这一帧可能不完整）")
            self._set_hint("界面刷新出错，详情见 data/floating/window.log")
        except Exception:                       # noqa: BLE001 - 兜底，别让窗口死掉
            logger.exception("刷新时出现未预期的错误")

    def _appearance_changed(self, new_config: dict) -> bool:
        """
        判断"和外观有关的设置"有没有变（变了就得重建界面）。

        ⚠️ 这个列表必须包含【所有会影响界面长相】的设置。
           漏一个的后果是：用户改了它，窗口毫无反应，看起来像设置坏了。
           BUG-026 就是这么来的 —— 当时漏了 width / height，
           于是"改窗口大小"要等到用户再改一次透明度（命中列表里的项）
           才顺带生效。这种"改 A 要碰 B 才生效"的现象最难排查。

        【为什么还有一条"防复发"测试？】
           见 tests/test_gui_utils.py::TestConfigChangeDetection
           最后那条 test_every_config_field_is_watched_by_something ——
           它会自动检查"新增的设置项有没有被某个判断函数盯上"。
        """
        keys = ("bg_color", "fg_color", "accent_color", "font_family", "font_size")
        if not self.config_data:
            return False
        return any(self.config_data.get(k) != new_config.get(k) for k in keys)

    def _geometry_changed(self, new_config: dict) -> bool:
        """位置 / 大小 / 透明度 / 置顶变了（这些可以"轻量地"改，不用重建界面）。"""
        keys = ("x", "y", "width", "height", "opacity", "always_on_top")
        if not self.config_data:
            return False
        return any(self.config_data.get(k) != new_config.get(k) for k in keys)

    def apply_geometry(self):
        """
        按设置里的位置和大小调整窗口几何。

        ★ 这是修掉 BUG-026 时补上的函数。

        【原来的问题是什么？】
            位置和大小只在【启动时】读一次（在 _setup_window 里）。
            如果用户之后改了窗口尺寸，窗口虽然会重建界面，
            却仍然保持着旧的外框大小 —— 看起来就像"设置没生效"。

        【为什么单独抽成一个函数，而不是塞进 _setup_window？】
            因为 _setup_window 里还做了一堆"只能做一次"的事：
                overrideredirect（去边框）、-toolwindow（不进任务栏）……
            重复执行它们会让窗口闪烁、甚至丢焦点。
            而"改位置和大小"是随时可以做、且必须能重复做的动作。
            两者分开之后，语义就清楚了：
                _setup_window  = 出生时办一次手续
                apply_geometry = 之后每次想挪动/缩放都调它
        """
        cfg = self.config_data
        width = int(cfg.get("width", config.FLOATING_DEFAULT_WIDTH))
        height = int(cfg.get("height", config.FLOATING_DEFAULT_HEIGHT))
        x, y = int(cfg.get("x", 0)), int(cfg.get("y", 0))

        # 【为什么这里只夹位置，不自己改 width/height？】
        #   这是排查 OPT-024 时改的：原来这里会在超出屏幕时
        #   偷偷算一个"夹过的新尺寸"，但那个新尺寸只写回 tkinter，
        #   没有写回 config_data —— 于是下次比较时又判定"变了"，
        #   来回折腾。现在位置和尺寸各管各的：位置夹、尺寸不动。
        x, y = self._clamp_position(x, y, width, height)

        try:
            self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))
            # 内容区要跟着新宽度重新折行，否则文字还是按旧宽度排的
            self._reflow(width)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


        # 透明度 / 置顶也是"可以随时改"的，一起放在这里
        try:
            self.root.attributes("-alpha", float(cfg.get("opacity", 0.5)))
            self.root.attributes("-topmost", bool(cfg.get("always_on_top", False)))
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def _update_toolbar(self):
        """工具栏上会变的东西：未完成条数、分类按钮的文字。"""
        try:
            # 标题文字交给 _fit_toolbar_title() 决定（它会按剩余宽度缩写）
            self._fit_toolbar_title()

            group_name = config.FLOATING_GROUP_BY_NAMES.get(
                self.config_data.get("group_by", "date"), "按日期分块")
            short = group_name.replace("按", "").replace("分块", "")
            self.group_button.configure(text="%s ▾" % short)
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def poll_config(self):
        """
        每 3 秒问一次服务端："设置改了没？"

        【为什么不能只靠"刷新数据"顺带拿设置？】
            因为数据可能很久都不变（指纹判断会直接 return 掉），
            那样用户在网页里改了透明度，窗口要等到"数据变了"才跟着变 ——
            看起来就像设置没生效。
            所以设置单独问一次，只花几百字节。
        """
        if self._stop:
            return
        try:
            data = self.api.get_config()
            new_config = (data or {}).get("config") or {}
            # 先把"变了没"判断完，再覆盖 config_data —— 顺序反了就永远判断为"没变"
            need_rebuild = self._appearance_changed(new_config)
            need_geometry = self._geometry_changed(new_config)
            self.config_data = new_config

            if need_rebuild:
                # 配色 / 字体 / 字号变了：整个界面重建最干净
                for child in self.root.winfo_children():
                    child.destroy()
                self._build()
                self.apply_geometry()
            elif need_geometry:
                # 只是挪位置 / 改大小 / 调透明度：不用重建，直接改外框
                self.apply_geometry()
        except FloatingApiError as exc:
            # 服务可能在重启，安静地等下一轮就好
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("服务端调用失败（调用方负责提示用户）：%s", exc, exc_info=True)

        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


        if not self._stop:
            self.root.after(int(config.FLOATING_CONFIG_POLL_SECONDS * 1000),
                            self.poll_config)

    def check_reminders(self):
        """
        每 3 秒看一眼："有没有提醒刚弹过？"

        【系统通知已经弹了，为什么窗口里还要标一下？】
            因为它们回答的是不同的问题：
                系统通知 = "你该看一下了"（哪怕你在别的软件里）
                窗口内标红 = "就是这条"（等你回头看窗口时，一眼能找到它）
            两条路都留着，比只留一条更不容易漏掉（这叫冗余提醒，是故意的）。
        """
        if self._stop:
            return
        try:
            payload = self.api.get_data()
            for group in payload.get("groups") or []:
                for task in group["tasks"]:
                    if task.get("remind_label") == "已提醒" and \
                            task["id"] not in self._reminder_ids:
                        self._reminder_ids.add(task["id"])
                        self._show_toast("⏰ %s" % task["title"])
        except FloatingApiError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("服务端调用失败（调用方负责提示用户）：%s", exc, exc_info=True)


        if not self._stop:
            self.root.after(3000, self.check_reminders)

    def _show_toast(self, text: str):
        """
        窗口内右下角浮出一个小提示，4 秒后自动消失。

        【为什么不用 messagebox？】那是一个"模态对话框"，
        它会卡住整个界面等你点确定 —— 提醒应该是"顺便告诉你一声"，
        不该打断你正在做的事。
        """
        pal = self.pal
        if self._toast is not None:
            try:
                self._toast.destroy()
            except tk.TclError as exc:
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        self._toast = tk.Label(self.root, text=text, bg=pal["accent"],
                               fg=contrast_text(pal["accent"]),
                               font=self._font(-1), wraplength=240, padx=6, pady=3)
        self._toast.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-34)
        self.root.after(4000, self._hide_toast)

    def _hide_toast(self):
        try:
            if self._toast is not None:
                self._toast.destroy()
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)

        self._toast = None

    # ------------------------------------------------------------------
    # 停止与主循环
    # ------------------------------------------------------------------
    def _watch_stop_file(self):
        """
        看"停止标志文件"还在不在。

        【为什么不直接让服务端 kill 这个进程？】
            kill 会跳过 tkinter 的清理，刚拖动过的位置还没保存就丢了。
            所以服务端只是"写一个文件"，窗口自己看到后走正常的退出流程：
            保存设置 -> 释放锁 -> 关窗口。
        """
        if self._stop:
            return
        if self._stop_file.exists():
            logger.info("收到停止信号，准备退出")
            # ★ 【v0.4.4 修】这里以前还会先 PATCH 一次 visible=False，有两个坏处：
            #   ① 服务端收到这个 PATCH 会【再调一次 stop_window】等你退出，
            #      而你在等这个 PATCH 的响应 —— 互相等，最后只能 3 秒超时强杀；
            #   ② 于是"重启窗口"之后，设置里 visible 变成 false，
            #      设置页显示"窗口当前是隐藏的"，可窗口明明就在屏幕上。
            #   "要停了"这件事服务端本来就知道（是它让你停的），不需要你回报。
            self.shutdown()
            return
        self.root.after(1500, self._watch_stop_file)

    def shutdown(self):
        """准备退出：保存位置、释放锁、销毁窗口。"""
        if self._stop:
            return
        self._stop = True
        try:
            if self._save_job is not None:
                self.root.after_cancel(self._save_job)
                self._save_job = None
            self._save_position()
        except Exception as exc:                       # noqa: BLE001
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("这里出错不影响主流程，按可忽略处理：%s", exc, exc_info=True)

        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("控件已经销毁或参数不被接受，忽略这次界面操作：%s", exc, exc_info=True)


    def run(self):
        """
        进入界面主循环（会一直阻塞到窗口关闭）。

        【第一次刷新为什么放在 after 里而不是直接调用？】
            mainloop 还没跑起来时，界面还没真正显示，
            这时候去改控件容易出各种平台相关的怪问题。
            用 after(60) 让"第一次刷新"发生在窗口显示之后，稳得多。
            apply_geometry 也放在这里 —— 因为改窗口大小同样要在窗口
            显示出来之后做才可靠。
        """
        self.root.after(30, self.apply_geometry)
        self.root.after(60, lambda: self.refresh(force=True))
        # 记一行"我实际画出来的是什么颜色"。
        # 【为什么要专门记这个？】
        #   界面类问题最难查的地方在于：程序不报错，只是"看起来不对"。
        #   有了这行日志，"窗口显示的配色"和"设置里的配色"就能对上了 ——
        #   排查时一眼就能看出"是没生效"还是"生效了但被别的窗口盖住了"。
        self.root.after(1500, self._log_applied_look)
        self.root.after(400, self.poll_config)
        self.root.after(800, self.check_reminders)
        self.root.after(1000, self._watch_stop_file)
        self.root.after(int(config.FLOATING_DATA_REFRESH_SECONDS * 1000), self._auto_refresh)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:                # 源码运行时按 Ctrl+C
            logger.debug("用户按了 Ctrl+C，正常退出")

        finally:
            self._stop = True

    def _log_applied_look(self):
        """把"窗口实际用的配色和几何"记进日志（排查界面问题时的第一手证据）。"""
        try:
            cfg = self.config_data
            logger.info(
                "界面已应用：bg=%s fg=%s accent=%s alpha=%.2f 字体=%s %s磅 "
                "几何=%dx%d+%d+%d 置顶=%s 分组=%s",
                cfg.get("bg_color"), cfg.get("fg_color"), cfg.get("accent_color"),
                float(cfg.get("opacity", 0.5)),
                cfg.get("font_family"), cfg.get("font_size"),
                self.root.winfo_width(), self.root.winfo_height(),
                self.root.winfo_x(), self.root.winfo_y(),
                bool(cfg.get("always_on_top")),
                cfg.get("group_by"),
            )
        except Exception:                       # noqa: BLE001 - 日志不该影响主流程
            logger.exception("记录界面状态时出错")

    def _auto_refresh(self):
        """每 N 秒自动刷新一次数据。"""
        if self._stop:
            return
        self.refresh()
        if not self._stop:
            self.root.after(int(config.FLOATING_DATA_REFRESH_SECONDS * 1000),
                            self._auto_refresh)


# ===========================================================================
# 四、进程入口
# ===========================================================================


def main(token: str = None) -> int:
    """
    悬浮窗进程的入口。

    【职责顺序很重要，别调换】
        0. 先把日志配置好 —— 这样后面任何一步出问题都留得下证据；
        1. 再抢"座位"（文件锁）—— 抢不到说明已经有一个窗口了，立刻退出；
        2. 等 Web 服务就绪（最多等 30 秒）—— 刚开机时窗口可能比服务起得快；
        3. 画窗口、进主循环；
        4. 退出时写回 pid=0 并释放锁。
    """
    from app.services import floating_service

    # ---- 0) 配置日志（★ 这一步是 BUG-028 复盘加的）----
    #
    # 【为什么窗口进程必须自己配一次日志？】
    #   app/core/logging_setup.py 里的 setup() 是【由谁调用谁才写文件】的。
    #   服务进程在 main.py 里调了，但窗口是另一个进程 ——
    #   它如果不调，那么它内部所有 logger.info/exception 都会**无声无息**：
    #   没有 handler，日志直接被丢掉。
    #
    #   后果（BUG-028 实际发生的）：窗口启动失败退出，
    #   日志文件里只有一行"已有一个悬浮窗在运行"，没有任何报错线索。
    #   所以现在窗口一起来就先配日志，把所有输出写进
    #   data/logs/app.log 和 data/floating/window.log。
    #
    # ⚠️ 顺序：必须在抢锁【之前】。
    #    否则"抢锁失败退出"这件事本身也记不下来（而它恰恰是常被误认成故障的正常行为）。
    try:
        logging_setup.setup()
        logging_setup.install_exception_hooks()
    except Exception as exc:                             # noqa: BLE001 - 日志配不上也要能开窗口
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("这里出错不影响主流程，按可忽略处理：%s", exc, exc_info=True)

    logger.info("悬浮窗进程启动（pid=%s）", os.getpid())

    api = FloatingApi(token=token or os.environ.get("TODO_FLOAT_TOKEN") or None)

    # ---- 1) 抢座位 ----
    lock_handle = floating_service.acquire_window_lock()
    if lock_handle is None:
        logger.info("已经有一个悬浮窗在运行，本次启动取消")
        print("已有一个悬浮窗在运行")
        return 0

    # ---- 2) 等 Wi-Fi / 服务就绪 ----
    # 【为什么要等？】开机自启时，窗口和服务几乎同时起步。
    # 服务要先建表、备份、初始化通知身份，比窗口慢几秒很正常。
    # 不等的话窗口第一屏会是"连不上服务"，用户还以为坏了。
    if not _wait_for_server(api, timeout=30):
        logger.error("等待后端服务超时，悬浮窗退出")
        floating_service.release_window_lock(lock_handle)
        return 1

    # ---- 3) 画窗口 ----
    # 【为什么拉设置要"重试几次"再放弃？】
    #   因为这一步失败会让整个窗口进程退出，而服务端的守护线程
    #   3 秒后又会把它拉起来 —— 于是变成
    #   "启动 -> 失败 -> 退出 -> 再启动"的无限循环。
    #   所以：能重试就重试（服务可能正在重启），确实拿不到再干净地退出。
    window = None
    try:
        cfg = _load_config_with_retry(api)
        window = FloatingWindow(api, cfg)

        # 写下"我在这儿"（服务端靠它显示"运行中（进程号 xxx）"）
        _write_seat()

        logger.info("悬浮窗已启动（pid=%s）", os.getpid())
        window.run()
    except Exception:                          # noqa: BLE001
        # 【为什么不直接让异常冒出去？】
        #   这个进程是 noconsole 启动的，异常堆栈没人看得见。
        #   但我们把它重定向到了 data/floating/window.log，
        #   所以这里记录完整堆栈 = 用户/开发者能查到原因。
        logger.exception("悬浮窗运行出错")
        return 1
    finally:
        _clear_seat()
        floating_service.release_window_lock(lock_handle)
        logger.info("悬浮窗已退出")

    return 0


def _load_config_with_retry(api: FloatingApi, attempts: int = 5, delay: float = 1.0) -> dict:
    """
    取窗口设置，失败时重试几次。

    【最后把异常抛出去是有意的】
        如果试了 5 次还拿不到，说明要么服务挂了、要么令牌不对 ——
        这两种情况都不是"多等一会儿就好"。
        抛出去让 main() 记录完整堆栈到 window.log，
        用户（和我们）才有线索，而不是一个永远转圈的窗口。
    """
    last_error = None
    for _index in range(attempts):
        try:
            return api.get_config().get("config") or {}
        except FloatingApiError as exc:
            last_error = exc
            # 403 是"令牌不对"，重试没有意义（它不是暂时性故障），立刻放弃并说明原因
            if "403" in str(exc):
                raise FloatingApiError(
                    "服务端拒绝了悬浮窗的访问令牌（403）。\n"
                    "这通常发生在程序升级或数据库被替换之后。\n"
                    "请到设置页点一次「重启窗口」，让它带着新令牌重新启动。\n"
                    "原始错误：%s" % exc)
            time.sleep(delay)
    raise last_error


def _wait_for_server(api: FloatingApi, timeout: int = 30) -> bool:
    """等服务端能响应健康检查（每 0.5 秒试一次）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api.health()
            return True
        except FloatingApiError:
            time.sleep(0.5)
    return False


def _write_seat():
    """
    写下"我在这儿"：pid + 启动时刻，服务端靠它显示运行状态。

    写的是一个 JSON 文件，放在项目的数据目录里
    （遵守"项目自包含"原则：不往系统目录里写任何东西）。
    """
    seat = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "started_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        config.FLOAT_DIR.mkdir(parents=True, exist_ok=True)
        config.FLOATING_PID_FILE.write_text(
            json.dumps(seat, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("写入 pid 文件失败：%s", exc)


def _clear_seat():
    """退出时把"我在这儿"擦掉（下次启动才是干净的）。"""
    try:
        config.FLOATING_PID_FILE.unlink()
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)

