
# -*- coding: utf-8 -*-
"""
check_errors.py -- 日志与异常处理验收测试。

用法（必须在【项目根目录】执行，或直接双击 check.bat）：
    python scripts/check_errors.py

【重要：测试绝不碰你的真实数据】
    和 check_repo.py 一样，它在导入 app 之前就切到隔离数据库，
    并在导入后立刻复核。详见 AGENTS.md 数据安全铁律一。
"""

import json
import os
import sys
from pathlib import Path

# 【必须用 __file__ 推算路径，不能写死绝对路径】
# 写死的话：① 用户把项目文件夹换个位置就失效；
#           ② 把自己的本机目录结构泄露到了公开仓库里。
# 见 docs/问题记录与解决方案.md 的 BUG-020。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 输出可能被重定向到文件，此时编码可能是 GBK，装不下 emoji 等字符（BUG-019）
from scripts._console import use_safe_output            # noqa: E402
use_safe_output()

# ★ 必须在 import app 之前隔离（见 AGENTS.md 数据安全铁律一）
from scripts._isolation import assert_isolated, use_isolated_db   # noqa: E402
TEST_DB = use_isolated_db("error_handling")

from fastapi.testclient import TestClient                # noqa: E402
from app import config                                   # noqa: E402

# ===========================================================================
# 【关键】清掉【上一次运行】留下的日志
#
#   下面有「500 异常只被记录一次」这类断言，判断方法是【数日志里出现了几次】。
#   而日志是【追加】写入的 —— 不清掉的话，跑第二遍就会数出两倍，误报失败：
#       第 1 次运行 -> 通过
#       第 2 次运行 -> 「500 异常只被记录一次」失败（实际记录了 2 次）
#   用户会以为代码坏了，其实只是历史日志没清。详见 BUG-021。
#
# 【为什么必须写在这里？】
#   app/main.py 在被【导入的那一刻】就调用 logging_setup.setup()（模块级第 38 行），
#   日志文件随即被打开占用 —— 在 Windows 上，被占用的文件是删不掉的。
#   所以清理必须放在 `from app.main import app` 之前。
# ===========================================================================
import shutil                                            # noqa: E402
shutil.rmtree(config.LOG_DIR, ignore_errors=True)
config.LOG_DIR.mkdir(parents=True, exist_ok=True)

from app.core import errors as app_errors, logging_setup    # noqa: E402
from app.main import app                                    # noqa: E402
from app.services import task_service                       # noqa: E402

assert_isolated()

problems = []
log_file = config.LOG_DIR / "app.log"


def check(name, condition, detail=""):
    ok = bool(condition)
    print("   [%s] %s%s" % ("OK " if ok else "!!!", name, ("  " + str(detail)) if detail and not ok else ""))
    if not ok:
        problems.append(name)
    return ok


print("=" * 76)
print("【日志与异常处理 验收测试】")
print("=" * 76)
print("   隔离数据目录:", config.DATA_DIR)
print("   日志文件    :", log_file)

# 【关键】raise_server_exceptions=False 表示：
#   服务端抛异常时，TestClient 把【500 响应】交给测试，而不是把异常重新抛出来。
#   默认值是 True（直接把异常抛给测试），那样就测不到"全局异常处理器返回了什么"。
with TestClient(app, raise_server_exceptions=False) as client:

    print()
    print("--- 1) 正常功能没被改坏（回归）---")
    r = client.post("/api/tasks", json={"title": "回归测试任务", "priority": 1})
    check("新建待办 -> 201", r.status_code == 201, r.status_code)
    task_id = r.json()["id"]

    check("列表 -> 200", client.get("/api/tasks").status_code == 200)
    check("统计 -> 200", client.get("/api/tasks/stats").status_code == 200)
    check("单条 -> 200", client.get("/api/tasks/%d" % task_id).status_code == 200)
    check("切换 -> 200", client.post("/api/tasks/%d/toggle" % task_id).status_code == 200)
    check("日历 -> 200", client.get("/api/calendar/month").status_code == 200)
    check("设置 -> 200", client.get("/api/settings").status_code == 200)
    check("提醒收件箱 -> 200", client.get("/api/reminders/inbox").status_code == 200)

    print()
    print("--- 2) 业务规则不通过 -> 400（全局处理器）---")
    r = client.post("/api/tasks", json={"title": "时间在过去", "remind_at": "2020-01-01 09:00"})
    check("返回 400", r.status_code == 400, r.status_code)
    body = r.json()
    check("错误类型标记正确", body.get("type") == "business_error", body.get("type"))
    check("有给用户看的 detail", bool(body.get("detail")), body.get("detail"))
    print("        detail =", body.get("detail"))

    print()
    print("--- 3) 数据不存在 -> 404 ---")
    r = client.get("/api/tasks/999999")
    check("查询返回 404", r.status_code == 404, r.status_code)
    check("类型标记为 not_found", r.json().get("type") == "not_found")
    check("修改返回 404", client.patch("/api/tasks/999999", json={"title": "x"}).status_code == 404)
    check("删除返回 404", client.delete("/api/tasks/999999").status_code == 404)
    check("切换返回 404", client.post("/api/tasks/999999/toggle").status_code == 404)

    print()
    print("--- 4) 格式校验 -> 422（统一后的结构）---")
    r = client.post("/api/tasks", json={})
    check("返回 422", r.status_code == 422, r.status_code)
    body = r.json()
    check("类型标记为 validation_error", body.get("type") == "validation_error", body.get("type"))
    check("有 errors 列表", isinstance(body.get("errors"), list) and len(body["errors"]) > 0)
    if body.get("errors"):
        print("        第一条:", body["errors"][0])

    r = client.post("/api/tasks", json={"title": "x", "priority": 99})
    check("优先级越界 -> 422", r.status_code == 422, r.status_code)

    print()
    print("--- 5) 未处理的异常 -> 500（兜底处理器 + 记日志）---")
    original = task_service.list_tasks

    def boom(*args, **kwargs):
        raise RuntimeError("这是测试故意制造的异常")

    task_service.list_tasks = boom
    try:
        r = client.get("/api/tasks")
        check("返回 500 而不是崩溃", r.status_code == 500, r.status_code)
        body = r.json()
        check("类型标记为 internal_error", body.get("type") == "internal_error", body.get("type"))
        check("没有把堆栈泄露给用户", "RuntimeError" not in json.dumps(body, ensure_ascii=False))
        print("        给用户看的话:", body.get("detail", "")[:60], "…")
    finally:
        task_service.list_tasks = original

    print()
    print("--- 6) 出错的接口恢复正常（异常没把服务搞坏）---")
    check("恢复后列表 -> 200", client.get("/api/tasks").status_code == 200)

    print()
    print("--- 7) 自定义异常类能被正确识别 ---")
    r = client.post("/api/settings/preference", json={"key": "不存在的键", "value": "1"})
    check("非法设置项 -> 400", r.status_code == 400, r.status_code)
    check("类型是 business_error", r.json().get("type") == "business_error")

    print()
    print("--- 8) 清理测试数据 ---")
    check("删除测试任务", client.delete("/api/tasks/%d" % task_id).status_code == 200)

print()
print("--- 9) 日志文件是否真的写出来了 ---")
check("日志文件存在", log_file.exists(), str(log_file))
if log_file.exists():
    text = log_file.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    print("        日志行数: %d  大小: %.1f KB" % (len(lines), log_file.stat().st_size / 1024))
    check("记录了启动信息", "启动" in text)
    check("记录了 400 业务错误", "business_error" in text or "400" in text)
    check("记录了 500 的完整堆栈", "RuntimeError" in text and "Traceback" in text)
    check("记录了 404", "404" in text)

    # 【重要】同一个异常只能记一次，否则日志会被撑爆
    n_500_logs = text.count("未处理的异常")
    check("500 异常只被记录一次", n_500_logs == 1, "实际记录了 %d 次" % n_500_logs)
    # 数"堆栈头"而不是数异常文本 ——
    # 异常文本在堆栈里会自然出现两次（源码行 + 异常信息），是同一条日志里的内容
    n_tracebacks = text.count("Traceback (most recent call last)")
    check("完整堆栈只记录一次", n_tracebacks == 1, "实际有 %d 个堆栈" % n_tracebacks)
    check("没有 httpx 的刷屏日志", "HTTP Request: " not in text)
    print()
    print("    --- 日志最后 12 行 ---")
    for line in lines[-12:]:
        print("    " + line[:150])

print()
print("=" * 76)
print(("失败项: " + str(problems)) if problems else "全部通过")
print("=" * 76)
