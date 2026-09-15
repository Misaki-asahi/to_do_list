# -*- coding: utf-8 -*-
"""
settings.py -- 设置页的 API 路由（模块 M7）。

包含：
    GET  /api/settings              一次性拿到设置页要的全部信息
    POST /api/settings/autostart    开启 / 关闭开机自启
    POST /api/settings/open-folder  在资源管理器里打开某个目录
"""

import os
import threading
import time

from fastapi import APIRouter

from app import database
from app.core import backup as backup_core
from app.core import errors as app_errors
from app.core import logging_setup
from app.repositories import setting_repo
from app.schemas import (AutostartRequest, MessageOut, OpenFolderRequest,
                         PreferenceRequest)
from app.services import setting_service

from app.core import logging_setup

logger = logging_setup.get_logger("app.routers.settings")

# 允许通过接口修改的偏好项白名单，以及每一项的合法取值。
# 【为什么要白名单？】这个接口能往设置表里写东西，
# 不限制的话就能写入任意键、任意值，把配置搞乱。只放开确实需要的那几个。
EDITABLE_PREFERENCES = {
    "notify_sound_enabled": {"0", "1"},
    "notify_app_id_mode": {"auto", "system", "own"},
    # v0.3.0：用户在设置页里表达"我点击开启悬浮窗时，要不要顺便也让它开机自启"。
    # 它本身不会去动启动文件夹（那是 POST /api/settings/autostart 的职责），
    # 只是记住一个偏好，供悬浮窗的守护线程读。
    "autostart_floating": {"0", "1"},
}

router = APIRouter(prefix="/api/settings", tags=["设置"])


@router.get(
    "",
    summary="获取全部设置与系统状态",
)
def get_settings():
    """
    设置页一打开就调用它，一次性拿全：
        开机自启状态 / 数据统计 / 关于信息 / 已保存的用户偏好
    """
    return setting_service.full_settings()


@router.post(
    "/autostart",
    summary="开启或关闭开机自启",
)
def set_autostart(payload: AutostartRequest):
    """
    写 / 删 Windows 启动文件夹里的启动脚本。

    操作系统的动作可能失败（权限不足、文件被占用等），
    所以这里不用 HTTP 状态码表示失败，而是统一返回 200 + {ok: false, message: ...}，
    让前端把失败原因显示给用户 —— 这比抛一个 500 更友好。
    """
    return setting_service.set_autostart(payload.enabled, payload.open_browser,
                                        payload.floating)


@router.post(
    "/shutdown",
    summary="关闭程序",
)
def shutdown():
    """
    退出整个程序。

    【为什么需要这个接口？】
        打包成 exe 之后，程序运行时【没有黑色命令行窗口】——
        用户点右上角的 × 关不掉它，只能用任务管理器。

        所以设置页提供了一个"退出程序"按钮，让用户能正常关闭。
        （源码运行时用 Ctrl+C 或关窗口就行，这个按钮同样可用。）

    【为什么要延迟一下再退出？】
        得先把这个 HTTP 响应发回给浏览器，页面才能显示"已退出"。
        立刻 os._exit() 的话，浏览器会收到一个连接中断的错误。
    """
    def _exit_soon():
        time.sleep(0.8)                    # 留时间让响应发出去
        try:
            database.checkpoint_wal()      # 退出前把 WAL 日志合并回主库
        except Exception as exc:                    # noqa: BLE001
            # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
            logger.debug("这里出错不影响主流程，按可忽略处理：%s", exc, exc_info=True)

        os._exit(0)                        # 直接退出进程（后台提醒线程也会一起结束）

    threading.Thread(target=_exit_soon, daemon=True).start()
    return {"ok": True, "message": "程序即将退出，可以关闭这个页面了"}


@router.get(
    "/logs",
    summary="查看最近的日志内容",
)
def get_logs(lines: int = 120):
    r"""
    返回日志文件最后若干行。

    【为什么要有这个接口？】
        打包成 exe 之后没有黑色命令行窗口，
        用户遇到问题时【没有任何地方能看到报错】。

        有了它，设置页就能直接显示日志，
        用户把内容复制发给你，你立刻就知道发生了什么。
    """
    lines = max(10, min(int(lines or 120), 1000))     # 限制范围，防止有人传个天文数字
    return {
        "info": logging_setup.log_dir_info(),
        "content": logging_setup.tail(lines),
    }


@router.post(
    "/preference",
    summary="写一个偏好设置",
)
def set_preference(payload: PreferenceRequest):
    # 【重构说明】以前这里手动 raise HTTPException，
    # 现在统一抛 BusinessError，由全局处理器翻译成 400 —— 错误格式和别处一致。
    allowed = EDITABLE_PREFERENCES.get(payload.key)
    if allowed is None:
        raise app_errors.BusinessError(
            "不支持的设置项：%s（可选：%s）" % (
                payload.key, ", ".join(sorted(EDITABLE_PREFERENCES))))
    if payload.value not in allowed:
        raise app_errors.BusinessError(
            "%s 的取值只能是：%s" % (payload.key, ", ".join(sorted(allowed))))
    setting_repo.set_value(payload.key, payload.value)
    return {"ok": True, "key": payload.key, "value": payload.value}


@router.post(
    "/register-notify-app",
    summary="注册 Windows 通知的应用身份",
)
def register_notify_app():
    """
    注册 AppUserModelID。

    不注册的话，Windows 可能【静默丢弃】我们的通知 ——
    接口返回成功，但屏幕上一个通知都不出现。详见 BUG-013。
    """
    from app.core import notifier
    return notifier.register_app_id()


@router.post(
    "/backup",
    summary="立刻备份一次数据库",
)
def make_backup():
    """
    手动做一次数据库备份。

    平时程序【启动时】会自动备份（距上次不足 1 小时则跳过）；
    这个接口是给"我马上要改点危险的东西，先备一份"这种情况用的。
    """
    result = backup_core.make_backup(reason="manual")
    result["backups"] = setting_service.backups()
    return result


@router.post(
    "/open-folder",
    response_model=MessageOut,
    summary="在资源管理器里打开目录",
)
def open_folder(payload: OpenFolderRequest):
    """打开项目目录 / 数据目录 / 文档目录 / 启动文件夹。

    目标目录受白名单限制（见 setting_service.FOLDER_MAP），
    防止有人通过这个接口打开系统任意路径。
    """
    result = setting_service.open_folder(payload.target)
    if not result.get("ok"):
        raise app_errors.BusinessError(result.get("message") or "打开目录失败")
    return MessageOut(ok=True, message=result.get("message", "已打开"))
