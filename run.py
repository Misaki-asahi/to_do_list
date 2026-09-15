# -*- coding: utf-8 -*-
"""
run.py -- 项目总入口。

作用：启动服务，并自动帮你打开浏览器。

    python run.py                 源码运行
    python run.py --no-browser    只启动服务，不开浏览器（开机自启用）
    python run.py --floating      只启动【桌面悬浮窗】，不起服务（v0.3.0 新增）
    python run.py --floating-stop  让正在运行的悬浮窗退出（v0.3.0 新增）

【打包成 exe 之后，这个文件也是入口】
    PyInstaller 会把这里当成程序的起点。
    但打包后有两件事必须区别对待，否则会出问题：
        ① 没有控制台窗口 —— print() 没人看得到，出错要弹系统对话框；
        ② uvicorn 的启动方式要换 —— 见下面 main() 里的说明。

【--floating 是怎么被用到的？】
    服务端要显示悬浮窗时，会用【同一个可执行文件 / 同一个 run.py】
    再启动一个进程，并带上 --floating 参数。
    那个进程不会起 Web 服务（端口只有一个，起第二个必然失败），
    它只是去连已经跑着的那个服务，然后把窗口画出来。
    这条路径见 app/services/floating_service.py 的 _spawn()。
"""

import sys
import threading
import time
import webbrowser

import uvicorn

from app import config


def _ensure_std_streams():
    """
    打包成"无控制台"的 exe 后，sys.stdout / sys.stderr 可能是 None。
    这时任何 print() 都会直接抛异常，把程序搞崩。

    所以启动前先把它们换成"什么都不做的空对象"。
    这样代码里所有的 print 都能照常写，不用担心运行环境。
    （这些提示在打包版里本来就看不到，只是不能让它们引起崩溃。）
    """
    if sys.stdout is None or sys.stderr is None:
        class _NullStream:
            def write(self, *_args):
                return 0
            def flush(self, *_args):
                pass
            def isatty(self):
                return False
        if sys.stdout is None:
            sys.stdout = _NullStream()
        if sys.stderr is None:
            sys.stderr = _NullStream()


def show_error(message, title=None):
    """
    把错误信息显示给用户。

    【为什么不能只用 print？】
        打包后我们用的是 --noconsole（不带黑窗口），
        print() 的内容会消失在虚空里 —— 用户只会看到"双击了没反应"。

        所以出错时要弹一个系统对话框，把原因说清楚。
        这是所有 Windows 桌面程序都该做的事。
    """
    title = title or config.APP_NAME
    try:
        import ctypes
        # 0x10 = 红色错误图标
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10)
    except Exception:                                  # noqa: BLE001
        # 非 Windows 或调用失败时退回控制台输出
        print("[错误] %s" % message)


def open_browser_later(url, delay=1.5):
    """
    延迟一小会儿再打开浏览器。

    为什么延迟？
        服务器启动需要时间。立刻打开的话，用户会先看到"无法连接"的页面。
    """
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:                              # noqa: BLE001
            pass

    # daemon=True：这个线程是"随从"，主程序退出时它一起退出
    threading.Thread(target=_open, daemon=True).start()


def banner(url):
    """打印启动信息（打包后没有控制台，这些内容看不到，但源码运行时很有用）。"""
    lines = [
        "=" * 58,
        "  %s  v%s" % (config.APP_NAME, config.APP_VERSION),
        "=" * 58,
        "  主界面  : %s" % url,
        "  接口文档: %s/docs" % url,
        # ★ 【v0.4.4 修】以前这里印的是 BASE_DIR（程序"大本营"），
        #   但真正存数据的是 DATA_DIR —— 两者在"用 TODO_DATA_DIR 覆盖过"时不一样。
        #   本轮排查打包版时就因为这句话看错了目录，白找了十几分钟。
        #   顺带把 BASE_DIR 也印出来，两个都写清楚，不留歧义。
        "  数据目录: %s" % config.DATA_DIR,
        "  程序目录: %s" % config.BASE_DIR,
        "  按 Ctrl + C 停止服务",
        "=" * 58,
    ]
    print("\n".join(lines))


def run_floating_window():
    """
    --floating 模式的入口：只画桌面悬浮窗，不起 Web 服务。

    【为什么要单独一个模式？】
        因为服务端需要"再启动一个自己"来画窗口，
        而那个新进程必须【跳过】所有的服务初始化（数据库、备份、端口绑定）。
        用参数分流比"复制一份入口脚本"好得多：
        整个项目仍然只有一个入口，打包配置也只需要一份。

    【它自己会做的事】
        1. 抢窗口"座位"（保证不会同时出现两个窗口）；
        2. 等服务端就绪（开机自启时窗口可能比服务起得快）；
        3. 画窗口、进主循环。

    出错时弹一个系统对话框 —— 因为它是 noconsole 启动的，
    没有任何地方能看到 print 的内容，静默失败会让用户完全摸不着头脑。
    """
    try:
        # 只有真的要用界面了才 import tkinter。
        # 这保证"没装 tkinter 的机器"只是用不了悬浮窗，而不是整个程序起不来。
        from app.gui import window as floating_window
    except Exception as exc:                           # noqa: BLE001
        show_error("无法加载悬浮窗界面：\n%s\n\n"
                   "可能是这个 Python 没有自带 tkinter 模块。" % exc)
        return 1

    try:
        config.ensure_dirs()
    except OSError as exc:
        show_error("无法创建数据目录：\n%s\n\n%s" % (config.BASE_DIR, exc))
        return 1

    try:
        return floating_window.main()
    except Exception as exc:                            # noqa: BLE001
        show_error("悬浮窗启动失败：\n\n%s: %s" % (type(exc).__name__, exc))
        return 1


def stop_floating_window():
    """
    --floating-stop 模式：请正在运行的悬浮窗退出。

    【为什么需要它？】
        用户点了窗口的 ✕ 之后，窗口只是"隐藏"——服务里还是"开启"状态。
        如果哪天窗口卡死在屏幕外（拔掉副屏这种情况），
        用户就需要一个"不管怎样先把它停掉"的办法。
        这个命令就是那个后备方案（设置页的"关闭悬浮窗"也走同一段逻辑）。
    """
    try:
        from app.services import floating_service
        stopped = floating_service.stop_window(reason="命令行要求停止")
        if stopped:
            print("已停止悬浮窗")
        else:
            print("当前没有正在运行的悬浮窗")
        return 0
    except Exception as exc:                            # noqa: BLE001
        show_error("停止悬浮窗失败：\n\n%s: %s" % (type(exc).__name__, exc))
        return 1


def main():
    _ensure_std_streams()

    # ------------------------------------------------------------------
    # 先处理"悬浮窗模式"：它【不是】服务，所以要在所有服务相关的准备之前分流。
    #
    # 【为什么不能共用后面那一套？】
    #   后面那套会去 bind 8000 端口。如果悬浮窗进程也走那条路，
    #   第二个进程会因为"端口被占用"直接崩掉 —— 而用户只看到"窗口闪了一下就没了"。
    #   所以带 --floating 时我们【只】画窗口，绝不碰端口。
    # ------------------------------------------------------------------
    if "--floating" in sys.argv:
        return run_floating_window()

    if "--floating-stop" in sys.argv:
        return stop_floating_window()

    no_browser = "--no-browser" in sys.argv

    # 1) 确保数据目录存在（第一次运行会自动创建）
    try:
        config.ensure_dirs()
    except OSError as exc:
        show_error("无法创建数据目录：\n%s\n\n%s" % (config.BASE_DIR, exc))
        return 1

    url = "http://%s:%d" % (config.HOST, config.PORT)

    banner(url)

    # 2) 只有本机运行、并且没加 --no-browser 时才自动开浏览器
    if config.HOST in ("127.0.0.1", "localhost") and not no_browser:
        open_browser_later(url)
    elif no_browser:
        print("  （静默模式：请在浏览器里手动访问上面的地址）")

    # 3) 启动 Web 服务
    #
    # 【打包后为什么要换写法？】
    #   源码运行时写 "app.main:app" 这种字符串，uvicorn 会自己去 import。
    #   但打包成 exe 后，PyInstaller 是靠"静态分析代码"来收集依赖的，
    #   它看不见字符串里的模块名 —— 结果就是运行时找不到 app.main。
    #
    #   所以打包后我们直接把 app 对象传进去，不经过字符串。
    #   （代价是不能用 reload=True 热重载，但打包版本本来也不需要。）
    try:
        # 【access_log=False 是故意的】
        #   uvicorn 自带的访问日志会把【每个】请求都记一行（包括 CSS / JS），
        #   而我们在 app/main.py 里已经有一个中间件，记录的内容更全
        #   （带耗时、能区分 /api/ 和静态资源）。
        #   两个都开着 = 同一条请求记两遍，日志白白翻倍。
        #   原则：一个事件，只在一处记录。
        if config.FROZEN:
            from app.main import app as application
            uvicorn.run(application, host=config.HOST, port=config.PORT,
                        log_level="info", access_log=False)
        else:
            uvicorn.run("app.main:app", host=config.HOST, port=config.PORT,
                        reload=False, log_level="info", access_log=False)
    except OSError as exc:
        # 最常见的就是端口被占用
        show_error(
            "启动失败：%s\n\n"
            "最常见的原因是【端口 %d 已被占用】。\n"
            "请关掉之前残留的窗口，或者换个端口：\n"
            "    设置环境变量 TODO_PORT 后再运行" % (exc, config.PORT)
        )
        return 1
    except Exception as exc:                           # noqa: BLE001
        show_error("程序启动时出错：\n\n%s: %s" % (type(exc).__name__, exc))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
