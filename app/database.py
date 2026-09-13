# -*- coding: utf-8 -*-
"""
database.py -- 数据库连接与会话管理。

【这一层负责什么】
    1. 创建数据库引擎（engine）—— 相当于"数据库连接池"
    2. 提供会话（session）—— 相当于"一次数据库操作的工作台"
    3. Base 基类 —— 所有数据表模型都要继承它
    4. init_db() —— 第一次运行时自动建表

【这一层不负责什么】
    不写任何业务规则。业务规则属于 services 层。
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import config

# --------------------------------------------------------------------------
# 1) 数据库连接地址
#    sqlite:///  后面跟文件路径。
#    as_posix() 把 Windows 的反斜杠转成正斜杠，
#    避免 SQLAlchemy 把路径里的反斜杠（如 \d、\t）当成转义字符。
# --------------------------------------------------------------------------
DATABASE_URL = "sqlite:///" + config.DB_PATH.as_posix()

# --------------------------------------------------------------------------
# 2) 引擎（engine）
#    echo=False        ：不打印每条 SQL（调试时可临时改成 True，很有用）
#    check_same_thread ：默认 SQLite 禁止跨线程用同一个连接。
#                        但本项目里"网页请求"和"后台提醒线程"都要访问数据库，
#                        所以必须关掉这个限制。
# --------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _on_connect(dbapi_connection, connection_record):
    """
    每次建立数据库连接时执行一次，用来打开两个重要开关。

    PRAGMA journal_mode=WAL
        WAL = Write-Ahead Logging（预写日志）。
        默认模式下，一个人写数据库时别人不能读，会报 "database is locked"。
        本项目第 9 步会有"后台提醒线程"不断读取数据库，
        同时你在网页上不断地写 —— 不开 WAL 必然冲突。
        （副作用：data/ 目录下会多出 todo.db-wal 和 todo.db-shm 两个文件，这是正常的。）

    PRAGMA foreign_keys=ON
        SQLite 默认【不】检查外键约束，必须手动打开。
        现在只有一张表用不上，但以后加 users 表时要靠它保证数据不出错。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# --------------------------------------------------------------------------
# 3) Base 基类
#    所有数据表模型都继承它。SQLAlchemy 靠它收集"这个项目有哪些表"。
# --------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# 4) 会话工厂
#    expire_on_commit=False 很关键：
#        默认情况下，commit() 之后对象里的数据会被清空，你再去读就会
#        触发一次新的查询（而此时 session 可能已经关了，直接报错）。
#        关掉它，commit 之后对象依然能正常读取字段。
# --------------------------------------------------------------------------
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def get_session():
    """
    会话上下文管理器，用法：

        with get_session() as session:
            session.add(obj)
        # 离开 with 块时自动 commit；出异常则自动 rollback

    为什么要用 with？
        手写 session = SessionLocal() ... session.close() 很容易漏掉 close，
        连接池被占满后程序就会卡死。with 保证无论如何都会关闭。
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()          # 一切正常 -> 提交
    except Exception:
        session.rollback()        # 出错了   -> 回滚，保证数据不会写一半
        raise
    finally:
        session.close()           # 无论如何 -> 关闭


# --------------------------------------------------------------------------
# 轻量迁移（schema migration）
#
# 【为什么需要它？—— 一个非常容易踩的坑】
#     Base.metadata.create_all() 只会创建【还不存在的表】，
#     它【不会】给已经存在的表添加新列！
#
#     举例：你已经在用这个软件了，data/todo.db 里的 tasks 表有 14 列。
#     现在升级版本，代码里加了 deleted_at（回收站功能）。
#     启动后 create_all 一看："tasks 表已经存在，跳过" —— 新列根本不会加上去。
#     于是所有查询都报 "no such column: tasks.deleted_at"，软件直接打不开。
#
# 【怎么解决？】
#     SQLite 支持 ALTER TABLE ... ADD COLUMN，我们就手动补上缺失的列。
#     新装的用户走 create_all（一次性建好全部列），
#     老用户走这里的 ALTER（只补差的那几列）—— 两条路殊途同归。
#
# 【为什么不用 Alembic 这类专业迁移工具？】
#     它们功能强大（支持回滚、复杂变更），但会引入一个新依赖 + 一堆配置文件。
#     本项目只需要"加一列"这种最简单的操作，用 20 行代码就能安全搞定。
#     —— 工具要匹配问题的复杂度，不要为了显得专业而上重武器。
#
# 【注意】这里只做"加列"，不做"改列类型""删列"。
#     那两类操作有丢数据的风险，必须人工确认后手工处理。
# --------------------------------------------------------------------------
MIGRATIONS = [
    # (列名, 建列语句)
    (
        "deleted_at",
        "ALTER TABLE tasks ADD COLUMN deleted_at VARCHAR(16)",
    ),
]


def migrate_schema() -> list:
    """
    给【已经存在】的数据库补上后加的列。返回本次实际新增的列名列表。

    新建的数据库已经有全部列了，这个函数会什么都不做（返回空列表）。
    所以每次启动无脑调用它是安全的。
    """
    from sqlalchemy import inspect, text

    added = []
    try:
        inspector = inspect(engine)
        if "tasks" not in inspector.get_table_names():
            return added          # 表都还不存在，交给 create_all 去建

        existing = {col["name"] for col in inspector.get_columns("tasks")}
        for column, ddl in MIGRATIONS:
            if column in existing:
                continue          # 已经有了，跳过（保证可以反复调用）
            with engine.begin() as conn:
                conn.execute(text(ddl))
            added.append(column)
    except Exception as exc:

        # 迁移失败必须【响亮地】报出来，绝不能静默跳过 ——
        # 否则程序会带着"缺列"的数据库继续跑，然后到处报奇怪的错误。
        raise RuntimeError("数据库升级失败，程序无法继续：%s" % exc) from exc

    return added


def init_db():
    """
    建表 + 补列，让数据库结构跟上代码。

    第一次运行程序时，data/todo.db 会被自动创建，里面建好 tasks / settings 两张表。
    已经存在的表不会被改动，也不会丢数据——所以每次启动都可以放心调用。

      【这两步的顺序不能反】
        1) create_all()  —— 先把"不存在的表"建出来
        2) migrate()     —— 再给"已存在的表"补上后加的列
     对全新数据库：第 1 步建出完整的表，第 2 步什么都不做。
     对老数据库  ：第 1 步什么都不做，第 2 步补上缺的列。

    【为什么 import 写在函数里面？】
        因为 models.py 需要从本文件 import Base，
        如果本文件在顶部 import models，就会形成"互相 import"的死循环。
        把 import 放进函数内部，执行到这里时才导入，循环就解开了。
    """
    from app import models  # noqa: F401  —— 必须导入，Base 才知道有哪些表

    Base.metadata.create_all(bind=engine)
    migrate_schema()


def checkpoint_wal():
    """
    把 WAL 日志里的内容合并回主数据库文件，并清空 WAL。

    【为什么需要它？】
        我们用了 WAL 模式（见 _on_connect），所有写操作先写进 todo.db-wal，
        之后才慢慢合并回 todo.db。

        SQLite 会在合适的时候自动做这件事，但如果我们【强制结束进程】
        （关掉黑窗口、任务管理器结束进程），自动合并就来不及做，
        todo.db-wal 会一直变大（实测能涨到 1MB 以上）。

        它不会导致数据丢失（下次打开时 SQLite 会自动恢复），
        但会让 data/ 目录里躺着一个大文件，看着让人不放心。

        所以：程序正常关闭时，主动做一次合并。

    TRUNCATE 表示"合并完把 WAL 文件清空"，比默认的 PASSIVE 更彻底。
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    except Exception:
        # 合并失败不影响程序退出，数据也不会丢（下次打开会自动恢复）
        pass


def db_status() -> dict:
    """
    返回数据库的当前状态（给 /api/health 接口和设置页用）。

    这里故意用 try 包住：万一数据库文件坏了，程序也不该整个崩溃，
    而是把这个错误如实报告出来。
    """
    from sqlalchemy import inspect, text

    info = {
        "url": DATABASE_URL,
        "path": str(config.DB_PATH),
        "exists": config.DB_PATH.exists(),
        "size_kb": round(config.DB_PATH.stat().st_size / 1024, 1) if config.DB_PATH.exists() else 0,
        "tables": [],
        "task_count": 0,
        "error": None,
    }
    try:
        inspector = inspect(engine)
        info["tables"] = sorted(inspector.get_table_names())
        with engine.connect() as conn:
            info["task_count"] = conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar() or 0
    except Exception as exc:
        info["error"] = str(exc)
    return info
