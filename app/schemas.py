# -*- coding: utf-8 -*-
"""
schemas.py -- 接口数据模型（Pydantic）。

【和 models.py 的区别，这是初学者最容易搞混的一点】

    models.py   -> 描述"数据库表长什么样"   （SQLAlchemy，负责存）
    schemas.py  -> 描述"接口收发什么 JSON"  （Pydantic，负责校验）

【为什么必须分开？】
    如果共用 models.Task，用户在提交"新建待办"的 JSON 时就可以写：
        {"id": 999, "created_at": "1999-01-01 00:00", "title": "..."}
    数据库里就会出现 id 混乱、创建时间被伪造的脏数据。

    分开之后，TaskCreate 里【根本没有】id / created_at 这两个字段，
    用户想传也传不进来 —— 这叫"从结构上杜绝"，比事后检查可靠得多。
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import config
from app.core import timeutil


class TaskCreate(BaseModel):
    """【新建待办】接口接收的数据。

    这里只允许出现"用户可以自己填"的字段。
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=config.TITLE_MAX_LENGTH,
        description="标题，必填",
        examples=["明天 8:30 交作业"],
    )
    description: str | None = Field(
        None, max_length=config.DESCRIPTION_MAX_LENGTH, description="详细描述"
    )
    priority: int = Field(
        config.PRIORITY_NORMAL, ge=0, le=2, description="优先级：0 低 / 1 中 / 2 高"
    )
    due_at: str | None = Field(None, description="截止时间，格式 YYYY-MM-DD HH:MM")
    remind_at: str | None = Field(None, description="提醒时间，格式 YYYY-MM-DD HH:MM")
    remind_enabled: bool = Field(False, description="是否开启提醒")
    category: str | None = Field(None, max_length=50, description="分类标签")

    # ---------------- 校验器 ----------------
    # @field_validator 表示："在这个字段被赋值之前，先跑一遍我这个小函数"。
    # 校验不通过就 raise ValueError，FastAPI 会自动变成 422 响应并说明原因。

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        """标题不能是空白。

        为什么要单独写？
            min_length=1 能挡住空字符串，但挡不住 "   "（三个空格）。
            这种"看起来有值其实是空的"输入，必须自己判一下。
        """
        value = (value or "").strip()
        if not value:
            raise ValueError("标题不能为空，也不能只有空格")
        return value

    @field_validator("due_at", "remind_at")
    @classmethod
    def _check_time_format(cls, value):
        r"""把时间"翻译"成规范格式 YYYY-MM-DD HH:MM。

        ★★【v0.4.3：从"只认死格式"改成"看得懂人话"】

            用户原话：「放宽日期格式，如：中英文皆可，
                      以用户当前系统时间为准，允许输入"某某分钟后"提醒」

            以前这里只认 @@BT@@YYYY-MM-DD HH:MM@@BT@@，别的一律拒绝 ——
            用户写 "30分钟后" 或 "9月16日" 就会整条待办都存不进去。
            现在交给 timeutil.parse_flexible() 宽容解析，
            **存进数据库的永远是规范格式**（决策 3B 的"字符串排序 == 时间排序"不受影响）。

            解析不了才报错，而且报的是**中文的、带例子**的提示。

        空字符串统一转成 None，避免数据库里出现 "" 和 NULL 两种"空"。
        """
        if value is None or str(value).strip() == "":
            return None
        text = str(value).strip()
        canonical = timeutil.parse_flexible(text)
        if canonical is None:
            raise ValueError("看不懂这个时间：%s。%s" % (text, timeutil.describe_accepts()))
        return canonical


class TaskUpdate(BaseModel):
    """【修改待办】接口接收的数据。

    【和 TaskCreate 的三个区别】
      1. 所有字段都是可选的（None）—— 用户只想改标题，就只传标题；
      2. 允许把字段显式设为 null —— 表示"清空这个字段"（比如取消提醒时间）；
      3. 多了一个 status 字段 —— 用来标记完成 / 取消完成。

    【这里有一个 REST API 的经典难题，务必理解】
        "用户没传 description"  和  "用户想清空 description"
        这两种情况，从 JSON 上看可能都是没有值。怎么区分？

        答案：用 model_dump(exclude_unset=True)（见 routers/tasks.py）
              - 字段【没出现过】  -> 不在结果里         -> 不改
              - 字段【出现过且为 null】-> 在结果里且为 None -> 清空
        这是 Pydantic 提供的标准解法。
    """

    title: str | None = Field(None, min_length=1, max_length=config.TITLE_MAX_LENGTH)
    description: str | None = Field(None, max_length=config.DESCRIPTION_MAX_LENGTH)
    status: str | None = Field(None, description="todo 未完成 / done 已完成")
    priority: int | None = Field(None, ge=0, le=2)
    due_at: str | None = None
    remind_at: str | None = None
    remind_enabled: bool | None = None
    category: str | None = Field(None, max_length=50)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value):
        # 注意：None 要放行 —— 因为"没传 title"和"传了空 title"是两回事，
        # 前者表示不改标题，不能报错。
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("标题不能为空，也不能只有空格")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value):
        if value is None:
            return None
        if value not in config.STATUS_NAMES:
            raise ValueError("状态只能是 todo（未完成）或 done（已完成）")
        return value

    @field_validator("due_at", "remind_at")
    @classmethod
    def _check_time_format(cls, value):
        """同 TaskCreate：接受"人话"时间，统一转成规范格式（v0.4.3）。"""
        if value is None or str(value).strip() == "":
            return None
        text = str(value).strip()
        canonical = timeutil.parse_flexible(text)
        if canonical is None:
            raise ValueError("看不懂这个时间：%s。%s" % (text, timeutil.describe_accepts()))
        return canonical


class TaskOut(BaseModel):
    """【返回给前端】的待办数据。

    model_config = ConfigDict(from_attributes=True) 的作用：
        允许 Pydantic 直接从"对象属性"读取数据（而不只是从字典）。
        这样即使以后直接传数据库对象进来，也能正确转换。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None = None
    status: str
    priority: int
    due_at: str | None = None
    remind_at: str | None = None
    remind_enabled: bool = False
    reminded_at: str | None = None
    category: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
    # 回收站相关：NULL 表示正常任务；有值表示"什么时候被删的"。
    # 接口把它返回出去，前端回收站页面才能显示「删除于 09-13 14:20」。
    deleted_at: str | None = None
    user_id: int = 1


class TrashOut(BaseModel):
    """【回收站列表】的响应。

    为什么不做成直接返回一个数组？
        因为回收站除了列表本身，还要告诉前端"一共有几条"（用来显示角标）。
        包一层对象，以后想再加"多少天后自动清理"这类字段也不用改接口形状。
    """

    items: list[TaskOut]
    count: int


class TaskStats(BaseModel):
    """列表页顶部的统计数字。"""

    total: int
    todo: int
    done: int


class AutostartRequest(BaseModel):
    """开机自启的设置请求。"""

    enabled: bool = Field(..., description="true 开启，false 关闭")
    open_browser: bool = Field(
        False,
        description="开机启动时是否自动打开浏览器。默认 false（静默启动，不打扰）",
    )
    floating: bool = Field(
        False,
        description="开机启动时是否顺便显示桌面悬浮窗（v0.3.0 新增）。"
                    "开启后启动脚本会带上 --floating 参数",
    )


class PreferenceRequest(BaseModel):
    """通用偏好设置读写。

    值统一用【字符串】传递，具体允许哪些取值由路由层的白名单决定：
        notify_sound_enabled -> "1" / "0"
        notify_app_id_mode   -> "system" / "own"
    """

    key: str = Field(..., description="设置名")
    value: str = Field(..., description="设置值（字符串）")


class OpenFolderRequest(BaseModel):
    """"在资源管理器里打开某个目录"的请求。"""

    target: str = Field(
        ...,
        description="要打开哪个目录：project 项目根 / data 数据 / docs 文档 / exports 导出 / startup 启动文件夹",
    )


class FloatingConfigIn(BaseModel):
    """【修改悬浮窗设置】接口接收的数据（v0.3.0 新增）。

    【关键设计：所有字段都可选，只改你传过的那几个】

        悬浮窗上"拖动一次"只该更新 x / y，
        "换个字号"只该更新 font_size —— 绝不能顺手把其它设置重置回默认值。
        这是靠路由层的 model_dump(exclude_unset=True) 实现的：
            没出现在 JSON 里的字段 -> 不在结果里 -> 不改
        和 TaskUpdate 是同一套办法（REST API 的经典难题，见上面那段注释）。

    【为什么把范围限制写在 Field(ge=..., le=...) 里？】
        因为默认值来自 config 常量，写在这里就永远不会和"服务端校验"不一致；
        真要越界，Pydantic 直接返回 422，前端 api.js 已经会把它翻译成中文。
    """

    enabled: bool | None = Field(None, description="是否开启悬浮窗（总开关）")
    autostart: bool | None = Field(None, description="是否随开机自启一起显示")
    visible: bool | None = Field(None, description="窗口当前是否显示（点关闭只是隐藏）")
    x: int | None = Field(None, description="窗口左边距（像素，可为负 = 副屏）")
    y: int | None = Field(None, description="窗口上边距（像素，可为负 = 副屏）")
    width: int | None = Field(
        None, ge=config.FLOATING_MIN_WIDTH, le=config.FLOATING_MAX_WIDTH,
        description="窗口宽度（像素）",
    )
    height: int | None = Field(
        None, ge=config.FLOATING_MIN_HEIGHT, le=config.FLOATING_MAX_HEIGHT,
        description="窗口高度（像素）",
    )
    opacity: float | None = Field(
        None, ge=config.FLOATING_MIN_OPACITY, le=config.FLOATING_MAX_OPACITY,
        description="整窗透明度：1.0 完全不透明，0.05 几乎全透",
    )
    font_family: str | None = Field(None, max_length=60, description="字体名")
    font_size: int | None = Field(
        None, ge=config.FLOATING_MIN_FONT_SIZE, le=config.FLOATING_MAX_FONT_SIZE,
        description="字号（磅）",
    )
    fg_color: str | None = Field(None, description="文字颜色，十六进制如 #1F2933")
    bg_color: str | None = Field(None, description="背景颜色，十六进制如 #FFFFFF")
    accent_color: str | None = Field(None, description="强调色（标题栏 / 高优先级）")
    always_on_top: bool | None = Field(None, description="是否总在最前面")
    show_completed: bool | None = Field(None, description="是否显示已完成的任务")
    highlight_reminders: bool | None = Field(None, description="是否标出即将提醒的任务")
    group_by: str | None = Field(None, description="分块方式：date / priority / category")
    scope: str | None = Field(None, description="显示范围：active / today / week / all")

    @field_validator("group_by")
    @classmethod
    def _check_group_by(cls, value):
        if value is None:
            return None
        if value not in config.FLOATING_GROUP_BY_CHOICES:
            raise ValueError("分块方式只能是：%s" % " / ".join(
                config.FLOATING_GROUP_BY_CHOICES))
        return value

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value):
        if value is None:
            return None
        if value not in config.FLOATING_SCOPE_CHOICES:
            raise ValueError("显示范围只能是：%s" % " / ".join(
                config.FLOATING_SCOPE_CHOICES))
        return value

    @field_validator("fg_color", "bg_color", "accent_color")
    @classmethod
    def _check_color(cls, value):
        """颜色必须写成 #RRGGBB。

        【为什么这里要严格校验？】
            这个值会被【原样】写进 tkinter 的参数。如果传进来一个乱码，
            悬浮窗进程会在创建控件时抛异常 —— 那是个独立进程，
            用户只会看到"窗口打不开了"，却看不到任何原因。
            在接口这一层挡住，用户就能立刻在设置页看到"颜色格式不对"。
        """
        if value is None:
            return None
        text = str(value).strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
            raise ValueError("颜色必须写成 #RRGGBB，例如 #FFFFFF")
        return text.lower()

    @field_validator("font_family")
    @classmethod
    def _check_font(cls, value):
        """字体名不能为空，也不能带引号。

        tkinter 的字体名是直接拼进 Tcl 命令的：
        带引号或大括号的字符串会被 Tcl 当成语法而不是名字，导致窗口创建失败。
        所以这里只放行"字母 / 数字 / 空格 / 常见符号"。
        （和"参数化 SQL"是同一个思路：不要让用户输入变成代码。）
        """
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError("字体名不能为空")
        if not re.fullmatch(r"[A-Za-z0-9 \u4e00-\u9fa5._@+\-]{1,60}", text):
            raise ValueError("字体名里有不支持的字符（不要加引号或大括号）")
        return text


class MessageOut(BaseModel):
    """通用消息响应（用于删除等不需要返回数据的接口）。"""

    ok: bool = True
    message: str = ""
