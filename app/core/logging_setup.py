# -*- coding: utf-8 -*-
r"""
logging_setup.py -- 日志系统。

【为什么必须有日志？】

    源码运行时，出错了你能在黑色窗口里看到报错。
    但打包成 exe 之后【没有控制台】—— 程序出错时：
        · 用户只看到"页面打不开"或"操作失败"
        · 你什么信息都拿不到，完全无法排查

    所以日志是发布版【唯一】的故障线索。

【记录到哪里】

    data/logs/app.log
        · 按天滚动（每天一个文件，如 app.log.2026-09-13）
        · 保留最近 14 天，自动清理旧的
        · 只记录重要事件，不记录每个静态文件请求（否则一天几万行）

【三个必须抓的异常入口】

    ① 普通代码里没被 try/except 接住的异常  ->  sys.excepthook
    ② 后台线程里没被接住的异常            ->  threading.excepthook
    ③ Web 请求处理中的异常                ->  FastAPI 的全局异常处理器

    其中 ② 最容易被忽略：**后台线程抛异常会安静地死掉**，
    程序表面上一切正常，只是那个功能再也<不工作了。
    —— 这个坑我们在 BUG-012 已经踩过一次。
"""

import logging
import logging.handlers
import sys
import threading
import traceback

from app import config

# 日志格式：时间 / 级别 / 模块 / 消息
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup(level=None) -> logging.Logger:
    r"""
    初始化日志系统（重复调用只会生效一次）。

    返回根日志对象。
    """
    global _configured
    root = logging.getLogger()

    if _configured:
        return root

    root.setLevel(getattr(logging, (level or config.LOG_LEVEL).upper(), logging.INFO))

    # ---- 1) 文件处理器（按天滚动）----
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            str(config.LOG_FILE),
            when="midnight",
            backupCount=config.LOG_KEEP_DAYS,
            encoding="utf-8",
            delay=True,                 # 真的写日志时才创建文件
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
    except OSError as exc:
        # 日志写不了也不能让程序崩 —— 退回控制台
        print("[警告] 无法创建日志文件：%s" % exc)

    # ---- 2) 控制台处理器 ----
    # 打包成 --noconsole 之后 sys.stdout 是 None，加了会报错，所以要先判断
    if config.LOG_TO_CONSOLE and sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
        console.setLevel(logging.INFO)
        root.addHandler(console)

    # ---- 3) 让 uvicorn 的日志也走同一套格式 ----
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # ---- 4) 把几个"话太多"或"重复"的日志调安静 ----
    # httpx 每次请求都会打一行 INFO，测试跑一轮能刷屏几百行
    for name in ("httpx", "httpcore", "urllib3", "asyncio", "multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # uvicorn 自带的访问日志和我们自己的中间件重复（见 app/main.py 的 log_requests），
    # 只保留信息更全的那一份。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _configured = True
    root.info("=" * 66)
    root.info("%s v%s 启动", config.APP_NAME, config.APP_VERSION)
    root.info("数据目录: %s", config.BASE_DIR)
    root.info("打包模式: %s", "是（exe）" if config.FROZEN else "否（源码）")
    root.info("=" * 66)
    return root


def get_logger(name: str) -> logging.Logger:
    """获取一个日志对象。用法：logger = get_logger(__name__)"""
    return logging.getLogger(name)


def install_exception_hooks() -> None:
    r"""
    安装"最后一道网"：捕获所有没被接住的异常。

    三个入口，一个都不能少：
        sys.excepthook        -> 主线程里漏掉的异常
        threading.excepthook  -> 后台线程里漏掉的异常（最容易被忽略！）
        unraisable            -> 析构函数等特殊位置里的异常
    """
    logger = get_logger("uncaught")

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            # Ctrl+C 是正常退出，不算错误
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "主线程出现未捕获的异常：\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    def _thread_excepthook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logger.critical(
            "后台线程 %s 出现未捕获的异常：\n%s",
            args.thread.name if args.thread else "?",
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def log_dir_info() -> dict:
    """给设置页用的日志信息。"""
    files = []
    total = 0
    if config.LOG_DIR.exists():
        for p in sorted(config.LOG_DIR.glob("app.log*"), reverse=True):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append({"name": p.name, "size_kb": round(size / 1024, 1)})
            total += size
    return {
        "dir": str(config.LOG_DIR),
        "file": str(config.LOG_FILE),
        "exists": config.LOG_FILE.exists(),
        "level": config.LOG_LEVEL,
        "keep_days": config.LOG_KEEP_DAYS,
        "file_count": len(files),
        "total_kb": round(total / 1024, 1),
        "files": files[:10],
    }


def tail(lines: int = 80) -> str:
    """读取日志文件最后若干行（给设置页"查看日志"用的）。"""
    if not config.LOG_FILE.exists():
        return "（还没有日志文件）"
    try:
        with open(config.LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except OSError as exc:
        return "（读取日志失败：%s）" % exc
