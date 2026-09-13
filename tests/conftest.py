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
