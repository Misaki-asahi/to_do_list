# -*- coding: utf-8 -*-
"""
floating_service.py -- 桌面悬浮窗的业务规则层（v0.3.0 新增）。

【这一层负责什么】

    1. 把"设置"和"任务列表"组装成悬浮窗需要的样子（分块、排序、打标记）；
    2. 判断"现在该不该有一个窗口在运行"（总开关 + 是否显示）；
    3. 负责窗口进程的【开、关、以及"不要开出两个"】。

【这一层不负责什么】
    不画界面（那是 app/gui/ 的事），不直接碰数据库（那是 repositories 的事）。

【整体数据流（看懂这张图就懂了这个功能）】

    ┌──────────────┐   HTTP(127.0.0.1)   ┌────────────────────┐
    │ 设置页（网页）│ ──────────────────► │  FastAPI 服务       │
    │ 改透明度/字号 │                     │  routers/floating.py│
    └──────────────┘                     └─────────┬──────────┘
                                                   │ 读写设置表
                                                   ▼
    ┌──────────────┐   每 3 秒检查一次   ┌────────────────────┐
    │ 悬浮窗进程    │ ◄────────────────── │ 守护线程 supervisor │
    │ （tkinter）   │   该开就开、该关就关 │ （在服务进程里）     │
    └──────┬───────┘                     └────────────────────┘
           │ 每 10 秒拉一次任务数据 / 每 3 秒问一次设置
           └──────────────► 还是这套 HTTP 接口

【为什么窗口不直接读数据库，而是走 HTTP？】

    因为项目的分层约定是"repositories 是唯一碰数据库的地方"，
    而窗口跑在【另一个进程】里。如果让它直连 SQLite，会出现两个问题：
        ① 两个进程同时写库，可能撞上 "database is locked"；
        ② 窗口就得把 models / repositories / services 全套代码搬过去，
           以后改一处数据逻辑，要保证两个进程都升级 —— 很容易漏。
    走本机 HTTP 就没有这两个问题，代价只是一次本地网络调用（不到 1 毫秒）。
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from app import config
from app.core import logging_setup, timeutil
from app.repositories import floating_repo, setting_repo, task_repo
from app.services import task_service

logger = logging_setup.get_logger("floating")

# 本进程的启动时刻。说它是"唯一指纹"是因为：
# [进程号, 启动时刻] 这个组合在 Windows 上是独一无二的 ——
# 进程号会被系统回收复用，而"披着同一个进程号的另一个进程"
# 不可能有着同样的启动时刻。判断"windows 里那个窗口还是不是我"就靠它。
config.FLOATING_PROCESS_STARTED_AT = time.monotonic()

# 防止"同时点两次开启"开出两个窗口。
# 【为什么服务端还要一把线程锁？】
#   守护线程每 3 秒检查一次，设置页点按钮也会立刻触发一次 ——
#   两者可能【同时】发现"没有窗口"然后各拉一个起来。
#   前面进程那一层已经用文件锁挡了一道，这里再挡一道（纵深防御），
#   因为"冒出两个一模一样的窗口"对用户来说是非常困惑的一件事。
_SPAWN_LOCK = threading.Lock()

# 窗口进程信息里由它自己写在第一行的"握手前缀"
READY_PREFIX = "FLOATING_READY"

# 数据缓存：{任务列表, 时间戳}。窗口每 10 秒拉一次，而分组结果在
# 同一秒内完全一样，缓存 1 秒能挡掉"设置页刷新 + 窗口刷新"这种瞬时重复计算。
_CACHE_TTL_SECONDS = 1.0
_data_cache = {"at": 0.0, "payload": None}


# ===========================================================================
# 一、设置：默认值 / 校验 / 分块方式
# ===========================================================================


def defaults(public: bool = True) -> dict:
    """返回全部默认值。

    为什么要有它？
        ① 新用户第一次打开设置页，界面上要有个合理的初值；
        ② 用户可以把设置"恢复默认"（把 set_value 全部删掉即可）。
    """
    values = {name: default for name, (_key, _kind, default) in floating_repo.FIELDS.items()}
    if not public:
        values.update({name: default
                       for name, (_key, _kind, default) in floating_repo.INTERNAL_FIELDS.items()})
    return values


def is_supported() -> bool:
    """
    悬浮窗靠 tkinter 画界面 —— 它只随 Windows 版 Python 一起装。

    【为什么标准库也会缺？】
        Linux 上很多发行版把 tkinter 拆成了单独的包（python3-tk），
        不装就没有。所以这里不能假设"标准库一定在"，
        而是真的去 import 一次看看（结果缓存起来，别每次都 import）。
    """
    global _tk_available
    if _tk_available is None:
        try:
            import tkinter            # noqa: F401
            _tk_available = True
        except Exception:             # noqa: BLE001 - 缺库的原因五花八门
            _tk_available = False
    return _tk_available


_tk_available = None


def get_config() -> dict:
    """读设置（给接口用）。"""
    return floating_repo.get_config()


def update_config(changes: dict) -> dict:
    """
    改设置，并在改动影响"窗口该不该开"时立刻联动。

    【为什么"联动"要写在这里，而不是等守护线程 3 秒后自己发现？】
        用户体验的差别：点一下"开启悬浮窗"，窗口应当【马上】出现。
        如果只依赖守护线程轮询，最坏情况下要等 3 秒，
        用户会以为"没生效"，然后再点一下 —— 然后开出两个窗口。
    """
    # ---- 规则 1：不能凭空往设置表里写未知字段 ----
    unknown = [k for k in changes if k not in floating_repo.FIELDS]
    if unknown:
        from app.core import errors as app_errors
        raise app_errors.BusinessError(
            "不支持的悬浮窗设置项：%s（可选：%s）" % (
                ", ".join(sorted(unknown)), ", ".join(sorted(floating_repo.FIELDS))))

    # ---- 规则 2：范围和类型再兜一层 ----
    # schemas 已经用 Field(ge=..., le=...) 挡过一次，但 services 层是
    # "业务规则的最后一道门"：有人绕过 HTTP 直接调这个函数时也得管用。
    cleaned = _validate(changes)

    # ---- 规则 3：开启时顺手把"显示"也打开 ----
    # 用户点"开启悬浮窗"的本意一定是"我要看到它"。
    # 让他再单独去点一次"显示"是多余的心智负担（和"填了提醒时间就自动开提醒"同理）。
    if cleaned.get("enabled") is True and "visible" not in cleaned:
        cleaned["visible"] = True

    # ---- 规则 4：关闭时把显示状态一起收掉，并停掉正在跑的窗口 ----
    if cleaned.get("enabled") is False:
        cleaned["visible"] = False

    result = floating_repo.set_config(**cleaned)

    # 数据版本 +1：让所有正在运行的窗口知道"设置变了，该重画了"
    floating_repo.bump_data_version()

    if result.get("enabled") and result.get("visible"):
        # 用户改了与悬浮窗有关的设置 = "情况变了，再试一次"
        # （比如他刚把别的程序关掉、或者刚改完令牌）
        reset_supervisor_backoff()
        ensure_running(force=True)
    else:
        # ★ 【v0.4.4 修】不能在请求线程里"同步等窗口退出"。
        #   窗口自己也会调这个接口（点 ✕ 时它先把 visible 写成 false），
        #   如果这里同步等它，就变成"服务端等窗口退出、窗口在等服务端回响应"——
        #   实测每次关窗/重启都要卡 3.35 秒，最后靠超时强杀收场，
        #   "优雅退出"（保存窗口位置）的设计目的完全落空。
        #   放到后台线程里做，接口立刻返回，窗口也能正常走完自己的退出流程。
        threading.Thread(target=stop_window, kwargs={"reason": "设置已关闭"},
                         daemon=True).start()

    # ------------------------------------------------------------------
    # 【返回值为什么是"完整状态"而不是"刚改的那几个字段"？】
    #
    #   因为改设置会产生"副作用"：开启 -> 窗口被拉起来了；关闭 -> 窗口被停掉了。
    #   如果只回传"你刚改的字段"，前端就不知道窗口现在到底跑没跑，
    #   设置页上的"窗口进程：运行中 / 未运行"就会显示成上一次的旧状态 ——
    #   用户点一下开关，看到的说明还是老样子，会以为没生效。
    #
    #   回传 status（里面同时含 config 和 window）一次说清楚，
    #   前端就不用再补一次请求。这是"接口设计要包含副作用的后果"的一个例子。
    # ------------------------------------------------------------------
    return {"ok": True, "message": "已保存", "status": status()}


def _validate(changes: dict) -> dict:
    """把越界的值挡下来（换成 BusinessError，前端会显示成 400 + 中文原因）。"""
    from app.core import errors as app_errors

    ranges = {
        "width": (config.FLOATING_MIN_WIDTH, config.FLOATING_MAX_WIDTH, "窗口宽度"),
        "height": (config.FLOATING_MIN_HEIGHT, config.FLOATING_MAX_HEIGHT, "窗口高度"),
        "font_size": (config.FLOATING_MIN_FONT_SIZE, config.FLOATING_MAX_FONT_SIZE, "字号"),
    }
    for field, (low, high, label) in ranges.items():
        if field in changes and changes[field] is not None:
            try:
                value = int(changes[field])
            except (TypeError, ValueError):
                raise app_errors.BusinessError("%s必须是整数" % label)
            if not (low <= value <= high):
                raise app_errors.BusinessError(
                    "%s必须在 %d ~ %d 之间（收到 %s）" % (label, low, high, value))
            changes[field] = value

    if changes.get("opacity") is not None:
        try:
            opacity = float(changes["opacity"])
        except (TypeError, ValueError):
            raise app_errors.BusinessError("透明度必须是数字")
        if not (config.FLOATING_MIN_OPACITY <= opacity <= config.FLOATING_MAX_OPACITY):
            raise app_errors.BusinessError(
                "透明度必须在 %.0f%% ~ %.0f%% 之间（收到 %s）" % (
                    config.FLOATING_MIN_OPACITY * 100,
                    config.FLOATING_MAX_OPACITY * 100, opacity))
        changes["opacity"] = round(opacity, 2)

    if changes.get("group_by") is not None and \
            changes["group_by"] not in config.FLOATING_GROUP_BY_CHOICES:
        raise app_errors.BusinessError("分块方式只能是：%s" % " / ".join(
            config.FLOATING_GROUP_BY_CHOICES))

    if changes.get("scope") is not None and \
            changes["scope"] not in config.FLOATING_SCOPE_CHOICES:
        raise app_errors.BusinessError("显示范围只能是：%s" % " / ".join(
            config.FLOATING_SCOPE_CHOICES))

    for field in ("x", "y"):
        if changes.get(field) is not None:
            try:
                changes[field] = int(changes[field])
            except (TypeError, ValueError):
                raise app_errors.BusinessError("窗口位置必须是整数")

    return changes


def apply_theme(name: str) -> dict:
    """
    一键套用配色主题（设置页上那几个色块）。

    "主题"只是把三个颜色 + 透明度的组合存下来，没有别的魔法 ——
    但它在界面上的价值很高：用户不用自己调四个滑块就能变好看。

    返回值说明：和 update_config 一样，回传的是【改动之后的最新设置】
    （前端拿到就能直接重画界面，不用再多发一次请求）。
    """
    from app.core import errors as app_errors

    theme = config.FLOATING_THEMES.get(name)
    if theme is None:
        raise app_errors.BusinessError(
            "没有这个配色主题：%s（可选：%s）" % (name, ", ".join(config.FLOATING_THEMES)))

    # ★【只改颜色，不动透明度】（v0.3.4 改，见 BUG-042）
    #   用户调好的不透明度是他自己的选择，不该因为"换了个配色"被重置 ——
    #   尤其默认值是"透明度 30%"，任何一个主题顺手改成 0.92 都是在跟他对着干。
    update_config({
        "fg_color": theme["fg"],
        "bg_color": theme["bg"],
        "accent_color": theme["accent"],
    })
    return get_config()


def reset_config() -> dict:
    """
    恢复出厂设置（位置、大小、外观、行为全部回到默认）。

    【为什么"位置"也要恢复？】
        有一种真实的困境：用户把窗口拖到了副屏，后来副屏拔了 ——
        窗口就出现在屏幕外，怎么都点不到。恢复默认是唯一的自救办法。
    """
    for name, (key, _kind, _default) in floating_repo.FIELDS.items():
        if name == "data_version":
            continue
        setting_repo.delete_key(key)

    stopped = stop_window(reason="恢复默认设置")
    floating_repo.bump_data_version()

    result = get_config()
    result["status"] = status()
    result["message"] = "已恢复默认设置" + ("，窗口已关闭，重新开启即可出现在左上角" if stopped else "")
    return result


# ===========================================================================
# 二、分块与排序（这是"按日期 / 优先级 / 分类分块显示"的实现）
# ===========================================================================


def group_key(task, group_by: str) -> tuple:
    """
    算出某条任务该归到哪一块，返回 (排序号, 块标题, 块提示)。

    【三位分别是什么】
        排序号 —— 让块按"我指定的顺序"排列（数字小的在前）
        块标题 —— 显示在分组条上的文字，例如「今天 9-14」
        块提示 —— 鼠标停在分组条上时的说明，例如「最近一周内到期」

    【为什么第一位是"排序号"而不是块标题本身？】
        Python 排序时先比第一个元素，相同才比第二个。
        把"我希望这块排第几"放在第一位，结果就是
        "块按我指定的顺序排列"，而不用给每种分块方式写一遍排序代码。
    """
    if group_by == "priority":
        priority = task.priority
        # 排序号取负数：这样"高优先级"排在最前面（数字越小越靠前）
        return (-priority, config.PRIORITY_NAMES.get(priority, "中") + "优先级", "按重要程度")

    if group_by == "category":
        name = (task.category or "").strip()
        # 没填分类的永远排最后：用一个"比任何中文都大"的占位符排在后面
        if not name:
            return (1, "未分类", "")
        return (0, name, "")

    # ---- 默认：按日期分块 ----
    #
    # 档位（用户指定的顺序）：
    #     已过期 → 今天 → 明天 → 七天内 → 之后 → 未安排日期
    #
    # 【块标题里为什么带日期？】—— 用户明确要求：
    #   「分类默认按日期分，今天 明天 七天内 之后（要附带具体日期）」
    #   所以标题长这样：「今天 9-14」「明天 9-15」「七天内 9-16 ~ 9-20」。
    #   只写"今天"的话，用户没法一眼确认它指的是哪一天 ——
    #   尤其是窗口挂了好几天没关的时候（那种情况很常见）。
    #
    # 【为什么要单独给"七天内"一档？】
    #   只有"今天/明天/之后"三档的话，"后天要交的"和"下个月要做的"
    #   会被混在同一块里，而这两件事的紧迫程度差得远。
    #   把最近一周单独拎出来，用户扫一眼就知道"这一周要忙什么"。
    due = task.due_at
    if not due:
        return (5, "未安排日期", "没有截止时间")

    today = timeutil.today_prefix()
    day = due[:10]
    days = _days_until(day, today)

    if days is None:
        return (5, due[5:], "无法解析日期")
    if days < 0:
        return (0, "已过期 " + _mmdd(day), "本该在 %s 之前做完" % due)
    if days == 0:
        return (1, "今天 " + _mmdd(day), "就是今天")
    if days == 1:
        return (2, "明天 " + _mmdd(day), "明天到期")
    if days <= 7:
        # 【为什么这档显示的是"范围"而不是单个日期？】
        #   因为这一档里可能装着 6 个不同的日期。标题写成"7 天后"会误导
        #   （那块里不只有 7 天后那一天）；写成 "9-16 ~ 9-20" 才准确 ——
        #   用户一眼就知道这一块的时间跨度。
        return (3, "七天内 " + _mmdd(_shift_days_str(2)) + " ~ " + _mmdd(_shift_days_str(7)),
                "最近一周内到期")

    # ---- 之后：★ 一整档，不再按日期拆开（v0.3.4 改，见 BUG-038）----
    #
    # 【为什么改成"一档"？】
    #   原来的写法是每个远期日期各成一块（「之后 9-22」「之后 10-24」…），
    #   于是：① 远期日期一多，侧边会刷出一长串「之后 x-x」；
    #        ② 更糟的是**顺序会乱**——同一档的排序号都是 4，
    #           排序只好退化成"比标题字符串"，"10-24" < "9-22"，
    #           结果 10 月排在了 9 月前面（BUG-038）。
    #
    #   用户 2026-09-14 确认：「之后：一档就可以，但每一项后面表明具体日期」。
    #   所以这里合成一档，具体日期交给每条任务自己显示 ——
    #   任务行上的日期来自 _due_label()，远期日期会显示成 "2026-10-24 09:00"，
    #   "哪一天要交"这条信息一点没丢，只是从"块标题"挪到了"条目上"。
    return (4, "之后", "还要等一段时间")


def _mmdd(day: str) -> str:
    """
    把 "2026-09-14" 变成 "9-14"。

    【为什么不显示年份？】
        待办清单的视野通常只有"最近几个月"。
        每个分组条都塞上 "2026-" 会让标题变长，而多出来的信息
        对"我现在该做什么"毫无帮助。
        （跨年的日期会落在"之后"那一档，那种情况月份本身就够区分了。）
    """
    if not day or len(day) < 10:
        return day or ""
    return "%d-%d" % (int(day[5:7]), int(day[8:10]))


def _days_until(day: str, today: str):
    """
    两个日期字符串相差几天（算出负数表示已经过去）。

    这里用 datetime 直接算，而不是自己拼字符串 ——
    虽然只差几天，但"2 月 28 日到 3 月 1 日差几天"这种问题
    交给标准库永远比手写可靠（闰年是经典陷阱）。
    """
    try:
        from datetime import date
        a = date.fromisoformat(day)
        b = date.fromisoformat(today)
        return (a - b).days
    except (ValueError, TypeError):
        return None


def build_groups(tasks: list, group_by: str) -> list:
    """
    把任务列表切成若干块，返回
        [{"key": ..., "title": "今天 9-14", "hint": "就是今天",
          "count": 2, "tasks": [...]}, ...]

    【块内怎么排序？】
        保持 repository 给出的顺序（未完成在前 -> 截止时间早的在前 -> 新的在前），
        也就是"用户在主列表页看到的那一套"，两个界面看起来一致，不用重新学。

    【块的顺序怎么定？】
        不用给每种分块方式各写一遍排序代码，而是让分块函数返回一个"排序号"：
            date     -> 0 已过期 / 1 今天 / 2 明天 / 3 七天内 / 4 之后（一整档）
                        / 5 未安排日期
            priority -> 高优先级的排序号是负数，于是自然排最前
            category -> 按名字排，"未分类"永远最后
        这样"加一种新的分块方式"只需要让 group_key 返回合适的排序号。
    """
    buckets = {}       # 标题 -> 任务列表
    orders = {}        # 标题 -> 排序号（同一个块里所有任务的排序号必然相同）
    hints = {}         # 标题 -> 提示文字

    for task in tasks:
        order, title, hint = group_key(task, group_by)
        buckets.setdefault(title, []).append(task)
        orders.setdefault(title, order)
        hints.setdefault(title, hint)

    if group_by == "category":
        # 分类是"名字"，排序号没有意义（所有分类的排序号都是 0），
        # 所以这种情况单独按名字排 —— 而且"未分类"要永远垫底。
        titles = sorted(buckets, key=lambda t: (t == "未分类", t))
    else:
        # 默认排序是"小的在前"，而 group_key 已经把"该排第几"编码进排序号了。
        # 用 (排序号, 标题) 做键，保证同一排序号下的顺序也是稳定的
        # （不加标题的话，同号的两个块顺序不确定，界面会"每次刷新都换位置"）。
        titles = sorted(buckets, key=lambda t: (orders[t], t))

    return [{"key": t, "title": t, "hint": hints.get(t, ""),
             "count": len(buckets[t]), "tasks": buckets[t]}
            for t in titles]


# ===========================================================================
# 三、组装悬浮窗要显示的数据
# ===========================================================================


def _scope_filter(tasks: list, scope: str) -> list:
    """
    按"看哪一段"过滤。

    【和分块的关系】
        "范围"决定哪些任务进入视野，"分块"决定它们怎么被组织。
        两者可以自由组合：比如"今天 + 按分类分块"= 今天要做的事按类别摆开。
    """
    if scope == "all":
        return tasks

    today = timeutil.today_prefix()
    week_end = _shift_days_str(7)

    if scope == "today":
        # "今天"包含两类：截止时间在今天的，以及【已过期还没做】的。
        # 为什么不把过期的藏起来？—— 待办清单的价值恰恰在于"提醒你漏了什么"。
        return [t for t in tasks if t.due_at and t.due_at[:10] <= today]

    if scope == "week":
        return [t for t in tasks if not t.due_at or t.due_at[:10] <= week_end]

    if scope == "active":
        # 默认视图：没完成的 + 今天到期之前完成的（做了的当天还能看见，有成就感）
        return [t for t in tasks if t.status != config.STATUS_DONE
                or (t.completed_at and t.completed_at[:10] >= today)]

    return tasks


def _decorate(task, now_text: str) -> dict:
    """
    给一条任务加上"界面上要用、但数据库里没有"的字段。

    【为什么要在这里算，而不是让窗口自己算？】
        因为时间比较的规则属于业务逻辑（"什么时候算今天"、"过期怎么算"），
        而这种规则只应该有一份实现（在 timeutil 和这里）。
        窗口只负责把字符串画出来 —— 它是"笨"的，越笨越不容易出错。
    """
    item = task.to_dict()
    item["is_done"] = task.status == config.STATUS_DONE
    item["priority_name"] = config.PRIORITY_NAMES.get(task.priority, "中")

    due = task.due_at or ""
    item["due_label"] = _due_label(due, now_text)
    item["overdue"] = bool(due and not item["is_done"] and due < now_text)

    # ★ 把"原来存的字符串"也一并回传，供编辑弹窗预填。
    #
    # 【为什么要专门说明这一句？】
    #   编辑弹窗里的输入框必须是"2026-09-14 18:00"这种【规范格式】，
    #   不能填"今天 18:00"这个人话标签（否则用户一保存就被接口拒绝）。
    #   所以 due_label（给人看）和 due_raw（给输入框用）是两个字段，
    #   混用会导致"打开编辑框直接点保存就报格式错误"这种很恼人的 Bug。
    item["due_raw"] = task.due_at or ""
    item["remind_raw"] = task.remind_at or ""
    item["remind_enabled_raw"] = bool(task.remind_enabled)
    item["description_raw"] = task.description or ""

    # 提醒状态：即将提醒 / 已提醒过 / 没设提醒
    item["remind_label"] = ""
    item["remind_soon"] = False
    if task.remind_enabled and task.remind_at and not item["is_done"]:
        if task.reminded_at:
            item["remind_label"] = "已提醒"
        else:
            minutes = _minutes_between(now_text, task.remind_at)
            if minutes is not None and 0 <= minutes <= 60:
                item["remind_soon"] = True
                item["remind_label"] = ("%d 分钟后提醒" % minutes) if minutes > 0 else "马上提醒"
            else:
                item["remind_label"] = "提醒 " + task.remind_at[11:]

    item["dialog_label"] = _dialog_label(item, task)
    return item


def _dialog_label(item: dict, task) -> str:
    """
    给窗口上的"快速编辑对话框"用的英文/无译文字段。

    【为什么界面文字要放在服务端？】
        因为窗口进程读的是【从接口拿到的数据】，
        而这些短语（"编辑任务""保存""取消"）如果写在窗口代码里，
        以后想改成"改一改"就得重新打包一个 exe。
        放在服务端，改一次全员生效 —— 而且测试也容易写。
    """
    return "编辑：%s" % (task.title or "")


def _due_label(due: str, now_text: str) -> str:
    """把 "2026-09-20 08:30" 变成"今天 08:30"这种人话。"""
    if not due:
        return ""
    today = timeutil.today_prefix()
    if due[:10] == today:
        return "今天 " + due[11:]
    if due[:10] < today:
        # 过期的只显示"月-日 时:分"：年份对"我漏了什么"这件事没有帮助
        return "已过期 " + due[5:]
    # 其余交给 timeutil.humanize（"明天 09:00" / "3 天后 10:00" / 完整日期）
    return timeutil.humanize(due) or due


def _shift_days_str(days: int) -> str:
    """
    "今天往后 N 天"的日期字符串。

    【为什么不写在 timeutil.py 里？】
        因为这个换算只有悬浮窗的"未来七天"这个筛选用得到，
        它不是一个"全局时间规则"，而是一个界面选项的实现细节。
        按需放在这里，timeutil 保持"只放通用规则"的干净定位。
    """
    from datetime import timedelta
    return (timeutil.now() + timedelta(days=days)).strftime(config.DATE_FORMAT)


def _minutes_between(start_text: str, end_text: str):
    """两个时间字符串相差几分钟（end 在前则返回负数）。"""
    try:
        delta = timeutil.parse(end_text) - timeutil.parse(start_text)
        return int(delta.total_seconds() // 60)
    except Exception:                      # noqa: BLE001 - 解析失败就不显示，不影响列表
        return None


def _signature(tasks: list) -> str:
    """
    给一批任务算一个"指纹"字符串。

    【为什么要算指纹而不是比版本号？】
        版本号是"有人动过数据"的粗粒度信号：用户在网页里改了标题，
        版本号 +1，窗口就得重建整个列表 —— 其实只是改了一个字。
        指纹是"内容到底一不一样"的精确判断：把关键字段拼起来算个哈希，
        一样就真的什么都不用做。两者配合：版本号负责"要不要去问"，
        指纹负责"问了之后要不要重画"。
    """
    import hashlib
    parts = []
    for t in tasks:
        parts.append("%s|%s|%s|%s|%s|%s|%s|%s" % (
            t.id, t.title, t.status, t.priority, t.due_at or "",
            t.remind_at or "", bool(t.remind_enabled), t.category or ""))
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def window_payload(force: bool = False) -> dict:
    """
    悬浮窗一次刷新需要的全部数据。

    返回：
        {
          "config":   窗口外观与行为设置,
          "groups":   分好块的任务（已经带上 due_label / overdue / remind_label）,
          "stats":    总览数字（未完成 / 今天 / 已过期 / 已完成）,
          "next":     最近一条要提醒的任务（窗口顶部显示）,
          "version":  数据版本号,
          "signature":内容指纹,
          "now":      服务端当前时间（窗口用它校准，避免两边时钟不一致）,
        }
    """
    now = time.time()
    if not force and _data_cache["payload"] is not None and \
            (now - _data_cache["at"]) < _CACHE_TTL_SECONDS:
        return _data_cache["payload"]

    cfg = get_config()

    # 一次把"全部未删除的任务"取出来，然后在内存里筛选 / 分组。
    # 【为什么不在 SQL 里分？】因为窗口还要显示几个统计数字，
    # 而且待办的量级是几十到几千条，内存里处理既简单又快（本地 SQLite 也一样快）。
    tasks = task_repo.list_tasks()
    if not cfg["show_completed"]:
        tasks = [t for t in tasks if t.status != config.STATUS_DONE]

    scoped = _scope_filter(tasks, cfg["scope"])
    groups = build_groups(scoped, cfg["group_by"])

    # 加上界面字段，同时把 ORM 对象换成普通字典（窗口那边不需要也不该看到 ORM）
    decorated_groups = []
    for group in groups:
        decorated_groups.append({
            "key": group["key"],
            "title": group["title"],
            "hint": group.get("hint", ""),
            "count": group["count"],
            "tasks": [_decorate(t, timeutil.now_str()) for t in group["tasks"]],
        })

    stats = _stats(tasks, scoped)

    payload = {
        "config": cfg,
        "groups": decorated_groups,
        "stats": stats,
        "next": _next_reminder(tasks),
        "categories": _all_categories(tasks),
        "version": floating_repo.get_data_version(),
        "signature": _signature(scoped),
        "now": timeutil.now_str(),
        "server_version": config.APP_VERSION,
    }

    _data_cache["at"] = now
    _data_cache["payload"] = payload
    return payload


def _all_categories(tasks: list) -> list:
    """
    收集所有用过的分类名（去重、排序），供编辑弹窗的"分类"下拉框用。

    【为什么要有它？】—— 用户反馈里点出的问题：
        「编辑…功能过于简单」
        原来的编辑框只有一个自由输入的"分类"文本框，
        用户每次都得凭记忆手打分类名，打错一个字就会多出一个新分类
        （"工作"和"工作了"就变成两类了）。
        把已有分类列出来让他选，是这个问题最直接的解法。

    【为什么从 tasks 里收集，而不是单独查一次数据库？】
        因为调用这个函数的那一刻，所有任务已经在内存里了（见 window_payload）。
        再查一次数据库纯属浪费，而且两个查询之间还可能不一致
        （比如刚好有人加了一条带新分类的任务）。
        ——"用手里已经有的数据"是列表类功能最省事的原则。
    """
    names = {(t.category or "").strip() for t in tasks}
    names.discard("")
    # 排序用中文友好的方式：直接按字符串排就够了（Python 的字符串比较是按码位）
    return sorted(names)


def _stats(all_tasks: list, scoped: list) -> dict:
    """顶部要显示的四个数字。"""
    today = timeutil.today_prefix()
    now_text = timeutil.now_str()
    todo = [t for t in all_tasks if t.status != config.STATUS_DONE]
    overdue = [t for t in todo if t.due_at and t.due_at < now_text]
    due_today = [t for t in todo if t.due_at and t.due_at[:10] == today]
    return {
        "total": len(all_tasks),
        "todo": len(todo),
        "done": len(all_tasks) - len(todo),
        "overdue": len(overdue),
        "today": len(due_today),
        "shown": len(scoped),
    }


def _next_reminder(tasks: list):
    """在"还没提醒过、且时间未到"的任务里，找最早的那一条。"""
    now_text = timeutil.now_str()
    pending = [t for t in tasks
               if t.remind_enabled and t.remind_at and not t.reminded_at
               and t.status != config.STATUS_DONE and t.remind_at > now_text]
    if not pending:
        return None
    task = min(pending, key=lambda t: t.remind_at)
    return {
        "id": task.id,
        "title": task.title,
        "remind_at": task.remind_at,
        "in_minutes": _minutes_between(now_text, task.remind_at),
    }


def quick_add(payload) -> dict:
    """
    在悬浮窗里直接新建一条待办。

    校验规则完全复用 task_service.create_task ——
    【为什么不在窗口里单独写一套？】
        那样"提醒时间不能早于当前"这类规则就有两份实现，
        改了一处忘了另一处，两个入口的行为就会不一致。
    """
    task = task_service.create_task(payload)
    floating_repo.bump_data_version()
    logger.info("悬浮窗新建待办：%s", task.title)
    return {"ok": True, "task": task.to_dict(), "message": "已添加：%s" % task.title}


def toggle(task_id: int) -> dict:
    """在悬浮窗里勾选 / 取消勾选一条待办。"""
    task = task_service.toggle_status(task_id)
    floating_repo.bump_data_version()
    return {"ok": True, "task": task.to_dict(),
            "message": "已完成" if task.status == config.STATUS_DONE else "已取消完成"}


def update_task(task_id: int, changes: dict) -> dict:
    """在悬浮窗里编辑一条待办（标题 / 优先级 / 分类 / 时间 / 提醒）。"""
    task = task_service.update_task(task_id, changes)
    floating_repo.bump_data_version()
    logger.info("悬浮窗修改待办 #%s：%s", task_id, ", ".join(changes))
    return {"ok": True, "task": task.to_dict(), "message": "已保存"}


def get_task(task_id: int) -> dict:
    """
    取一条待办的完整信息（编辑弹窗要预填用）。

    【为什么不让窗口直接用列表里的数据预填？】
        因为列表里的数据是**最多 10 秒前**的快照（刷新间隔）。
        如果这期间用户在网页里改过，或者提醒已经弹过了，
        编辑弹窗就会拿到过期的值 —— 用户一保存，就把过期的值写回去了
        （这叫"丢失更新"，是编辑类功能最典型的坑）。
        打开弹窗时重新拉一次，虽然多一次请求，但能保证"看到的就是最新的"。
    """
    return {"ok": True, "task": task_service.get_task(task_id).to_dict()}


def snooze(task_id: int, minutes: int = None) -> dict:
    """
    把提醒往后推 N 分钟（悬浮窗上的"稍后提醒"）。

    【为什么写在这里而不是复用 reminders 路由？】
        复用是更好的选择 —— 所以这里直接调用 reminder_service，
        保持"提醒的规则只有一处实现"。
    """
    from app.services import reminder_service

    minutes = minutes or config.REMINDER_SNOOZE_MINUTES
    result = reminder_service.snooze(task_id, minutes)
    floating_repo.bump_data_version()
    return {"ok": True, "result": result, "message": "%d 分钟后再提醒" % minutes}


def compute_remind_at(choice: str) -> str:
    """
    把快捷选项翻译成具体的提醒时间字符串。

    选项：
        "10" / "30" / "60"  -> N 分钟之后
        "today"             -> 今天 18:00（可配置）
        "tomorrow"          -> 明天 09:00（可配置）

    【为什么不把这些换算放在窗口里？】
        因为窗口只会把结果原样提交给接口，而接口要求
        "提醒时间不能早于当前时间"。如果时间的换算和校验分别在两个地方，
        只要窗口和服务器的时钟差一点点，就会出现"我选了 10 分钟后，
        却被告知时间早于当前"这种莫名其妙的错误。
        放在服务端算，用的就是服务端的钟，不可能自相矛盾。
    """
    now = timeutil.now()

    if choice in ("today", "tomorrow"):
        base = now if choice == "today" else now + timedelta(days=1)
        clock = (config.FLOATING_REMIND_TODAY_AT if choice == "today"
                 else config.FLOATING_REMIND_TOMORROW_AT)
        hour, minute = (int(x) for x in clock.split(":"))
        target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # 【边界情况】已经过了今天 18:00 还想"今天 18:00 提醒"？
        # 那就顺延到明天同一时刻 —— 否则接口会以"时间早于当前"拒绝，
        # 用户反复点却怎么也加不上，会以为是 Bug。
        if target <= now:
            target += timedelta(days=1)
        return target.strftime(config.DATETIME_FORMAT)

    try:
        minutes = max(1, int(choice))
    except (TypeError, ValueError):
        from app.core import errors as app_errors
        raise app_errors.BusinessError(
            "提醒快捷选项只能是分钟数，或 today / tomorrow（收到 %r）" % (choice,))

    return (now + timedelta(minutes=minutes)).strftime(config.DATETIME_FORMAT)


# ===========================================================================
# 四、窗口进程的"座位"管理：开、关、以及不要开出两个
#
# 【为什么这一段要这么小心？】
#     有两个真实的坑，都会让用户以为"这软件坏了"：
#       ① 重复启动 —— 点两次"开启"就开出两个窗口，叠在一起、关掉一个还剩一个；
#       ② 状态漂移 —— 程序崩过一次，pid 文件留着，之后再也开不出窗口（以为还活着）。
#     所以下面用了"双保险"：
#         · Windows 文件锁（floating.lock）：进程被杀时由【操作系统】自动释放，
#           这是最可靠的"我还在不在"的证据；
#         · pid + 启动时刻：锁文件判断不了时（比如换台电脑、文件被删）的兜底。
# ===========================================================================


def _process_alive(pid: int) -> bool:
    """判断某个进程号还在不在（跨平台，Windows 上先用 psutil 之外的办法）。"""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform.startswith("win"):
            # tasklist 是 Windows 自带的，不需要装任何东西。
            # 【为什么不打开进程句柄？】那要写 ctypes 调 win32 API，
            # 可读性差很多，而这里每 3 秒才跑一次，开销可以忽略。
            # ★ 【v0.4.4 修】errors="replace" 不能省，原因见 _kill() 的注释：
            #   带 CREATE_NO_WINDOW 时 Windows 自带命令会输出本地化中文（GBK），
            #   而程序在 PYTHONUTF8=1 下是按 UTF-8 解码的 —— 解码线程会抛异常，
            #   结果是 stdout 变成 None，"这个进程在不在"就永远判成"不在"。
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                capture_output=True, text=True, errors="replace", timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout or ""
            return str(pid) in out
        os.kill(pid, 0)          # 非 Windows：信号 0 表示"只检查，不真的发信号"
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _lock_held() -> bool:
    """
    锁文件是否被某个活着的进程占着。

    做法：用和窗口【完全一样】的方式去抢一次锁。
        · 抢到了  -> 说明本来没人占 -> 立刻释放，返回 False
        · 抢不到  -> 有人在用       -> 返回 True

    【为什么必须是"抢一次"而不是"试着打开文件"？】
        这一点踩过坑：最初写的是 `open(lock_file, "r+b")`，
        以为"打不开就是被占用"。**这是错的** ——
        在 Windows 上，一个文件被某进程用 msvcrt.locking 上了字节锁之后，
        别的进程依然能正常 open() 它，只是【读不到那几个被锁的字节】。
        所以"能打开"根本不能说明"没人在用"，结果就是
        "已经有一个窗口在跑了，服务端却以为没有" —— 于是又开一个。

        结论：判断"锁有没有被占"，唯一可靠的办法就是**真的去抢一次**，
        而且要用和占用方一模一样的方式（同样的粒度、同样的起始位置）。

    抢到之后马上释放，不会干扰任何人：
    真正的窗口此刻如果正在跑，我们本来就抢不到；
    如果它没在跑，我们抢到又立刻放开，回到"没人占"的状态，等价于没动过。
    """
    probe = acquire_window_lock()
    if probe is None:
        return True
    release_window_lock(probe)
    return False


def acquire_window_lock():
    """
    由【窗口进程】调用：抢下"座位"。抢不到返回 None（说明已经有一个窗口了）。

    返回一个打开的文件对象 —— 调用方必须一直持有它，
    一旦关闭（或进程退出），锁就释放了。

    ⚠️ 这个函数只在窗口进程里用；服务端只做"探测"（_lock_held）。
       两者角色的区别很重要：服务端是"敲门看看里面有没有人"，
       窗口是"把门锁上"。
    """
    lock_file = config.FLOATING_LOCK_FILE
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        handle = open(lock_file, "a+b")
    except OSError as exc:
        raise RuntimeError("无法打开悬浮窗锁文件：%s" % exc)

    if sys.platform.startswith("win"):
        import msvcrt
        try:
            # 文件要至少有 1 个字节才锁得住。用 "a+b" 打开时，
            # 写入总是追加到末尾 —— 所以先判断一下，别每次都往后追加一个 \0。
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            # LK_NBLCK = 非阻塞独占锁：抢不到就立刻抛错，不会卡住
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return None
    else:
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None

    return handle


def release_window_lock(handle) -> None:
    """窗口退出时释放座位（就算不调用，进程结束系统也会自动释放）。"""
    if handle is None:
        return
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)

    finally:
        try:
            handle.close()
        except OSError as exc:
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)



def read_seat() -> dict:
    """读 pid 文件（窗口自己写的"我在这儿"）。"""
    path = config.FLOATING_PID_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def clear_pid_file() -> None:
    """
    删掉 pid 文件（让下一个窗口可以从干净状态开始）。

    【为什么要单独一个函数？】
        Python 的 FileNotFoundError 在 Windows 上偶尔会以
        PermissionError 的形式出现（文件刚好被别的进程删掉）。
        所以这里统一吞掉异常 —— 删不掉不是致命问题，
        因为真正的"谁在运行"由文件锁说了算。
    """
    try:
        config.FLOATING_PID_FILE.unlink()
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)



def is_window_running() -> bool:
    """
    当前有没有窗口进程活着。

    判断顺序刻意如此：
        1. Windows 文件锁 —— 最可靠（进程死掉时系统自动释放）
        2. pid 文件 + 进程存在 —— 兜底（比如锁文件被用户手动删掉了）
    """
    seat = read_seat()
    pid = int(seat.get("pid") or 0)

    if _lock_held():
        # 锁被占着。但如果 pid 文件里那个进程已经不在了，
        # 说明锁是"上一个死掉的窗口"留下的（比如被强杀时系统延迟释放），
        # 这时必须清干净、允许重开 ——
        # 否则用户会陷入"再也开不起来"的困境，那比多开一个窗口糟得多。
        if pid and not _process_alive(pid):
            logger.warning("检测到残留的文件锁（pid=%s 已不存在），自动清理", pid)
            clear_pid_file()
            return False
        return pid > 0

    # 锁是空的但 pid 文件还在 -> 上次异常退出留下的残渣，顺手清掉
    if pid:
        clear_pid_file()
    return False


def window_status() -> dict:
    """给设置页显示的状态信息（人类看得懂的那种）。"""
    seat = read_seat()
    pid = int(seat.get("pid") or 0)
    running = is_window_running()
    cfg = get_config()

    if not is_supported():
        summary = "当前 Python 没有 tkinter，无法显示悬浮窗"
    elif not cfg["enabled"]:
        summary = "未开启"
    elif not cfg["visible"]:
        summary = "已开启，但窗口当前是隐藏的（点「立即显示」可让它出现）"
    elif running:
        summary = "运行中（进程号 %d）" % pid
    else:
        summary = "已开启，窗口正在启动中…"

    return {
        "supported": is_supported(),
        "running": running,
        "pid": pid if running else 0,
        "started_at": seat.get("started_at_text") or "",
        "summary": summary,
        "pythonw": _pythonw_hint(),
        "log_file": str(config.FLOAT_DIR / "window.log"),
    }


def _pythonw_hint() -> str:
    """
    给设置页用的说明文字：窗口是怎么被拉起来的。

    这段文字存在的意义：用户（和未来的你）看设置页时应该能立刻明白
    "窗口是服务的一个子进程"，而不是以为它是网页里的一个悬浮 div。
    """
    if config.FROZEN:
        return "安装版：由程序自己拉起一个无窗口的 Python 子进程"
    return "源码版：用 pythonw.exe 拉起悬浮窗（不会弹出黑色命令行窗口）"


def ensure_running(force: bool = False) -> dict:
    """
    确保窗口在跑（该开就开，已经在跑就什么都不做）。

    参数 force=True 表示"用户刚刚明确要求显示"，此时会清掉残留再启动。

    ⚠️ 整个函数体在 _SPAWN_LOCK 里执行，这是"不要开出两个窗口"的第一道闸门：
       守护线程和设置页按钮可能同时判断出"没有窗口"然后各拉一个起来。
       判断和启动必须是一个不可分割的动作 —— 这在并发编程里叫"检查-使用竞态"
       （check-then-act race），是初学者最容易踩的一类坑。
    """
    with _SPAWN_LOCK:
        cfg = get_config()
        if not cfg["enabled"]:
            return {"ok": False, "started": False, "message": "悬浮窗未开启"}

        if not is_supported():
            return {"ok": False, "started": False,
                    "message": "当前 Python 没有 tkinter，无法显示悬浮窗"}

        if is_window_running():
            return {"ok": True, "started": False, "message": "悬浮窗已经在运行"}

        # ---- ★ 冷却期（修 BUG-035）----
        # 【为什么要有它？】
        #   子进程要将近 1 秒才会把 pid 写进数据库，在这之前
        #   is_window_running() 一直回答"没有在跑"。
        #   于是连点几下「立即显示」，每一下都会再拉起一个进程
        #   （实测 20 个并发请求拉出了 13 个以上，只有 1 个真的画出窗口）。
        #   这里用"刚刚已经拉过一个"这个本地信号把这段时间兜住。
        global _last_spawn_at
        since = time.time() - _last_spawn_at
        if since < config.FLOATING_SPAWN_COOLDOWN_SECONDS:
            logger.debug("距上次拉起窗口只有 %.1f 秒，跳过本次启动", since)
            # ★ 【v0.4.4】文案改准一点：这句以前读起来像"已经好了、你别管"，
            #   而用户点「重启窗口/立即显示」的时刻，屏幕上其实还没有窗口 ——
            #   实测第二次点击会拿到这句话，虽然 1.6 秒后窗口确实起来了，
            #   但用户会以为按钮没生效（记成 OPT-034）。
            return {"ok": True, "started": False, "waiting": True,
                    "message": "窗口正在启动中（%.1f 秒前已拉起），请稍等一两秒" % since}

        # 清理上一次异常退出留下的壳（不清理的话新窗口写 pid 会被旧内容干扰）
        clear_pid_file()
        _trim_window_log()

        try:
            proc = _spawn()
        except OSError as exc:
            logger.error("启动悬浮窗失败：%s", exc)
            return {"ok": False, "started": False, "message": "启动失败：%s" % exc}

        _last_spawn_at = time.time()
        logger.info("已拉起悬浮窗进程 pid=%s", proc.pid)
        return {"ok": True, "started": True, "pid": proc.pid,
                "message": "悬浮窗已启动（进程号 %d）" % proc.pid}


def _spawn():
    """
    真正去启动一个窗口进程。

    【三种运行环境，三种启动方式】

     ① 源码运行（python run.py）
        -> 用 pythonw.exe 启动：它和 python.exe 是同一个程序，
           区别只是【不带控制台窗口】。用 python.exe 的话，
           屏幕上会多出一个黑色命令行窗口，很难看。

     ② 打包成 exe（安装版）
        -> 直接再用【同一个 exe】启动一次，并带上 --floating 参数。
           收到这个参数时程序【不会】再起一个 Web 服务，
           只会去连已经跑着的那个服务（见 run.py）。

     ③ pythonw 找不到（绿色版 Python、被精简过）
        -> 退回 python.exe，能跑就行，难看一点总比不能用好。
    """
    env = os.environ.copy()
    # 子进程要连的地址：如果将来把服务跑到别的端口，只要改环境变量，
    # 子进程会自己读一样的环境变量，不用在两个地方各写一遍。
    env["TODO_HOST"] = config.HOST
    env["TODO_PORT"] = str(config.PORT)
    env["TODO_DATA_DIR"] = str(config.DATA_DIR)

    # ★★ 令牌必须【传给子进程】★★
    #
    # 【为什么这一行是必须的？】
    #   /api/floating/data 这类接口要求带 X-Float-Token 请求头，
    #   而窗口是另一个进程，它没地方"查"这个令牌 ——
    #   唯一的传递途径就是我们启动它时的环境变量。
    #
    # 【漏了会怎样？】（这正是本次开发真实踩到的坑）
    #   窗口能起来，但每次调接口都收到 403。
    #   它的表现是"窗口闪一下就没了"：
    #   窗口每次失败就退出，守护线程 3 秒后又把它拉起来，
    #   于是"启动 -> 403 -> 退出 -> 再启动"无限循环，
    #   任务管理器里堆出一串 pythonw 进程，而用户一头雾水。
    #   —— 所以这一行配套了一条测试（tests/test_floating_service.py
    #      里的 TestSpawnEnvironment），专门盯着它。
    env["TODO_FLOAT_TOKEN"] = floating_repo.get_token()

    if config.FROZEN:
        cmd = [sys.executable, "--floating"]
        cwd = str(Path(sys.executable).resolve().parent)
    else:
        cmd = [str(_python_interpreter()), str(config.BASE_DIR / "run.py"), "--floating"]
        cwd = str(config.BASE_DIR)

    # Windows：CREATE_NO_WINDOW 防止弹出黑色控制台；
    # CREATE_NEW_PROCESS_GROUP 让窗口进程【不】跟着服务进程一起吃 Ctrl+C，
    # 这样服务重启时窗口还能活下来（也就不会出现"服务一重启窗口就消失"的怪现象）。
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    # 窗口进程的输出重定向到 data/floating/window.log。
    # 【为什么必须重定向？】窗口是 noconsole 启动的，它没有控制台，
    # 一旦抛异常，那段堆栈会消失得无影无踪 —— 到时候只能靠猜。
    log_path = config.FLOAT_DIR / "window.log"
    log_handle = open(log_path, "a", encoding="utf-8")

    try:
        return _popen(
            cmd, cwd=cwd, env=env,
            stdout=log_handle, stderr=subprocess.STDOUT,
            creationflags=creationflags, close_fds=True,
        )
    finally:
        # 父进程这边马上关掉句柄：真正的写入在子进程里，
        # 我们留着一个句柄只会让日志文件被"锁"着，不方便用户查看。
        log_handle.close()


def _popen(cmd, **kwargs):
    """
    真正调用 subprocess.Popen 的那一行。

    【为什么要多包一层这么"没有意义"的函数？】
        因为它是【唯一需要被测试替换掉的一步】。
        测试要验证"启动参数对不对"（令牌传了没、--floating 加了没、
        工作目录对不对），但绝不能真的去启动一个窗口进程。

        如果直接替换 _spawn()，那"启动参数对不对"这段代码就在测试里
        被整个跳过了 —— 而那恰恰是最容易出错的部分
        （BUG-023 就是漏传了一个环境变量）。
        所以把"组装参数"和"真正启动"分开，测试只替换后者：
        参数照样被组装、被断言，进程却不会被真的拉起来。
        —— 这类"为了可测性把一个动作切成两半"的做法，
           是开发中最值得付出的一点额外结构。
    """
    return subprocess.Popen(cmd, **kwargs)


def _python_interpreter() -> Path:
    """找出 pythonw.exe 的路径（找不到就退回当前解释器）。"""
    exe = Path(sys.executable)
    if sys.platform.startswith("win"):
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return candidate
    return exe


def _trim_window_log(max_bytes: int = 200_000) -> None:
    """
    窗口日志太大的话清掉重来。

    【为什么要管这个？】窗口每 10 秒刷一次，出了错就会一直刷堆栈，
    几天下来能长到几百 MB —— 而它的价值只是"出问题时看一眼"。
    保留最后一份（几十 KB）就够排查了。
    """
    path = config.FLOAT_DIR / "window.log"
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            path.write_text("", encoding="utf-8")
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)



def stop_window(reason: str = "", timeout: float = 3.0) -> bool:
    """
    让窗口自己退出。

    【为什么是"请它退出"而不是"杀掉它"？】
        直接 kill 会跳过 tkinter 的清理：窗口位置 / 大小还没来得及存，
        用户刚拖动过的地方下次就丢了。
        所以这里写一个"停"字给窗口看，让它自己保存设置再退出；
        只有它赖着不走（超过 timeout 秒）才动手强杀 —— 这是最后手段。
    """
    if not is_window_running():
        clear_pid_file()
        return False

    seat = read_seat()
    pid = int(seat.get("pid") or 0)
    stop_file = config.FLOAT_DIR / "stop.flag"
    try:
        stop_file.write_text("stop\n", encoding="ascii")
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)


    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_window_running():
            break
        time.sleep(0.2)

    still_alive = is_window_running()
    if still_alive and pid:
        logger.warning("悬浮窗没有响应退出信号（pid=%s），改为强制结束", pid)
        _kill(pid)

    try:
        stop_file.unlink()
    except OSError as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("文件 / 系统调用失败，按可忽略处理：%s", exc, exc_info=True)


    floating_repo.clear_window_seat(pid=pid or None)
    clear_pid_file()
    if reason:
        logger.info("已停止悬浮窗（%s）", reason)
    return True


def _kill(pid: int) -> None:
    """强制结束进程（taskkill /F）。"""
    try:
        if sys.platform.startswith("win"):
            # ★ 【v0.4.4 修】errors="replace" 是必须的：
            #   带 CREATE_NO_WINDOW 调用 Windows 自带命令时，
            #   taskkill 输出的是【本地化中文】字节（GBK，实测是 "成功: 已终止 PID …"），
            #   而本程序在 PYTHONUTF8=1 下按 UTF-8 解码 → 读取线程抛
            #   UnicodeDecodeError → 日志里每次强杀窗口都多一条 CRITICAL，
            #   把真正的问题淹没掉（这类"假崩溃日志"比没有日志更糟）。
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True, text=True, errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError) as exc:
        # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
        logger.debug("进程操作失败，按可忽略处理：%s", exc, exc_info=True)



def restart_window() -> dict:
    """重启窗口（改了需要重启才生效的东西时用；也可以用来"卡了就重开一下"）。"""
    # ★ 用户主动要求重启 = 解除退避（否则可能撞上"已经放弃"的状态，
    #   什么都没试就直接报错，那会让人以为按钮坏了）。
    reset_supervisor_backoff()
    stop_window(reason="重启")
    time.sleep(0.4)
    return ensure_running(force=True)


def show_now() -> dict:
    """
    "立即显示"按钮：把 visible 打开并拉起窗口。

    【为什么需要它？】
        用户点了窗口右上角的 × 之后，窗口只是隐藏（不是关闭功能），
        否则他得回到网页去重新开启 —— 那太绕了。
        但隐藏之后总得有个地方能把它叫回来，就是这里。
    """
    # 同上：用户明确要求显示 -> 解除退避，给他一次"干净的重试"
    reset_supervisor_backoff()
    cfg = update_config({"enabled": True, "visible": True})
    result = ensure_running(force=True)
    result["config"] = cfg
    return result


def status() -> dict:
    """设置页 / 接口用的完整状态。"""
    return {
        "config": get_config(),
        "window": window_status(),
        "supervisor": supervisor_health(),
        "supported": is_supported(),
        "defaults": defaults(),
        "themes": config.FLOATING_THEMES,
        "fonts": list(config.FLOATING_FONT_PRESETS),
        "choices": {
            "group_by": {k: config.FLOATING_GROUP_BY_NAMES[k]
                         for k in config.FLOATING_GROUP_BY_CHOICES},
            "scope": {k: config.FLOATING_SCOPE_NAMES[k]
                      for k in config.FLOATING_SCOPE_CHOICES},
        },
        "limits": {
            # 透明度同时给出"上下限""当前默认值""开始发虚的临界点"，
            # 设置页上的滑块和文案都由它决定（前端不写死任何数字）——
            # 这样以后调整范围，只改 config.py 一处，界面自动跟上。
            "opacity": [config.FLOATING_MIN_OPACITY, config.FLOATING_MAX_OPACITY],
            "opacity_default": config.FLOATING_DEFAULT_OPACITY,
            "clarity_warn_below": config.FLOATING_CLARITY_WARN_BELOW,
            "font_size": [config.FLOATING_MIN_FONT_SIZE, config.FLOATING_MAX_FONT_SIZE],
            "font_size_default": config.FLOATING_DEFAULT_FONT_SIZE,
            "width": [config.FLOATING_MIN_WIDTH, config.FLOATING_MAX_WIDTH],
            "height": [config.FLOATING_MIN_HEIGHT, config.FLOATING_MAX_HEIGHT],
        },
        "quick_remind": {
            "minutes": list(config.FLOATING_QUICK_REMIND_MINUTES),
            "today_at": config.FLOATING_REMIND_TODAY_AT,
            "tomorrow_at": config.FLOATING_REMIND_TOMORROW_AT,
        },
        "token": floating_repo.get_token(),
    }


# ===========================================================================
# 五、守护线程：让"设置"和"实际有没有窗口"保持一致
#
# 【v0.3.2 新增：失败退避（指数退避）】
#
#   背景是 BUG-027 的复盘：那次"忘了传令牌"，窗口一起来就被 403 拒绝、
#   然后自己退出；守护线程本着"窗口掉了就拉起来"的职责，
#   3 秒后又拉一个 —— 于是变成**无限重启**，
#   任务管理器里堆出一串 pythonw，日志被刷屏，而用户完全不知道发生了什么。
#
#   问题不在"自愈"这个设计（它是对的），而在于：
#   **自愈机制没有刹车。** 一个"配置类错误"（改一次就好了）被放大成了
#   "持续的资源消耗 + 无法定位的现象"。
#
#   所以这里加三层保护：
#       连续失败 3 次  -> 检查间隔从 3 秒退避到 30 秒（少刷日志、少拉进程）
#       连续失败 10 次 -> 停下来，并在设置页把原因报给用户
#       用户改设置 / 点"立即显示" -> 立刻解除退避，重新开始（给人一个"重试"的抓手）
#
#   这套思路在运维里叫【指数退避 / exponential backoff】，
#   是所有"自动重试"逻辑的标准配套。
# ===========================================================================

# 连续失败到这个次数就"放慢"，到这个次数就"停下"
BACKOFF_AFTER_FAILURES = 3
GIVE_UP_AFTER_FAILURES = 10
BACKOFF_INTERVAL_SECONDS = 30

_supervisor_thread = None
_supervisor_stop = threading.Event()
_last_supervisor_run = {
    "at": None,          # 上一次检查的时刻
    "action": "",        # 上一次做了什么（给设置页显示）
    "error": None,       # 上一次性错误（异常，不是"启动失败"）
    "failures": 0,       # ★ 连续启动失败了几次（成功一次就清零）
    "backing_off": False,     # 当前是否处于"放慢"状态
    "given_up": False,        # 是否已经放弃（不再自动重试）
    "last_failure": "",       # 最后一次失败的原因（给人看的）
    # ★ v0.3.4 新增（修 BUG-034）：刚刚拉起、但【还没确认活下来】的窗口进程号。
    #   0 表示"没有待确认的启动"。
    #   为什么要它？见 _settle_pending_spawn() 的说明。
    "pending_pid": 0,
    "pending_at": 0.0,   # 上面那个进程是什么时候拉起的（用来算宽限期）
}

# ★ v0.3.4 新增（修 BUG-035）：上一次成功拉起窗口的时刻。
#   冷却期用它判断"是不是刚刚已经拉过一个了"。
#   为什么用模块级变量而不是写进数据库？——它只是"这个进程内的节流阀"，
#   不需要跨进程共享，也不需要持久化。
_last_spawn_at = 0.0


def start_supervisor():
    """
    启动守护线程（服务启动时调用一次）。

    【它到底干什么？】
        每 3 秒看一眼设置：
            开启 + 显示，但没有窗口 -> 拉起一个
            关闭 或 隐藏，但有窗口 -> 让窗口退出
        就这样。听起来简单，但它是"设置页开关"和"真有一个窗口"之间的桥。

    【为什么不用"设置页点一下才启动"？】
        因为窗口可能因为各种原因死掉（内存不足、用户强杀、屏幕分辨率变了）。
        "服务端有个东西一直盯着"才能保证
        "只要你开着这个功能，窗口就在"。
    """
    global _supervisor_thread
    if _supervisor_thread is not None and _supervisor_thread.is_alive():
        return False

    _supervisor_stop.clear()
    _supervisor_thread = threading.Thread(
        target=_supervisor_loop, name="floating-supervisor", daemon=True)
    _supervisor_thread.start()
    logger.info("悬浮窗守护线程已启动（每 %d 秒检查一次）"
                % config.FLOATING_SUPERVISOR_SECONDS)
    return True


# 旧版本（0.3.0 开发期）透明度的上限：那时候这个值是用【百分比】存的（30 = 30%）。
# 后来统一改成 0~1 的小数，就需要一次数据迁移。
# 阈值定在 5 是安全的：新的合法值恰好落在 0.05 ~ 1.0，永远小于 5。
LEGACY_OPACITY_THRESHOLD = 5.0


def migrate_legacy_settings() -> dict:
    """
    把旧版本留下的"百分比透明度"换算成小数（服务启动时跑一次）。

    【为什么需要它？】（这是改动默认值时最容易被忘掉的一步）
        项目开发过程中，透明度的合法范围曾经是"30 ~ 100 的整数"。
        后来为了让滑块能拉到"几乎全透明"，改成了"0.05 ~ 1.0 的小数"。

        麻烦在于：**用户数据库里已经存着旧格式的值**。
        如果不管它，会出现两种难受的情况：
            ① 旧值 92 直接喂给 tkinter 的 -alpha 参数 -> 远超 1.0，
               tkinter 会直接报错，窗口开不出来（而用户完全不知道为什么）；
            ② 或者被静默地当成 1.0，用户发现自己调的透明度"失效了"。

        所以升级时主动换算一次：92 -> 0.92、30 -> 0.30。
        这类"改了数据格式，就得顺手把老数据搬家"的活儿叫【数据迁移】，
        是任何会长期演进的软件都躲不掉的一件事。

    【为什么不干脆把用户的设置清空、用新默认值？】
        因为透明度只是他调过的众多设置之一。直接清空会顺手抹掉
        他精心调好的字号、颜色、位置 —— 那是一种"为了省事而破坏用户数据"，
        违反"永远不要替用户判断这些东西不重要"（数据安全铁律四）。

    返回一个说明字典（有没有迁移、迁移成了什么值），方便记进日志。
    """
    opacity_key = floating_repo.FIELDS["opacity"][0]
    raw = setting_repo.get(opacity_key)
    if raw in (None, ""):
        return {"migrated": False, "reason": "没有保存过透明度"}

    try:
        value = float(raw)
    except (TypeError, ValueError):
        return {"migrated": False, "reason": "透明度的值不是数字：%r" % raw}

    if not (LEGACY_OPACITY_THRESHOLD <= value <= 100):
        # 落在新范围里的值（0.05~1.0）或明显异常的值，都不用动
        return {"migrated": False, "reason": "透明度已经是新格式（%s）" % raw}

    new_value = round(value / 100.0, 2)
    setting_repo.set_value(opacity_key, new_value)
    logger.info("悬浮窗设置迁移：透明度 %s%% -> %s（旧格式换算成小数）",
                raw, new_value)
    return {"migrated": True, "old": value, "new": new_value}


def stop_supervisor():
    """服务关闭时调用。"""
    _supervisor_stop.set()
    stop_window(reason="服务正在关闭")


def supervisor_running() -> bool:
    return _supervisor_thread is not None and _supervisor_thread.is_alive()


def supervisor_last_run() -> dict:
    return dict(_last_supervisor_run)


def _note_spawn_success():
    """启动成功：把"连续失败"清零，解除退避。"""
    if _last_supervisor_run["failures"] or _last_supervisor_run["given_up"]:
        logger.info("悬浮窗恢复正常，失败计数清零（之前连续失败 %d 次）",
                    _last_supervisor_run["failures"])
    _last_supervisor_run["failures"] = 0
    _last_supervisor_run["backing_off"] = False
    _last_supervisor_run["given_up"] = False
    _last_supervisor_run["last_failure"] = ""


def _note_spawn_pending(pid) -> None:
    """
    记下"刚拉起了一个窗口，但还不知道它能不能活"。

    ★ 这是修 BUG-034 的关键一步。

    【原来的问题】
        拉起窗口成功时直接调 _note_spawn_success() 把失败计数清零。
        可是"Popen 返回了"只说明【进程创建成功】，不说明它活着 ——
        一个"起来 2 秒后因为令牌不对被 403 拒绝而退出"的窗口，
        同样会被记成一次成功。

        结果是：BUG-027 那种"启动 → 崩溃 → 再启动"的无限循环里，
        失败计数永远是 0，退避和"放弃重试"这两道刹车【永远踩不下去】。

    【现在的做法】
        把"拉起成功"记成【待确认】，等【下一次 tick】再看窗口还在不在：
        在  -> 才算成功（_note_spawn_success）
        不在-> 算一次失败（_note_spawn_failure）
        这样刹车看的信号（进程存活）就和故障表现是同一个量了。
    """
    _last_supervisor_run["pending_pid"] = int(pid or 0)
    _last_supervisor_run["pending_at"] = time.time()
    logger.info("已拉起悬浮窗进程 pid=%s（下一次检查确认它是否存活）", pid)


def _settle_pending_spawn(running: bool) -> str:
    """
    结算上一次"待确认"的启动。返回一句话（没待确认的就返回空字符串）。

    参数 running 是本次 tick 开头测到的"窗口在不在跑"。
    """
    pid = _last_supervisor_run.get("pending_pid") or 0
    if not pid:
        return ""

    if running:
        # 上一次拉起的窗口活到了现在 —— 这才是一次真正的成功
        _last_supervisor_run["pending_pid"] = 0
        _note_spawn_success()
        return "窗口已确认存活（pid=%s）" % pid

    # ★ 宽限期：窗口要 1 秒左右才写完座位信息。
    #   等够时间再下结论，否则会把"注册得慢"冤枉成"启动失败"。
    waited = time.time() - (_last_supervisor_run.get("pending_at") or 0.0)
    if waited < config.FLOATING_PENDING_GRACE_SECONDS:
        return "等待窗口完成注册（已等 %.1f 秒）" % waited

    _last_supervisor_run["pending_pid"] = 0
    # 拉起去了但已经没了：这是一次真实的启动失败（BUG-027 就是这种）
    return _note_spawn_failure(
        "窗口进程 %s 启动后立刻退出，请查看 data/floating/window.log" % pid)


def _note_spawn_failure(reason: str) -> str:
    """
    启动失败：累计次数，必要时进入退避或放弃。

    返回一句"该做什么"，会被设置页显示出来 ——
    **报错必须带上"怎么办"**，否则用户只知道坏了、不知道能做什么。
    """
    state = _last_supervisor_run
    state["failures"] += 1
    state["last_failure"] = reason or "未知原因"
    count = state["failures"]

    if count >= GIVE_UP_AFTER_FAILURES:
        state["given_up"] = True
        state["backing_off"] = True
        logger.error(
            "悬浮窗连续启动失败 %d 次，已停止自动重试。最后一次原因：%s。"
            "请到设置页点「重启窗口」重试，或查看 data/floating/window.log",
            count, reason)
        return "已停止自动重试（连续失败 %d 次）" % count

    if count >= BACKOFF_AFTER_FAILURES:
        state["backing_off"] = True
        logger.warning(
            "悬浮窗连续启动失败 %d 次（%s），检查间隔暂时放慢到 %d 秒",
            count, reason, BACKOFF_INTERVAL_SECONDS)
        return "启动失败 %d 次，已放慢重试" % count

    return "启动失败（第 %d 次）：%s" % (count, reason)


def reset_supervisor_backoff() -> None:
    """
    解除退避，立刻重新开始尝试。

    【谁调用它？】用户在设置页点「立即显示」「重启窗口」，
    或者改了与悬浮窗有关的设置时。

    【为什么必须留这个出口？】
        退避和放弃都是为了"不要把小问题放大"，但它们同时意味着
        **自动恢复停止了**。如果用户修好了原因（比如把服务修好、把窗口进程清干净），
        却还得等很久才会重试 —— 那体验很差。
        给他一个"我现在就想试一次"的按钮，是这套机制的必要配套。
    """
    state = _last_supervisor_run
    if state["failures"] or state["given_up"]:
        logger.info("用户主动重试，已解除悬浮窗的退避状态")
    state["failures"] = 0
    state["backing_off"] = False
    state["given_up"] = False
    # 待确认的那次启动也一并作废：用户刚动过手，应该以"从现在开始"为准
    state["pending_pid"] = 0


def supervisor_health() -> dict:
    """
    给设置页看的"守护线程健康状况"（把人话和原因一起返回）。

    【为什么要把这些状态暴露到界面？】
        因为退避和放弃都是**看不见的行为**：程序"不再尝试拉起窗口"这件事
        本身不会弹窗、不会报错。不告诉用户，他只会觉得"这功能坏了"。
        所以这里把它变成一句能显示的话 + 一个"该怎么办"的提示。
    """
    state = _last_supervisor_run
    if state["given_up"]:
        return {
            "ok": False,
            "level": "error",
            "failures": state["failures"],
            "backing_off": True,
            "given_up": True,
            "reason": state["last_failure"],
            "summary": "已停止自动重试（连续失败 %d 次）：%s"
                       % (state["failures"], state["last_failure"]),
            "advice": "请点「重启窗口」重试；如果还是失败，"
                      "点「打开窗口日志目录」看 window.log 里的报错原文。",
        }
    if state["backing_off"]:
        return {
            "ok": False,
            "level": "warn",
            "failures": state["failures"],
            "backing_off": True,
            "given_up": False,
            "reason": state["last_failure"],
            "summary": "启动失败 %d 次，检查间隔已放慢到 %d 秒（原因：%s）"
                       % (state["failures"], BACKOFF_INTERVAL_SECONDS,
                          state["last_failure"]),
            "advice": "窗口会继续自动重试，只是间隔变长了。"
                      "想立刻再试一次就点「重启窗口」。",
        }
    return {
        "ok": True,
        "level": "ok",
        "failures": state["failures"],
        "backing_off": False,
        "given_up": False,
        "reason": "",
        "summary": "正常",
        "advice": "",
    }


def _supervisor_loop():
    """
    线程主体。

    ⚠️ 和 scheduler.py 一样的纪律：循环体必须包在 try 里。
       后台线程抛异常不会被任何人看到 —— 它会安静地死掉，
       而程序表面上一切正常（这类静默死亡最难查，见 BUG-012）。

    另外启动后先等 5 秒：让 Web 服务先完全起来。
    窗口一起来就会立刻去调接口，服务没起来的话它会拉不到数据
    （虽然它会重试，但会让日志里多出一堆无意义的失败记录）。
    """
    _supervisor_stop.wait(5.0)

    while not _supervisor_stop.is_set():
        try:
            action = _supervisor_tick()
            _last_supervisor_run["at"] = timeutil.now_str()
            _last_supervisor_run["action"] = action
            _last_supervisor_run["error"] = None
        except Exception as exc:            # noqa: BLE001 - 后台线程必须吞掉所有异常
            _last_supervisor_run["error"] = "%s: %s" % (type(exc).__name__, exc)
            logger.exception("悬浮窗守护线程出错")

        try:
            _supervisor_stop.wait(_next_interval())
        except Exception:                   # noqa: BLE001
            time.sleep(config.FLOATING_SUPERVISOR_SECONDS)


def _next_interval() -> float:
    """
    下一次检查该等多久（正常 3 秒；退避时 30 秒；放弃后 60 秒）。

    【为什么"放弃"之后还要继续循环，而不是直接 return 退出线程？】
        两个原因：
          ① 用户可能点「重启窗口」把状态清掉 —— 线程如果退出了，
             就没人接着管了，得重启程序才行；
          ② 用户也可能直接改设置（比如关掉再打开悬浮窗），
             那也意味着"情况变了，再试试"。
        所以线程一直活着，只是**等得越来越久**，几乎不消耗资源。
    """
    state = _last_supervisor_run
    if state["given_up"]:
        return BACKOFF_INTERVAL_SECONDS * 2
    if state["backing_off"]:
        return BACKOFF_INTERVAL_SECONDS
    return max(1, int(config.FLOATING_SUPERVISOR_SECONDS))


def _supervisor_tick() -> str:
    """
    守护线程的一次检查，返回它做了什么（方便设置页显示、也方便排查）。

    【★★ 修 BUG-034 时改掉的一处逻辑】
        以前：ensure_running() 返回 started=True 就直接 _note_spawn_success()。
        现在：先结算上一次"待确认"的启动，再决定这次要不要拉起。
        这样一来，"窗口一起来就崩"会被如实记成【连续失败】，
        退化到 3 次放慢、10 次停下 —— 也就是 OPT-025 当初承诺的行为。
    """
    cfg = get_config()
    running = is_window_running()

    if not cfg["enabled"] or not cfg["visible"]:
        if running:
            stop_window(reason="设置里未开启或已隐藏")
            return "已停止窗口"
        # 【用户关掉悬浮窗 = 情况变了】顺手把退避状态清掉。
        # 这样他下次再打开时，是"从零开始试"，而不是背着上一次的失败计数
        # （否则可能一打开就撞上"已经放弃"的状态，什么都没试就报错）。
        reset_supervisor_backoff()
        return "无需操作"

    # ---- ★ 先结算"上一次拉起、还没确认"的那个窗口（修 BUG-034）----
    #   返回非空就表示本次 tick 已经做了判断（记成功或记失败），不再往下走。
    settled = _settle_pending_spawn(running)
    if settled:
        return settled

    if not running:
        # ★ 已经放弃的情况下不再自动重试（这正是"刹车"的意义）。
        #   但注意：这里【不】返回错误 —— 它只是"等用户来处理"，
        #   设置页会通过 supervisor_health() 把这个状态显示出来。
        if _last_supervisor_run["given_up"]:
            return "已停止自动重试，等待用户处理"

        result = ensure_running()
        if result.get("started"):
            # ★ 不要在这里记"成功"：Popen 返回 ≠ 窗口活着（BUG-034）。
            #   先记成"待确认"，下一次 tick 再看它还在不在。
            _note_spawn_pending(result.get("pid"))
            return result.get("message", "已启动窗口")

        # 没启动成功：三种情况要分开看
        message = result.get("message", "启动失败")
        if result.get("waiting"):
            # ★ 不是失败：只是刚刚拉过一个、还在冷却期里（BUG-035 的节流阀）
            return message
        if "已经在运行" in message:
            # 不是失败，只是"已经有了"（与其他路径竞态）
            _note_spawn_success()
            return message
        return _note_spawn_failure(message)

    # 有窗口在跑 —— 这是"成功"的证据（不管它怎么起来的）
    _note_spawn_success()
    return "运行中"
