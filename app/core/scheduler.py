# -*- coding: utf-8 -*-
"""
scheduler.py -- 后台提醒线程。

【这个模块经历过一次"删掉又加回来"，原因值得记住】

    最初的提醒设计是"前端轮询，接口内扫描+标记"，并且【故意不做后台线程】。
    理由是：如果后端提前把提醒标记成"已提醒"，而当时没人看见，
    这条提醒就被永久吃掉了 —— 等于丢提醒。

    但那个设计有一个无法回避的代价：
        **浏览器没打开，就完全没有提醒。**
        （过期提醒不会丢，但要等你打开页面才补弹）

    现在有了 Windows 原生系统通知，这个理由不再成立：
        后端自己就能把提醒"显示"出来，
        所以后端来标记"已提醒"是安全的 —— 用户真的收到了。

【现在的分工】

    后端线程（本模块）：扫描 -> 发系统通知 -> 标记已提醒 -> 放进 UI 收件箱
    前端页面            ：轮询收件箱 -> 右下角卡片（带操作按钮）-> 确认

    两者都会显示，是【故意】的：
        系统通知负责"你一定能看到"（哪怕切到别的软件、哪怕浏览器关着）；
        页面内卡片负责"给操作按钮"（标记完成 / 稍后提醒）。
"""

import threading
import time

from app import config
from app.core import timeutil
from app.services import reminder_service

__all__ = ["start", "stop", "is_running", "last_run", "config"]

# 扫描间隔的兜底默认值。
# 为什么要兜底？——见 BUG-012：曾经因为配置常量的名字对不上（POLL / SCAN），
# 后台线程在读取时就抛异常、安静地死掉，而且完全没有提示。
DEFAULT_INTERVAL = 20

_thread = None
_stop_event = threading.Event()

# 记录最后一次扫描的结果，方便设置页显示、也方便排查"为什么不提醒"
last_run = {
    "at": None,
    "fired": 0,
    "native_ok": 0,
    "error": None,
}


def _interval() -> int:
    """安全地读取扫描间隔。配置缺失或值不合法时退回默认值，绝不抛异常。"""
    try:
        return max(5, int(config.REMINDER_SCAN_SECONDS))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_INTERVAL


def start():
    """启动后台线程（重复调用只会启动一次）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return False

    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="reminder-scheduler", daemon=True)
    _thread.start()
    return True


def stop():
    """请求线程退出（服务关闭时调用）。"""
    _stop_event.set()


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _loop():
    """
    线程主体：不停地"等一会儿 -> 扫一次"。

    三个要点：
        1. 启动后先等 3 秒，让 Web 服务先完全起来，避免启动瞬间的资源竞争；
        2. 用 _stop_event.wait(秒数) 而不是 time.sleep()：
           关闭服务时能【立刻】醒来退出，不用干等满 20 秒；
        3. ★ 整个循环体（包括"读配置""算等待时间"）都必须包在 try 里。
           后台线程抛异常不会被任何人看到 —— 它会安静地退出，
           而程序表面上一切正常，只是再也不提醒了。
           这种"静默死亡"是最难排查的一类 Bug（见 BUG-012）。
    """
    time.sleep(3)

    while not _stop_event.is_set():
        try:
            result = reminder_service.scan_and_fire()
            last_run["at"] = timeutil.now_str()
            last_run["fired"] = result.get("fired", 0)
            last_run["native_ok"] = result.get("native_ok", 0)
            last_run["error"] = None
        except Exception as exc:            # noqa: BLE001 - 后台线程必须吞掉所有异常
            last_run["error"] = "%s: %s" % (type(exc).__name__, exc)

        # 等待下一次扫描；期间如果收到停止信号，会立刻返回。
        # 【注意】这一行以前写在 try 外面 —— 读配置出错就会让线程直接死掉。
        # 现在放进 try，并用了带兜底的 _interval()，双保险。
        try:
            _stop_event.wait(_interval())
        except Exception as exc:            # noqa: BLE001
            last_run["error"] = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(DEFAULT_INTERVAL)
