# -*- coding: utf-8 -*-
"""
run.py -- 项目总入口。

作用：启动服务，并自动帮你打开浏览器。

    python run.py                 源码运行
    python run.py --no-browser    只启动服务，不开浏览器（开机自启用）

【打包成 exe 之后，这个文件也是入口】
    PyInstaller 会把这里当成程序的起点。
    但打包后有两件事必须区别对待，否则会出问题：
        ① 没有控制台窗口 —— print() 没人看得到，出错要弹系统对话框；
        ② uvicorn 的启动方式要换 —— 见下面 main() 里的说明。
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
        "  数据目录: %s" % config.BASE_DIR,
        "  按 Ctrl + C 停止服务",
        "=" * 58,
    ]
    print("\n".join(lines))


def main():
    _ensure_std_streams()
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
