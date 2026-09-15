# -*- coding: utf-8 -*-
"""
setting_repo.py -- 设置表的数据访问层。

设置就是一堆"键 -> 值"，例如 autostart_enabled -> "1"。
值统一以字符串保存，读取时由调用方决定怎么理解（转成 bool / int）。
"""

from sqlalchemy import Integer, cast, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core import timeutil
from app.database import get_session
from app.models import Setting


def get(key: str, default: str = None):
    """读取一个设置，不存在就返回默认值。"""
    with get_session() as session:
        obj = session.get(Setting, key)
        return default if obj is None else obj.value


def get_bool(key: str, default: bool = False) -> bool:
    """读取一个开关型设置。"""
    raw = get(key, None)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def set_value(key: str, value):
    r"""
    写入一个设置（存在就更新，不存在就新建）。

    ★★【为什么不用"先查再插"？（BUG-035 的教训）】

        原来的写法是：
            obj = session.get(Setting, key)      # 查
            if obj is None: session.add(...)     # 再插
            else: obj.value = text

        这在【单线程】下完全正确，但只要有第二个写者就会出事：
        两个线程同时查到"这个键不存在"，于是都去 INSERT，
        第二个撞上 settings.key 的 UNIQUE 约束 ——
        抛 IntegrityError，接口返回 500。

        本项目真实发生过：20 个并发的「立即显示」请求里有 1 个返回 500；
        3 个并发就稳定复现。第一次生成悬浮窗令牌（floating_token）也是同一条路径。

    ★ 现在的写法是【一条 SQL 完成的 upsert】（SQLite 的 INSERT ... ON CONFLICT DO UPDATE）：
        要么插入，要么更新，判断和写入在同一条语句里 ——
        中间没有可以被别人插进来的缝隙，这个竞态从结构上消失了。

      这和"参数化查询防注入"是同一个思路：
      **把"判断 + 动作"交给数据库一次做完，而不是自己在 Python 里分两步做。**
    """
    text = "" if value is None else str(value)
    now = timeutil.now_str()
    stmt = sqlite_insert(Setting).values(key=key, value=text, updated_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Setting.key],
        set_={"value": text, "updated_at": now},
    )
    with get_session() as session:
        session.execute(stmt)
    return text


def all_settings() -> dict:
    """一次性读出全部设置，返回普通字典（设置页要用）。"""
    with get_session() as session:
        rows = session.execute(select(Setting)).scalars().all()
        return {row.key: row.value for row in rows}


def delete_key(key: str) -> bool:
    """删除一个设置项，恢复成"默认值"状态。"""
    with get_session() as session:
        obj = session.get(Setting, key)
        if obj is None:
            return False
        session.delete(obj)
        return True
