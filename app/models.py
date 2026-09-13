# -*- coding: utf-8 -*-
"""
models.py -- 数据表结构定义（SQLAlchemy 模型）。

【models 和 schemas 的区别】（初学者最容易搞混的地方）
    models.py  ：描述"数据库里的表长什么样"
    schemas.py ：描述"接口收发的 JSON 长什么样"（第 3 步才建）

    为什么要分成两个？
        比如"新建任务"时，用户只需要填标题和提醒时间，
        但数据库里还有 id、created_at、updated_at 这些字段。
        如果共用一个类，用户就能伪造 id 和创建时间，数据会乱。
        分开写，接口层就不会被数据库内部字段污染。

【时间字段为什么是 String 而不是 DateTime？】
    因为决策 3B 选了"纯本地时间字符串"：
        肉眼可读、字符串排序即时间排序、导出的 JSON 直接能看懂。
    所有转换都走 app/core/timeutil.py，别处不自己做转换。
"""

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    """待办事项表。"""

    __tablename__ = "tasks"

    # ---- 主键 ----
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ---- 内容 ----
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 状态 ----
    # status   : "todo" 未完成 / "done" 已完成
    # priority : 0 低 / 1 中 / 2 高
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="todo")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ---- 时间（格式统一为 "YYYY-MM-DD HH:MM"，所以长度 16 就够） ----
    due_at: Mapped[str | None] = mapped_column(String(16), nullable=True)         # 截止时间
    remind_at: Mapped[str | None] = mapped_column(String(16), nullable=True)      # 提醒时间
    remind_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 是否开启提醒 0/1
    reminded_at: Mapped[str | None] = mapped_column(String(16), nullable=True)    # 已提醒时刻（防重复弹窗）
    created_at: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(16), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ---- 回收站（软删除） ----
    # 【设计决定】删除 = 打个时间戳标记，而不是真的从数据库里抹掉。
    #   NULL  -> 正常任务，会出现在列表和日历里
    #   有值  -> 已进回收站，默认不出现在任何查询里

    # 【为什么用时间戳而不是 is_deleted 布尔值？】
    #   布尔值只能回答"删没删"，时间戳还能回答"什么时候删的"。
    #   回收站按删除时间倒序排列（最近删的排最前）正好要用到它，
    #   将来想加"30 天后自动清理"也不用再改表结构。
    #   —— 多花 0 字节（反正这一列总要存在），换来一个额外的信息维度。

    # 【为什么不是布尔值 + 另一个时间列？】
    #   那样会有"is_deleted=1 但 deleted_at 为空"这种自相矛盾的状态，
    #   一列能表达清楚的事就不要用两列，少一个不一致的可能。
    deleted_at: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ---- 分类与多用户预留 ----
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)      # 决策 4A：现在恒为 1

    # ---- 索引 ----
    # 索引的作用：让"按条件查找"变快。
    # 不建索引时，数据库要一行一行翻完整张表（数据一多就明显卡顿）。
    __table_args__ = (
        Index("idx_tasks_status", "status"),
        # 提醒扫描每 20 秒执行一次，查询条件是 remind_at + reminded_at，必须有索引
        Index("idx_tasks_remind", "remind_at", "reminded_at"),
        # 日历视图要按"用户 + 日期范围"查询
        Index("idx_tasks_user_due", "user_id", "due_at"),
        # 回收站：几乎每条查询都带 "deleted_at IS NULL" 这个条件，
        # 所以它必须进索引，否则每次查询都要全表扫描。
        Index("idx_tasks_deleted", "user_id", "deleted_at"),
    )

    def to_dict(self) -> dict:
        """
        把数据库对象转成普通字典，方便接口直接返回 JSON。

        为什么要手动写而不是自动转换？
            自动转换会把数据库内部字段全部暴露出去。
            手写虽然多几行，但"接口到底返回什么"一目了然，也更安全。
        """
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "due_at": self.due_at,
            "remind_at": self.remind_at,
            "remind_enabled": bool(self.remind_enabled),
            "reminded_at": self.reminded_at,
            "category": self.category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "deleted_at": self.deleted_at,
            "user_id": self.user_id,
        }

    @property
    def is_deleted(self) -> bool:
        """是否在回收站里。给"这个对象现在能不能被编辑"这种判断用。"""
        return self.deleted_at is not None

    def __repr__(self) -> str:
        """调试时打印对象用，例如 <Task id=3 title='写作业'>"""
        return "<Task id=%s title=%r status=%s>" % (self.id, self.title, self.status)


class Setting(Base):
    """
    设置表（键值对结构）。

    为什么用"键值对"而不是给每个设置建一列？
        以后要加"主题颜色""默认提醒提前量"这些新设置时，
        不需要修改表结构（改表结构是有风险的操作），
        只要往这张表里加一行就行 —— 扩展性最好。
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(String(16), nullable=False)

    def __repr__(self) -> str:
        return "<Setting %s=%r>" % (self.key, self.value)
