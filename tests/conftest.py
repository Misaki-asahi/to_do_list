# -*- coding: utf-8 -*-
r"""
conftest.py -- pytest 的公共装置（fixture）。

【这个文件最重要的作用：让测试【绝不碰】你的真实数据】

    pytest 会自动先加载 conftest.py，再加载各个测试文件。
    所以我们可以在这里、在 import app 之前，把数据目录切到测试专用目录。

    ⚠️ 顺序至关重要：app.config 在【第一次被导入时】就会读环境变量定路径。
       导入之后再设就来不及了 —— 这个坑见 BUG-009。

【另一个重要的事：测试绝不能真的弹系统通知】

    提醒模块会调用 Windows 通知 API。如果测试真的触发它，
    你跑一遍测试就会被弹几十个通知窗口 —— 而且这些通知是"不点不掉"的。

    所以下面用一个 session 级的 fixture 把 notifier.show 换成空实现。
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ===========================================================================
# ★ 必须在 import app 之前：把数据目录整体切到测试目录
# ===========================================================================
TEST_DATA_DIR = PROJECT_ROOT / "data" / "test" / "pytest"
os.environ["TODO_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["TODO_DB"] = str(TEST_DATA_DIR / "pytest.db")

# 现在才可以安全地导入 app
from fastapi.testclient import TestClient          # noqa: E402

from app import config                             # noqa: E402
from app.core import errors as app_errors          # noqa: E402
from app.main import app                           # noqa: E402
from app.repositories import task_repo             # noqa: E402


def pytest_sessionstart(session):
    """整个测试会话开始前：清空测试目录，保证从干净状态开始。"""
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)


def pytest_sessionfinish(session, exitstatus):
    """整个测试会话结束后：把测试数据删掉，不留垃圾。"""
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def disable_native_notifications():
    r"""
    【全局生效】把系统通知换成空实现。

    测试环境不应该真的往屏幕上弹通知 —— 那既不礼貌，也没法自动化。
    我们只关心"该不该弹"这个逻辑，不关心"弹出来长什么样"。
    """
    from app.core import notifier
    original = notifier.show
    notifier.show = lambda *args, **kwargs: {"ok": True, "message": "测试环境：已跳过"}
    yield
    notifier.show = original


@pytest.fixture(scope="session", autouse=True)
def disable_floating_window_spawn():
    r"""
    【全局生效】禁止测试真的去启动桌面悬浮窗进程（v0.3.0 新增）。

    和上面那个"不弹系统通知"是同样的道理：
        悬浮窗的守护线程每 3 秒检查一次，一旦测试里有人写了
        "悬浮窗=开启"，它就会真的去 spawn 一个 tkinter 进程 ——
        于是你跑一次测试，屏幕上就多出一堆小窗口，还会一直开着。
        更糟的是测试结束时那批进程还活着，得手动一个个关掉。

    【为什么替换的是 _popen，而不是 _spawn？】（这一点很重要，踩过坑）
        一开始我替换的是 _spawn（"启动窗口"那个总函数），
        结果测试永远拿不到启动参数 —— 因为参数【是在 _spawn 里组装的】，
        把 _spawn 整个换掉，被测的那段代码就不执行了。测试变成了"测了个假的"。

        现在改成只替换最后一步（真正调用 Popen），于是：
            · 参数组装照常执行  -> 可以断言"令牌传了没、--floating 加了没"
            · 进程不会被真的拉起 -> 跑测试不会弹出一堆窗口
        所以下面这个假函数同时兼顾了"安全"和"可测"。
    """
    from app.services import floating_service

    calls = []

    def fake_popen(cmd, **kwargs):
        """假装启动成功，并把收到的参数记下来供测试断言。"""
        class _FakeProc:
            pid = 999999          # 一个绝不会和真实进程撞上的进程号

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        calls.append({"cmd": cmd, **kwargs})

        # 【必须做的事】把日志文件句柄关掉。
        # 因为真实现场是子进程持有它，父进程立刻关闭；
        # 测试里如果不关，Windows 上文件会被占着，后续测试删不掉目录。
        stream = kwargs.get("stdout")
        if hasattr(stream, "close"):
            try:
                stream.close()
            except OSError:
                pass
        return _FakeProc()

    original = floating_service._popen
    floating_service._popen = fake_popen
    # 挂到模块上，方便个别测试查看"有没有被调用过"
    floating_service._fake_spawn_calls = calls
    yield
    floating_service._popen = original
    floating_service._fake_spawn_calls = []


@pytest.fixture(scope="session", autouse=True)
def isolate_autostart_folder():
    r"""
    【全局生效】禁止测试去写【真实的 Windows 启动文件夹】。

    【为什么需要它？（v0.4.2 的一次真实误操作）】
        为了让"出厂默认开启开机自启"生效，服务启动钩子里加了一句
        "没有启动项就补建一个"。

        问题是：**测试用的 TestClient 也会触发启动钩子**。
        于是跑一次 python -m pytest，就在开发者真实的
        %APPDATA%\...\Startup 里写了一个 OfflineTodoList.vbs ——
        数据能靠 TODO_DATA_DIR 隔离，但"启动文件夹"是操作系统的东西，
        它不认这个环境变量。

        这个装置和"不弹系统通知""不真的开悬浮窗进程"是同一类：
        把"会留下系统痕迹的一步"换成假的。

    （代码里还有第二道网：ensure_autostart_by_default() 会先判断
      "当前是不是默认数据目录"，不是就直接跳过 —— 双保险。）
    """
    from app.core import autostart

    fake = TEST_DATA_DIR / "Startup"
    fake.mkdir(parents=True, exist_ok=True)
    original = autostart.startup_dir
    autostart.startup_dir = lambda: fake
    yield
    autostart.startup_dir = original


@pytest.fixture(scope="session")
def client():
    r"""
    一个共享的测试客户端。

    用 with 包起来是为了触发 lifespan（启动钩子）——
    这样建表、后台线程等真实启动流程都会跑一遍，测得更接近真实。
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _reset_database():
    """把数据库恢复成"空"的状态：待办清空、设置清空。"""
    from sqlalchemy import delete

    from app.database import get_session
    from app.models import Setting

    task_repo.reset_all(confirm=True)
    with get_session() as session:
        session.execute(delete(Setting))


@pytest.fixture(autouse=True)
def clean_db(client):
    r"""
    【每个测试函数前自动执行】把数据库恢复到干净状态。

    为什么必须这么做？
        测试之间必须互相独立。如果上一个测试留下的数据影响下一个测试，
        就会出现"单独跑通过、一起跑失败"这种最让人头疼的问题。

        这次就真的遇到了：测设置表时，前面的测试留下了 theme / flag / num，
        导致"全部设置"的断言失败。**测试自己把测试搞挂了。**

    ⚠️ 参数里的 client 不能去掉：
        数据库表是在应用启动钩子（lifespan）里创建的，
        而 client 这个 fixture 负责触发它。
        如果不依赖 client，纯逻辑测试跑起来时表还没建好，
        清理操作会直接报 "no such table: tasks"。
    """
    _reset_database()
    yield
    _reset_database()


@pytest.fixture
def sample_task(client):
    """一条现成的待办，方便测试"修改 / 删除 / 完成"这类操作。"""
    resp = client.post("/api/tasks", json={"title": "示例待办", "priority": 1})
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture
def make_task(client):
    """工厂 fixture：调用它就能造一条待办。

    用法：
        def test_x(make_task):
            t = make_task(title="买牛奶", priority=2)
    """
    def _make(**kwargs):
        payload = {"title": "测试任务"}
        payload.update(kwargs)
        resp = client.post("/api/tasks", json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()
    return _make


def future_due(day_offset: int = 0, now=None) -> tuple:
    r"""
    造一个【保证在未来】的截止时间字符串，格式 "YYYY-MM-DD HH:MM"。

    【★ 为什么必须有这个函数？（这是排查出来的一个真实缺陷，见 BUG-032）】

        原来的测试里到处写着这样的代码：

            make_task(title="今天的事", due_at=timeutil.today_prefix() + " 23:00")

        这句话在**白天跑**是没问题的。但业务规则里有一条：
        "截止时间不能早于当前时间" —— 于是**晚上 23:00 之后再跑测试**，
        它就会返回 400，测试**假失败**。

        这个缺陷是晚上 20:02 修另一个 Bug 时撞出来的：当时有个测试用了
        "今天 20:00"，正好已经过去了。也就是说 **测试只在一天的某个时段是绿的** ——
        这种 Bug 最气人：明天早上跑一遍又全是绿的，你会以为是自己手滑。

    【解决办法：把"时刻"也动态算出来，而不是只算日期】
        · 现在还没到 22:30 -> 用今天的 23:50（保持"今天"这个语义）
        · 已经过了 22:30 -> 顺延到明天 23:50
          （因为今天已经没有一个"还没到"的晚时刻可用了）

    Args:
        day_offset: 预留参数（当前实现不使用，保留是为了将来要"后天"这类需求）
        now: 注入"现在"便于测试。**测试里专门传它来覆盖各个时间点** ——
             否则这段逻辑就只能"等真的到深夜才验证"，那不叫验证。

    Returns:
        (due_at 字符串, "today" 或 "tomorrow")
    """
    from datetime import datetime, timedelta

    now = now or datetime.now()
    late = now.hour == 23 or (now.hour >= 22 and now.minute >= 30)
    target = now + timedelta(days=1) if late else now
    which = "tomorrow" if late else "today"
    return target.strftime("%Y-%m-%d ") + "23:50", which


def future_due_today(now=None) -> str:
    r"""
    造一个【今天之内、且保证还在未来】的截止时间 "YYYY-MM-DD HH:MM"。

    【什么时候用它，而不是上面的 future_due()？】
        有些测试**必须**让任务落在"今天"——比如它要断言日历里今天那一格、
        或者要断言分组标题以"今天"开头。
        这时上面的 future_due() 不够用（它在深夜会顺延到明天）。

    ⚠️ 接近午夜（23:56 之后）调用会抛 RuntimeError，
       调用方应该捕获并 `pytest.skip("已接近午夜，今天之内没有合法时刻")`。
       这是**诚实地跳过**，而不是让它"偶尔红一次" ——
       那种"只在深夜失败的测试"比没有测试更糟：它会训练你忽略红色。
    """
    from datetime import datetime

    now = now or datetime.now()
    if now.hour == 23 and now.minute >= 56:
        raise RuntimeError("已接近午夜，今天之内已经没有一个'还没到'的时刻了")
    return now.strftime("%Y-%m-%d ") + "23:59"


@pytest.fixture
def future_due_clock():
    """
    `future_due` 的 fixture 版本。

    之所以做成 fixture（而不是让测试直接 import 那个函数），
    是为了和这个文件里其它测试工具**用法一致**：
    需要它的测试在参数里写上 `future_due_clock` 就行，不用额外 import。

    用法：
        def test_x(make_task, future_due_clock):
            due, which = future_due_clock()          # which 是 "today"/"tomorrow"
            make_task(title="今天的事", due_at=due)
    """
    return future_due


@pytest.fixture
def future_due_today_clock():
    """`future_due_today` 的 fixture 版本（用法同上，参数里写上它即可）。"""
    return future_due_today
