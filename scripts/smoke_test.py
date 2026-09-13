# -*- coding: utf-8 -*-
"""
smoke_test.py -- 冒烟测试：一条命令检查所有页面和接口是否正常。

"冒烟测试"是行业术语，意思是"通电看看冒不冒烟"——
不测细节，只快速确认整个程序大体是活的。

用法（必须在【项目根目录】执行）：
    1. 先在另一个窗口运行 python run.py（让服务跑起来）
    2. 再运行：python scripts/smoke_test.py
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 接口返回的内容里可能有 emoji / 生僻字，重定向到文件时会编码失败（BUG-019）
from scripts._console import use_safe_output   # noqa: E402
use_safe_output()

BASE = "http://127.0.0.1:8000"

# (请求路径, 说明)
PAGES = [
    ("/", "首页（待办列表）"),
    ("/calendar", "日历页"),
    ("/trash", "回收站页"),
    ("/settings", "设置页"),
    ("/docs", "自动生成的接口文档"),
]

ASSETS = [
    ("/static/css/style.css", "全局样式"),
    ("/static/js/api.js", "api.js（唯一请求出口）"),
    ("/static/js/health.js", "health.js（状态栏）"),
    ("/static/js/list.js", "list.js（待办列表）"),
    ("/static/js/edit.js", "edit.js（编辑弹窗）"),
    ("/static/js/calendar.js", "calendar.js（日历）"),
    ("/static/js/trash.js", "trash.js（回收站）"),
    ("/static/js/reminder.js", "reminder.js（提醒）"),
    ("/static/js/settings.js", "settings.js（设置页）"),
]

APIS = [
    ("/api/health", "健康检查"),
    ("/api/tasks", "待办列表"),
    ("/api/tasks/stats", "待办统计"),
    ("/api/tasks/trash", "回收站列表"),
    ("/api/reminders/inbox", "提醒收件箱"),
    ("/api/reminders/scheduler", "后台提醒线程状态"),
    ("/api/reminders/stats", "提醒统计"),
    ("/api/calendar/month", "日历网格"),
    ("/api/calendar/day?day=2026-01-01", "某天详情"),
    ("/api/settings", "设置与系统状态"),
]


def check(path, name, results):
    try:
        with urllib.request.urlopen(BASE + path, timeout=8) as resp:
            size = len(resp.read())
            results.append((path, resp.status, size, name, None))
    except urllib.error.HTTPError as exc:
        results.append((path, exc.code, 0, name, "HTTP %s" % exc.code))
    except Exception as exc:
        results.append((path, "ERR", 0, name, str(exc)[:60]))


def main():
    print("正在检查 %s ..." % BASE)
    print()

    results = []
    for group, items in (("页面", PAGES), ("静态资源", ASSETS), ("接口", APIS)):
        print("【%s】" % group)
        print("%-38s %-7s %-9s %s" % ("路径", "状态", "大小", "说明"))
        print("-" * 78)
        for path, name in items:
            check(path, name, results)
            row = results[-1]
            note = ("   <== " + row[4]) if row[4] else ""
            print("%-38s %-7s %-9s %s%s" % (
                path, row[1], ("%dB" % row[2]) if row[2] else "-", name, note))
        print()

    failed = [r for r in results if r[1] != 200]
    print("-" * 78)

    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("健康检查: status=%s  version=%s" % (data.get("status"), data.get("version")))
            db = data.get("db", {})
            print("数据库  : %s  表=%s  待办=%s 条  大小=%s KB" % (
                "已就绪" if db.get("exists") else "缺失",
                db.get("tables"), db.get("task_count"), db.get("size_kb")))
    except Exception as exc:
        print("无法读取 /api/health: %s" % exc)

    print()
    if failed:
        print("[失败] 有 %d 项异常：" % len(failed))
        for r in failed:
            print("   %s -> %s  %s" % (r[0], r[1], r[4] or ""))
        print()
        print("排查顺序：")
        print("   1. 服务是否已启动（另一个窗口在跑 python run.py）")
        print("   2. 端口是否与 config.py 中的 PORT 一致")
        print("   3. 终端里后端有没有打印报错信息")
        sys.exit(1)

    print("[通过] 共检查 %d 项，全部正常。" % len(results))


if __name__ == "__main__":
    main()
