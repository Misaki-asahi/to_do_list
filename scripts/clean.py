# -*- coding: utf-8 -*-
"""
clean.py -- 清理项目里所有"自动生成、随时可以删除"的文件。

用法（在项目根目录执行）：
    python scripts/clean.py            # 清理安全项（推荐，随时可跑）
    python scripts/clean.py --dry-run  # 只看会删什么，不真删
    python scripts/clean.py --reset-db # 危险：连真实待办数据库一起删掉

【会清理什么】
    1. __pycache__ 目录        Python 自动生成的字节码缓存
    2. .pytest_cache           测试框架缓存
    3. data/test/ 里的内容     自检脚本用的临时数据库

【【不会】清理什么，以及为什么】
    data/todo.db          <- 你的真实待办数据！除非加 --reset-db，绝不动它
    data/todo.db-wal      <- SQLite 的预写日志，是数据库【正在使用的一部分】，
    data/todo.db-shm         删掉可能丢失尚未落盘的数据，所以不碰
    docs/ 和 app/ 下的源文件   <- 那是项目本身，不是生成物
"""

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# (路径, 说明)，路径可以是目录或文件
SAFE_TARGETS = [
    (PROJECT_ROOT / "data" / "test", "测试用临时数据库目录"),
    (PROJECT_ROOT / ".pytest_cache", "pytest 测试缓存"),
]


def human(size_bytes: int) -> str:
    """把字节数变成人类好读的形式。"""
    if size_bytes < 1024:
        return "%d B" % size_bytes
    if size_bytes < 1024 * 1024:
        return "%.1f KB" % (size_bytes / 1024)
    return "%.2f MB" % (size_bytes / 1024 / 1024)


def dir_size(path: Path) -> int:
    """算出一个目录（或文件）占多少字节。"""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def find_pycache() -> list:
    """找出项目里所有的 __pycache__ 目录（Python 自动生成的字节码缓存）。"""
    return [p for p in PROJECT_ROOT.rglob("__pycache__") if p.is_dir()]


def remove(path: Path, dry_run: bool) -> int:
    """删除一个文件或目录，返回释放的字节数。"""
    if not path.exists():
        return 0
    size = dir_size(path)
    if dry_run:
        return size
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            return 0
    return size


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    reset_db = "--reset-db" in args

    print("=" * 66)
    print("  项目清理工具" + ("（预览模式，不会真的删除）" if dry_run else ""))
    print("=" * 66)
    print("项目目录: %s" % PROJECT_ROOT)
    print()

    freed = 0
    counts = []

    # ---- 1) 所有 __pycache__ ----
    caches = find_pycache()
    cache_size = sum(dir_size(p) for p in caches)
    if not dry_run:
        for p in caches:
            shutil.rmtree(p, ignore_errors=True)
    freed += cache_size
    counts.append(("__pycache__ 目录", len(caches), cache_size))

    # ---- 2) 其它安全项 ----
    for path, desc in SAFE_TARGETS:
        exists = path.exists()
        size = remove(path, dry_run)
        freed += size
        counts.append((desc, 1 if exists else 0, size))

    # ---- 3) 可选：重置真实数据库 ----
    if reset_db:
        db = PROJECT_ROOT / "data" / "todo.db"
        if db.exists():
            print("!! 警告：即将删除真实待办数据库 %s" % db)
            size = remove(db, dry_run)
            freed += size
            counts.append(("真实数据库（已重置）", 1, size))
            # WAL / SHM 是主库的附属文件，主库删了它们也就没有意义了
            for suffix in ("-wal", "-shm"):
                p = Path(str(db) + suffix)
                if p.exists():
                    freed += remove(p, dry_run)
        else:
            print("（真实数据库不存在，跳过）")

    # ---- 汇总 ----
    print("%-28s %-8s %s" % ("清理项", "数量", "大小"))
    print("-" * 66)
    for desc, n, size in counts:
        if n == 0:
            continue
        print("%-28s %-8d %s" % (desc, n, human(size)))
    print("-" * 66)
    print("合计释放空间: %s" % human(freed))
    print()

    if dry_run:
        print("这只是预览。真要清理，请执行：python scripts/clean.py")
    else:
        print("清理完成。你的 data/todo.db（真实待办）完全没有被动过。")
        print()
        print("下一步：数据还在吗？运行  python scripts/demo_db.py --list  看看。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
