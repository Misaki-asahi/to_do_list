# -*- coding: utf-8 -*-
"""
floating_repo.py -- 悬浮窗设置的数据访问层（v0.3.0 新增）。

【为什么不用新建一张表？】
    悬浮窗的设置（位置、大小、透明度、字号、颜色、分块方式……）
    全都是"一个名字对应一个值"，而项目里 【settings 表】就是干这个的
    （键值对结构，见 models.Setting 的注释）。

    复用它的三个好处：
        1. 不用改表结构 —— 改表结构是有风险的操作，而且老用户的库要迁移；
        2. 以后想加一项设置（比如"标题栏显示日期"），这里加一行就行；
        3. 备份 / 恢复的逻辑不用动，悬浮窗设置跟着数据库一起被备份。

【这一层只做存取，不做判断】
    "透明度必须在 0.3~1.0 之间"这种规则属于 services 层的事。
    这里只负责把值读出来、写进去，并且负责【把字符串转成正确的类型】。

【为什么所有值都要转成字符串再存？】
    因为 settings 表的 value 列是 Text（见 models.py）。
    读出来时由这一层按字段类型转回去（bool / int / float），
    这样上层拿到的就是 Python 类型，不用自己记得"这个字段是 int"。
"""

from app import config
from app.repositories import setting_repo

# ---------------------------------------------------------------------------
# 字段清单：一个"名字 -> (设置表里的键, 类型, 默认值)"的映射表
#
# 【为什么要这张表？】—— 这是本文件的核心设计
#   如果没有它，就得手写这样一堆代码：
#       _get_int("fw_x", 80)、_get_int("fw_y", 80)、_get_int("fw_w", 320) ...
#       if "x" in changes: setting_repo.set_value("fw_x", changes["x"])
#   十几个字段 = 几十行几乎一样的代码，改一个字段要动三个地方。
#
#   有了这张表，"读全部""写一批""取默认值"全都变成一次循环，
#   加新字段只需要在表里加一行 —— 这类"把重复代码压成数据"的做法
#   叫【表驱动】，字段一多就非常划算。
# ---------------------------------------------------------------------------

# 设置表里的键前缀。加前缀是为了和别的设置（autostart_enabled 等）区分开，
# 一眼就能看出"这行是悬浮窗的设置"。
PREFIX = "floating_"

FIELDS = {
    # 名字（接口 / 前端用的）  : (设置表的键,         类型,    默认值)
    # ★★【v0.4.2：这三项的默认值 False -> True】
    #   用户 2026-09-14 要求："app默认开机静默自启，悬浮窗默认启动"。
    #
    #   这三项的分工（容易混，写清楚）：
    #       enabled   —— 悬浮窗功能的【总开关】（关掉它，守护线程会去把窗口停掉）
    #       visible   —— 窗口当前是否显示（点 ✕ 只是把它设成 False，功能还开着）
    #       autostart —— 偏好："开机自启时顺便把悬浮窗显示出来"
    #   三个默认都设为 True，出厂就是"启动程序 -> 桌面上就有待办便签"。
    #
    #   ⚠️ 老用户库里已经存了值的，**以库里的为准**（默认值只在"没有记录"时生效）。
    "enabled":             (PREFIX + "enabled",      "bool",  True),
    "autostart":           (PREFIX + "autostart",    "bool",  True),
    "visible":             (PREFIX + "visible",      "bool",  True),
    "x":                   (PREFIX + "x",            "int",   config.FLOATING_DEFAULT_X),
    "y":                   (PREFIX + "y",            "int",   config.FLOATING_DEFAULT_Y),
    "width":               (PREFIX + "width",        "int",   config.FLOATING_DEFAULT_WIDTH),
    "height":              (PREFIX + "height",       "int",   config.FLOATING_DEFAULT_HEIGHT),
    "opacity":             (PREFIX + "opacity",      "float", config.FLOATING_DEFAULT_OPACITY),
    "font_family":         (PREFIX + "font_family",  "str",   config.FLOATING_FONT_FAMILY),
    "font_size":           (PREFIX + "font_size",    "int",   config.FLOATING_DEFAULT_FONT_SIZE),
    "fg_color":            (PREFIX + "fg_color",     "str",   config.FLOATING_DEFAULT_FG),
    "bg_color":            (PREFIX + "bg_color",     "str",   config.FLOATING_DEFAULT_BG),
    "accent_color":        (PREFIX + "accent_color", "str",   config.FLOATING_DEFAULT_ACCENT),
    "always_on_top":       (PREFIX + "on_top",       "bool",  config.FLOATING_ALWAYS_ON_TOP),
    "show_completed":      (PREFIX + "show_done",    "bool",  config.FLOATING_SHOW_COMPLETED),
    "highlight_reminders": (PREFIX + "highlight",    "bool",  config.FLOATING_HIGHLIGHT_REMINDERS),
    "group_by":            (PREFIX + "group_by",     "str",   config.FLOATING_DEFAULT_GROUP_BY),
    "scope":               (PREFIX + "scope",        "str",   config.FLOATING_DEFAULT_SCOPE),
    # 窗口刷新用的"数据版本号"：服务端每次改动待办都会让它 +1，
    # 窗口靠它判断"数据变了没有"，没变就一个字都不重画（省 CPU、也不闪）。
    "data_version":        (PREFIX + "data_version", "int",   0),
}

# 窗口"座位"信息（谁在运行、什么时候启动的）—— 这些【不对外返回】，
# 因为它们是运行状态而不是用户设置，混在设置里会让前端很困惑。
INTERNAL_KEYS = ("pid", "started_at", "token")
INTERNAL_FIELDS = {
    "pid":        (PREFIX + "pid",        "int",   0),
    "started_at": (PREFIX + "started_at", "float", 0.0),
    "token":      (PREFIX + "token",      "str",   ""),
    # 自启脚本的"改动时间"：用来发现"用户在启动文件夹里动了手脚/被别的程序删了"
    "launcher_mtime": (PREFIX + "launcher_mtime", "float", 0.0),
}


def _to_bool(text, default=False) -> bool:
    """
    把设置表里的字符串转成布尔值。

    宽容一点：1 / true / yes / on 都算真 ——
    因为这个值可能来自用户手改的数据库、旧版本写入的内容，
    与其抛异常让程序起不来，不如按常识理解它。
    """
    if text is None:
        return default
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def _cast(kind: str, text, default):
    """按类型表把字符串转成目标类型；转不动就退回默认值。"""
    if text is None or str(text).strip() == "":
        return default
    try:
        if kind == "bool":
            return _to_bool(text, default)
        if kind == "int":
            return int(float(text))     # 先转 float 是为了兼容 "320.0" 这种历史值
        if kind == "float":
            return float(text)
        return str(text)
    except (TypeError, ValueError):
        # 【为什么容错而不是报错？】
        #   这个函数被"启动守护线程"调用。如果它抛异常，
        #   后台线程会安静地死掉 —— 用户只会看到"悬浮窗再也开不起来了"。
        #   一个坏掉的设置值不值得让整个功能瘫痪，所以退回默认值。
        return default


def get_config() -> dict:
    """读出悬浮窗的全部设置，返回【带正确 Python 类型】的字典。

    只包含 FIELDS 里的项（不含 pid / token 这些内部信息）。
    """
    raw = setting_repo.all_settings()
    result = {}
    for name, (key, kind, default) in FIELDS.items():
        result[name] = _cast(kind, raw.get(key), default)
    return result


def set_config(**changes) -> dict:
    """写入若干个设置，返回写完之后【完整】的设置（方便直接回给前端）。

    changes 里的键必须是 FIELDS 里有的名字；别的键会被忽略
    （这不是"宽容"，而是必要的：防止有人通过接口往设置表里塞任意内容）。
    """
    for name, value in changes.items():
        if name not in FIELDS:
            continue
        key, kind, _default = FIELDS[name]
        if kind == "bool":
            # 统一存成 "1" / "0"，这样在数据库里一眼就能看懂，也方便 SQL 查询
            setting_repo.set_value(key, "1" if value else "0")
        else:
            setting_repo.set_value(key, value)
    return get_config()


def bump_data_version(step: int = 1) -> int:
    """
    把"数据版本号"+1，返回新值。

    【为什么需要它？】
        悬浮窗是一个独立进程，它只能靠"不断去问服务端"来知道数据变了没有。
        如果每次都无脑重画整个列表，会有两个毛病：
            ① 白白耗 CPU（每 10 秒重画上百个控件）；
            ② 用户正在拖滚动条，列表"跳"一下 —— 很难受。

        有了版本号，窗口先比一下："还是那个数？那我什么都不做。"
        这一招在 Web 开发里叫 ETag / 缓存校验，道理完全一样。
    """
    # ★【为什么要用一条 SQL 而不是"读出来 +1 再写回去"？（BUG-035 的同类问题）】
    #   读-改-写分三步：两个线程可能同时读到 5，各自写回 6 ——
    #   两次"加一"只生效了一次（这叫"丢失更新"）。
    #   交给数据库在一条语句里自增，就不存在这个缝隙。
    from sqlalchemy import Integer, cast
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.core import timeutil
    from app.database import get_session
    from app.models import Setting

    key = FIELDS["data_version"][0]
    stmt = sqlite_insert(Setting).values(
        key=key, value=str(step), updated_at=timeutil.now_str())
    stmt = stmt.on_conflict_do_update(
        index_elements=[Setting.key],
        set_={"value": cast(cast(Setting.value, Integer) + step, Setting.value.type),
              "updated_at": timeutil.now_str()},
    )
    with get_session() as session:
        session.execute(stmt)
    return get_data_version()


def get_data_version() -> int:
    """只读数据版本号（接口返回给窗口用）。"""
    return _cast("int", setting_repo.get(FIELDS["data_version"][0]), 0)


# ---------------------------------------------------------------------------
# 内部信息（进程座位）
# ---------------------------------------------------------------------------


def get_internal() -> dict:
    """读出 pid / started_at / token 等运行状态。"""
    raw = setting_repo.all_settings()
    result = {}
    for name, (key, kind, default) in INTERNAL_FIELDS.items():
        result[name] = _cast(kind, raw.get(key), default)
    return result


def get_token() -> str:
    """取（或首次生成）接口访问令牌。

    【为什么悬浮窗要令牌？】
        因为多了一层"只有本机自己人才能用"的闸门：
        令牌是随机的、只存在本机数据库里，别人（比如同一局域网内的网页）
        猜不到它，就没法通过悬浮窗接口改写你的设置或杀掉窗口。
        项目将来万一部署给别人用，这一层就是现成的保护。
    """
    token = get_internal().get("token") or ""
    if not token:
        token = _new_token()
        setting_repo.set_value(INTERNAL_FIELDS["token"][0], token)
    return token


def ensure_token() -> str:
    """确保令牌存在并返回（供设置页"重新生成令牌"用）。"""
    return get_token()


def set_internal(**changes) -> dict:
    """写入运行状态（pid / started_at / launcher_mtime）。"""
    for name, value in changes.items():
        if name not in INTERNAL_FIELDS:
            continue
        key, _kind, _default = INTERNAL_FIELDS[name]
        setting_repo.set_value(key, value)
    return get_internal()


def clear_window_seat(pid: int = None) -> None:
    """
    把 pid 清成 0（表示"当前没有窗口在跑"）。

    【为什么要判断 pid？】
        新窗口启动时会先写自己的 pid，然后（在极端情况下）
        旧窗口的清理代码也可能在之后才执行。如果不判断就清零，
        会把新窗口的座位抹掉，于是"点两次开启"就能开出第二个窗口来。
        只清"还是我自己"的那一份，才安全。
    """
    current = get_internal().get("pid") or 0
    if pid is None or current == pid:
        setting_repo.set_value(INTERNAL_FIELDS["pid"][0], 0)


def _new_token() -> str:
    """生成一个随机令牌（标准库 secrets，密码学安全）。"""
    import secrets
    return secrets.token_urlsafe(24)
