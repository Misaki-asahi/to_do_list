# -*- coding: utf-8 -*-
"""
main.py -- 应用装配中心（FastAPI 实例）。

这一层只做三件事，绝不写业务逻辑：
    1. 创建 FastAPI 应用对象
    2. 挂载静态资源目录（CSS / JS / 图片）
    3. 注册页面路由和接口路由

以后的业务接口（/api/tasks 等）会写在 app/routers/ 里，
然后在这里用 app.include_router(...) 挂上来。
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, database
from app.core import backup as backup_core
from app.core import errors as app_errors
from app.core import logging_setup, notifier, scheduler
from app.routers import calendar as calendar_router
from app.routers import reminders as reminders_router
from app.routers import settings as settings_router
from app.routers import tasks as tasks_router

# ---------------------------------------------------------------------------
# 日志：尽早初始化，后面的所有日志都靠它
#
# 【为什么放在模块最上面？】
#   如果放在启动钩子里，那么"导入模块时"发生的错误就没人记录 ——
#   而那恰恰是最容易出错的地方（比如某个模块写错了）。
# ---------------------------------------------------------------------------
logging_setup.setup()
logging_setup.install_exception_hooks()
logger = logging_setup.get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用的生命周期钩子：服务"启动时"和"关闭时"各执行一次。

    为什么要有它？
        以前的做法是在每个用到数据库的地方都调用一次 init_db()，
        既啰嗦又容易漏。放在这里，服务一启动就自动准备好：
            1. 确保 data/ 目录存在（别人第一次下载运行时可能没有）
            2. 建好所有还不存在的表
        这样"第一次运行就能用"，不给使用者制造麻烦。
    """
    config.ensure_dirs()
    database.init_db()

    # 启动时自动备份一次（距上次不足 1 小时则跳过）
    # 这是被一次真实的数据丢失事故"逼"出来的功能，见 BUG-011
    result = backup_core.backup_on_startup()
    if result.get("ok"):
        print("[备份] " + result.get("message", ""))

    # 准备通知的应用身份（注册 + 判断能否使用自有身份）
    # 为什么要放在启动时做？因为注册后 Windows 需要时间刷新身份缓存，
    # 启动时就注册，等第一条提醒真的到点时身份多半已经被系统认下了。
    try:
        identity = notifier.init()
        print("[通知] %s" % identity.get("message", ""))
    except Exception as exc:                      # noqa: BLE001 - 不能让通知问题挡住启动
        print("[通知] 初始化失败（不影响其它功能）：%s" % exc)

    # 启动后台提醒线程：扫描到点的提醒并发 Windows 系统通知
    scheduler.start()
    print("[提醒] 后台提醒线程已启动（每 %d 秒检查一次）" % config.REMINDER_SCAN_SECONDS)

    yield
    # ---- 以下是服务关闭时执行的代码 ----
    scheduler.stop()
    # ---- yield 之后是"服务关闭时"要执行的代码 ----
    # 把 WAL 日志合并回主数据库并清空它。
    # 不做这件事也不会丢数据（SQLite 下次打开会自动恢复），
    # 但正常退出时合并一下，data/ 目录会更干净。
    database.checkpoint_wal()


# 创建应用对象。title / version / description 会显示在自动生成的接口文档 /docs 上
app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    description="离线待办清单的本地接口。打开 /docs 可以直接在网页上调试每一个接口。",
    lifespan=lifespan,
)


# ===========================================================================
# 请求日志中间件
# ===========================================================================
# 中间件的作用：在"请求进来"和"响应出去"之间插一段自己的代码。
#
# 【为什么要过滤？】
#   一个页面会带出十几个静态资源请求（CSS / JS）。
#   如果全都记下来，日志一天能涨几万行，真正的错误反而被淹没了。
#   所以只记：所有 /api/ 请求，以及任何非 2xx 的响应。
# ===========================================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 【注意】这里【不要】记日志。
        #   异常会继续往上冒，最终由下面的全局异常处理器负责记录。
        #   如果这里也记一次，同一个异常就会在日志里出现两遍，
        #   加上两份完整堆栈 —— 日志会迅速被撑爆，真正的信息反而淹没。
        #   原则：一个异常，只在一处记录。
        raise

    cost = (time.perf_counter() - start) * 1000
    path = request.url.path
    if path.startswith("/api/") or response.status_code >= 400:
        level = logging_setup.get_logger("access").warning if response.status_code >= 400             else logging_setup.get_logger("access").info
        level("%s %s -> %s （%.0f ms）", request.method, path,
              response.status_code, cost)
    return response


# ===========================================================================
# 全局异常处理器
# ===========================================================================
# 【为什么要有它们？】
#   以前每个路由函数里都写着这样一段：
#
#       try:
#           task = task_service.create_task(payload)
#       except task_service.BusinessError as exc:
#           raise HTTPException(status_code=400, detail=str(exc))
#
#   4 个路由文件里重复了十几遍 —— 而且每加一个接口就要再抄一遍，
#   漏写一个的话，那个异常就会变成 500（用户看到"服务器内部错误"）。
#
#   现在改成在这里统一处理：**路由函数里直接调用服务层，什么都不用包**。
#   异常自己会冒上来，这里负责翻译成合适的 HTTP 响应 + 记录日志。
# ===========================================================================

@app.exception_handler(app_errors.AppError)
async def handle_app_error(request: Request, exc: app_errors.AppError):
    """处理所有自定义异常（业务规则 / 找不到 / 取值不合法）。"""
    # 4xx 是用户的问题，记 WARNING 就够；不用 ERROR，避免刷屏
    logging_setup.get_logger("api").warning(
        "%s %s -> %s（%s）", request.method, request.url.path,
        exc.status_code, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "type": exc.error_type, "extra": exc.extra or None},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    """
    处理 Pydantic 的格式校验错误（默认是 422，但返回结构对前端不太友好）。

    顺手把它整理成和上面一致的格式，前端就不用分辨两种错误结构了。
    """
    details = []
    for item in exc.errors():
        loc = [str(x) for x in item.get("loc", []) if x not in ("body", "query", "path")]
        details.append({"field": ".".join(loc) or "参数", "message": item.get("msg", "")})
    return JSONResponse(
        status_code=422,
        content={"detail": "提交的数据格式不正确", "type": "validation_error",
                 "errors": details},
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    """
    兜底：任何没被上面接住的异常。

    **关键点：一定要把完整堆栈写进日志。**
    对用户只说"服务器内部错误"，因为异常细节可能包含路径、SQL 等敏感信息；
    但对你（开发者）来说，日志里有完整的现场。
    """
    logger.exception("未处理的异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误。详细信息已记录到日志文件，"
                      "可以在设置页点「查看日志」。",
            "type": "internal_error",
        },
    )

# --------------------------------------------------------------------------
# 静态资源
#   StaticFiles 会把 STATIC_DIR 这个文件夹"暴露"到网址 /static 下面。
#   例如文件 app/static/css/style.css  ->  网址 /static/css/style.css
#   HTML 里写 href="/static/css/style.css" 就能加载到它。
# --------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

# --------------------------------------------------------------------------
# 业务接口路由
#   include_router 把 routers/tasks.py 里定义的接口挂到应用上。
#   以后加新模块（提醒、日历、设置）就在这里再加一行。
# --------------------------------------------------------------------------
app.include_router(tasks_router.router)
app.include_router(reminders_router.router)
app.include_router(calendar_router.router)
app.include_router(settings_router.router)


def _page(filename: str) -> FileResponse:
    """
    返回 templates 目录下的某个 HTML 页面。

    FileResponse 会自己处理"文件不存在就报 404"，
    比手写读文件 + 设置响应头省事得多。
    """
    return FileResponse(config.TEMPLATE_DIR / filename, media_type="text/html")


# --------------------------------------------------------------------------
# 页面路由
#   include_in_schema=False：这些是给人看的网页，不用出现在接口文档里
# --------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def page_todo_list():
    """主界面：待办列表。"""
    return _page("index.html")


@app.get("/calendar", include_in_schema=False)
def page_calendar():
    """日历界面：点未来某一天直接建任务（第二阶段第 10 步实现）。"""
    return _page("calendar.html")


@app.get("/trash", include_in_schema=False)
def page_trash():
    """回收站：找回误删的任务，或永久清掉。"""
    return _page("trash.html")


@app.get("/settings", include_in_schema=False)
def page_settings():
    """设置界面：开机自启、数据备份、关于（第二阶段第 11 步实现）。"""
    return _page("settings.html")


# --------------------------------------------------------------------------
# 接口
# --------------------------------------------------------------------------
@app.get("/api/health", tags=["系统"], summary="健康检查")
def health():
    """
    健康检查接口。

    它的价值：
        1. 第一步用来验证"前端能不能调通后端"；
        2. 以后部署到公网，平台靠它判断服务是不是还活着；
        3. 程序出问题时，先访问这个接口，能快速区分"服务挂了"还是"页面写错了"。
    """
    return {
        "status": "ok",
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "db_path": str(config.DB_PATH),
        # 数据库的实时状态（第 2 步新增）
        # 前端首页靠它来判断"数据库这一项"是绿点还是红点
        "db": database.db_status(),
    }
