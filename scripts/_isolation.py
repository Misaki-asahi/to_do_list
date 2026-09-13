# -*- coding: utf-8 -*-
"""
_isolation.py -- 测试隔离工具。

【⚠️ 每个会"写数据 / 删数据"的脚本，都必须在导入 app 之前调用它。】

【为什么需要它？——一个真实的事故】

    开发过程中，我在临时测试脚本里直接操作了【真实数据库】：
        for t in task_repo.list_tasks():
            task_repo.delete(t.id)          # ← 删掉的是用户真实创建的待办
    结果用户手动创建的待办全部丢失，且无法恢复（因为没有备份）。

    详见证「问题记录与解决方案」BUG-011。

【正确做法】

    在脚本的最上面（在 import app 之前）写：

        from scripts._isolation import use_isolated_db
        use_isolated_db("my_test")

    这样脚本操作的会是 data/test/my_test.db，
    无论怎么折腾都不会碰到你的真实数据。

【为什么必须在导入 app 之前？】
    因为 app.config 在【第一次被导入时】就会读取环境变量 TODO_DB 并定下数据库路径。
    导入之后再设就来不及了 —— 这个坑见 BUG-009（校验发生的位置很重要）。
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 真实数据库的相对路径，用来做比对
PRODUCTION_DB = PROJECT_ROOT / "data" / "todo.db"


def use_isolated_db(name: str = "scratch") -> Path:
    """
    把数据库切到 data/test/<name>.db，并返回这个路径。

    在任何 from app import ... 之前调用。
    """
    if "app" in sys.modules or "app.config" in sys.modules:
        raise RuntimeError(
            "太晚了：app 已经被导入，数据库路径已经定下来了。\n"
            "请把 use_isolated_db(...) 放到所有 from app import ... 之前。"
        )

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    test_dir = PROJECT_ROOT / "data" / "test" / name
    test_dir.mkdir(parents=True, exist_ok=True)
    db_path = test_dir / ("%s.db" % name)

    # 同时隔离【整个数据目录】—— 这样测试产生的备份、日志也都进临时目录，
    # 不会往你真实的 data/backups 和 data/logs 里塞东西。
    os.environ["TODO_DATA_DIR"] = str(test_dir)
    os.environ["TODO_DB"] = str(db_path)
    return db_path


def assert_isolated():
    """
    安全检查：确认当前用的【不是】真实数据库。

    在脚本开始真正操作数据之前调用它，可以在"忘了隔离"的情况下立刻中止，
    而不是等数据被删光了才发现。
    """
    from app import config

    if Path(config.DB_PATH).resolve() == PRODUCTION_DB.resolve():
        raise RuntimeError(
            "\n" + "=" * 70 + "\n"
            "  危险：这个脚本正在操作【真实的待办数据库】！\n"
            "  路径：%s\n"
            "\n"
            "  如果是测试脚本，请在文件最上面加上：\n"
            "      from scripts._isolation import use_isolated_db\n"
            "      use_isolated_db(\"脚本名\")\n"
            "  然后再 import app。\n"
            "\n"
            "  参考：docs/问题记录与解决方案.md 的 BUG-011\n"
            + "=" * 70
        )
    return True


def cleanup(db_path: Path):
    """删掉整个隔离目录（数据库、备份、日志一起清掉）。"""
    import shutil
    folder = Path(db_path).parent
    shutil.rmtree(folder, ignore_errors=True)
