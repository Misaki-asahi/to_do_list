# -*- coding: utf-8 -*-
"""
test_trash.py -- 回收站（软删除）的测试。

【这个测试文件覆盖三层】
    1. 数据库迁移 —— 老数据库能不能平滑加上 deleted_at 列（最容易出事的地方）
    2. 仓库层     —— 软删除到底"软"在哪
    3. 接口层     —— 四个新接口的行为，以及路由顺序有没有被踩坏

【为什么专门为"删除"写一个测试文件？】
    因为删除是本项目【唯一不可逆】的操作。
    别的功能写错了，最多是显示不对；删除写错了，数据就没了 ——
    而且是用户自己一点点录进去的数据。所以它值得最厚的测试。
"""

from sqlalchemy import create_engine, inspect, text

from app import config, database
from app.core import timeutil
from app.database import get_session
from app.models import Task
from app.repositories import task_repo


# ===========================================================================
# 一、数据库迁移（老用户升级时最危险的一步）
# ===========================================================================

def test_migrate_adds_missing_column(tmp_path, monkeypatch):
    """
    模拟一个"升级前"的数据库：有数据，但没有 deleted_at 列。

    跑一次 migrate_schema() 之后必须做到三件事：
        1. 新列加上了
        2. 【老数据一条都没少】  <- 最重要
        3. 老任务的 deleted_at 是 NULL（默认"没被删过"）
    """
    db_file = tmp_path / "legacy.db"
    legacy_engine = create_engine("sqlite:///" + db_file.as_posix())

    # 手工建一张"老结构"的表：故意不含 deleted_at
    with legacy_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tasks ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  title VARCHAR(200) NOT NULL,"
            "  status VARCHAR(10) NOT NULL DEFAULT 'todo',"
            "  user_id INTEGER NOT NULL DEFAULT 1"
            ")"
        ))
        conn.execute(text("INSERT INTO tasks (title, status, user_id) VALUES ('升级前就有的待办', 'todo', 1)"))

    # 让 migrate_schema() 去操作这个"老库"，而不是项目自己的库
    monkeypatch.setattr(database, "engine", legacy_engine)

    # --- 迁移前：确认真的没有那一列 ---
    before = {col["name"] for col in inspect(legacy_engine).get_columns("tasks")}
    assert "deleted_at" not in before

    # --- 执行迁移 ---
    added = database.migrate_schema()
    assert added == ["deleted_at"]

    # --- 迁移后：列有了 ---
    after = {col["name"] for col in inspect(legacy_engine).get_columns("tasks")}
    assert "deleted_at" in after

    # --- ★ 最关键的一条：老数据还在 ---
    with legacy_engine.connect() as conn:
        row = conn.execute(text("SELECT title, deleted_at FROM tasks WHERE id = 1")).one()
    assert row[0] == "升级前就有的待办"     # 数据没丢
    assert row[1] is None                   # 老任务默认"没被删过"


def test_migrate_is_idempotent(tmp_path, monkeypatch):
    """
    迁移必须可以【反复执行】。

    因为 init_db() 每次启动都会调用它。
    如果第二次执行就报 "duplicate column name"，那程序第二次启动就崩了。
    """
    db_file = tmp_path / "legacy.db"
    legacy_engine = create_engine("sqlite:///" + db_file.as_posix())
    with legacy_engine.begin() as conn:
        conn.execute(text("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, user_id INTEGER)"))

    monkeypatch.setattr(database, "engine", legacy_engine)

    assert database.migrate_schema() == ["deleted_at"]   # 第一次：加上了
    assert database.migrate_schema() == []               # 第二次：什么都不做
    assert database.migrate_schema() == []               # 第三次：依然什么都不做


def test_migrate_on_missing_table_does_nothing(tmp_path, monkeypatch):
    """
    全新的空数据库：连 tasks 表都没有。

    这时候迁移应该安静地返回空列表 ——
    因为建表是 create_all() 的活儿，迁移只负责"给已有的表补列"。
    """
    db_file = tmp_path / "empty.db"
    empty_engine = create_engine("sqlite:///" + db_file.as_posix())
    monkeypatch.setattr(database, "engine", empty_engine)

    assert database.migrate_schema() == []


# ===========================================================================
# 二、仓库层：软删除到底"软"在哪
# ===========================================================================

def test_delete_is_soft_not_hard(make_task):
    """删除之后，数据库里【那行记录还在】，只是打上了时间戳。"""
    task = make_task(title="待删除")

    assert task_repo.delete(task["id"]) is True

    # 用能看见回收站的查询，确认记录还在
    row = task_repo.get_including_deleted(task["id"])
    assert row is not None
    assert row.deleted_at is not None
    assert row.title == "待删除"          # 内容原封不动


def test_deleted_task_disappears_from_normal_queries(make_task):
    """删除之后，正常的查询应该"看不见"它。"""
    task = make_task(title="会被藏起来")
    assert len(task_repo.list_tasks()) == 1

    task_repo.delete(task["id"])

    assert task_repo.get(task["id"]) is None                 # 按 id 查不到
    assert task_repo.list_tasks() == []                      # 列表里没有
    assert task_repo.count_by_status()["total"] == 0         # 统计里没有
    assert task_repo.list_undated() == []                    # 未安排日期面板里没有


def test_double_delete_returns_false(make_task):
    """重复删除同一条，第二次应该返回 False 而不是又"成功"一次。"""
    task = make_task()
    assert task_repo.delete(task["id"]) is True
    assert task_repo.delete(task["id"]) is False


def test_restore_brings_task_back(make_task):
    """还原之后，任务要完全回到删除前的样子。"""
    task = make_task(title="删错了")
    task_repo.delete(task["id"])
    assert task_repo.get(task["id"]) is None

    assert task_repo.restore(task["id"]) is True

    back = task_repo.get(task["id"])
    assert back is not None
    assert back.deleted_at is None
    assert back.title == "删错了"
    assert len(task_repo.list_tasks()) == 1


def test_restore_non_deleted_returns_false(make_task):
    """还原本就没删过的任务 -> False（不能"还原"一条正常任务）。"""
    task = make_task()
    assert task_repo.restore(task["id"]) is False


def test_purge_refuses_normal_task(make_task):
    """
    ★ 安全闸门：彻底删除【只能】作用在回收站里的任务。

    如果这条断言失败了，说明有人可以绕过"先删再清"的流程直接永久删数据 ——
    那是数据丢失事故的入口。
    """
    task = make_task(title="还是正常的")
    assert task_repo.purge(task["id"]) is False

    # 确认它真的没被删掉
    assert task_repo.get(task["id"]) is not None


def test_purge_removes_row_completely(make_task):
    """进了回收站之后再彻底删除，记录就真的从数据库里消失了。"""
    task = make_task(title="彻底不要了")
    task_repo.delete(task["id"])

    assert task_repo.purge(task["id"]) is True

    # 连"看得见回收站"的查询也找不到了
    assert task_repo.get_including_deleted(task["id"]) is None


def test_purge_all_requires_confirm(make_task):
    """清空回收站必须显式确认，不传 confirm 直接抛异常。"""
    make_task()
    task_repo.delete(make_task()["id"])

    try:
        task_repo.purge_all()
        assert False, "没有 confirm 竟然也执行了"
    except ValueError as exc:
        assert "confirm" in str(exc)


def test_purge_all_only_touches_trash(make_task):
    """
    ★ 清空回收站【只删回收站里的】，绝不能碰正常任务。

    这条保证了"即使有人误调 purge_all，损失也被限制在已经删过一次的范围内"。
    """
    keep = make_task(title="正常任务，不能被清掉")
    gone = make_task(title="在回收站里")
    task_repo.delete(gone["id"])

    n = task_repo.purge_all(confirm=True)

    assert n == 1                                          # 只删了 1 条
    assert task_repo.get(keep["id"]) is not None            # 正常任务还在
    assert task_repo.get_including_deleted(gone["id"]) is None


def test_list_deleted_orders_by_delete_time_desc(make_task):
    """
    回收站按【删除时间倒序】—— 最近删的排最前。

    因为"刚删错、想马上捞回来"是最常见的场景。
    """
    a = make_task(title="先删的")
    b = make_task(title="后删的")

    task_repo.delete(a["id"])
    # 手工把删除时间改早一点，制造明确的时间差
    # （不能靠 sleep，那样测试会变慢而且不稳定）
    with get_session() as session:
        row = session.get(Task, a["id"])
        row.deleted_at = "2020-01-01 00:00"

    task_repo.delete(b["id"])

    deleted = task_repo.list_deleted()
    assert [t.title for t in deleted] == ["后删的", "先删的"]


def test_count_deleted(make_task):
    """回收站计数（页面上显示角标用）。"""
    assert task_repo.count_deleted() == 0

    t1 = make_task()
    t2 = make_task()
    task_repo.delete(t1["id"])
    task_repo.delete(t2["id"])

    assert task_repo.count_deleted() == 2

    task_repo.restore(t1["id"])
    assert task_repo.count_deleted() == 1


def test_deleted_task_never_reminds():
    """
    ★ 已删除的任务【绝不能】再弹提醒。

    否则会出现最让人困惑的一幕：任务明明删了，到点还是弹出来。
    """
    past = "2020-01-01 09:00"
    alive = task_repo.create(title="会提醒的", remind_at=past, remind_enabled=True)
    deleted = task_repo.create(title="删了就不该提醒", remind_at=past, remind_enabled=True)

    assert len(task_repo.due_for_reminder()) == 2      # 两条都该提醒

    task_repo.delete(deleted.id)

    due = task_repo.due_for_reminder()
    assert [t.id for t in due] == [alive.id]           # 只剩没删的那条


def test_deleted_task_not_in_calendar(make_task):
    """已删除的任务不该出现在日历的日期范围查询里。"""
    today = timeutil.date_part(timeutil.now_str())
    task = task_repo.create(
        title="日历上的任务",
        due_at=today + " 23:59",
    )

    assert len(task_repo.list_between(today + " 00:00", today + " 23:59")) == 1

    task_repo.delete(task.id)
    assert task_repo.list_between(today + " 00:00", today + " 23:59") == []


# ===========================================================================
# 三、接口层
# ===========================================================================

def test_trash_route_not_shadowed_by_task_id(client):
    """
    ★★ 回归测试：/api/tasks/trash 不能被 /api/tasks/{task_id} 抢先匹配。

    这是一个真实踩过的坑（BUG-006）：
        如果 /{task_id} 定义在 /trash 前面，
        访问 /api/tasks/trash 会先匹配成 task_id="trash"，
        转整数失败 -> 直接返回 422，而不是 200。

    【为什么这条测试特别重要？】
        因为 FastAPI 是按【定义顺序】匹配路由的，而顺序是"看不见"的 ——
        以后有人在文件里挪动了一下函数位置，就可能重新踩坑。
        有了这条测试，挪错了立刻会红。
    """
    resp = client.get("/api/tasks/trash")
    assert resp.status_code == 200, (
        "访问 /api/tasks/trash 得到了 %s —— "
        "很可能是 /{task_id} 被定义在 /trash 前面了（见 BUG-006）: %s"
        % (resp.status_code, resp.text)
    )
    assert resp.json() == {"items": [], "count": 0}


def test_api_delete_moves_to_trash(client, sample_task):
    """接口层的删除 = 移到回收站：列表里没了，回收站里有了。"""
    tid = sample_task["id"]

    resp = client.delete("/api/tasks/%d" % tid)
    assert resp.status_code == 200
    assert "回收站" in resp.json()["message"]

    assert client.get("/api/tasks").json() == []
    assert client.get("/api/tasks/trash").json()["count"] == 1


def test_api_deleted_task_returns_404(client, sample_task):
    """
    已删除的任务，按 id 查应该返回 404 ——
    回收站的内容不能从普通接口泄漏出去。
    """
    tid = sample_task["id"]
    client.delete("/api/tasks/%d" % tid)

    assert client.get("/api/tasks/%d" % tid).status_code == 404
    assert client.patch("/api/tasks/%d" % tid, json={"title": "改改看"}).status_code == 404
    assert client.post("/api/tasks/%d/toggle" % tid).status_code == 404


def test_api_trash_list_contains_deleted_task(client, sample_task):
    """回收站列表要返回完整的任务信息 + 删除时间。"""
    tid = sample_task["id"]
    client.delete("/api/tasks/%d" % tid)

    body = client.get("/api/tasks/trash").json()
    assert body["count"] == 1

    item = body["items"][0]
    assert item["id"] == tid
    assert item["title"] == sample_task["title"]
    assert item["deleted_at"] is not None          # 前端要显示"删除于 ..."


def test_api_restore(client, sample_task):
    """还原接口：回收站里消失，正常列表里回来。"""
    tid = sample_task["id"]
    client.delete("/api/tasks/%d" % tid)

    resp = client.post("/api/tasks/%d/restore" % tid)
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is None

    assert client.get("/api/tasks/trash").json()["count"] == 0
    assert len(client.get("/api/tasks").json()) == 1


def test_api_restore_missing_returns_404(client):
    """还原一个不存在的 id -> 404。"""
    assert client.post("/api/tasks/999999/restore").status_code == 404


def test_api_purge(client, sample_task):
    """彻底删除接口。"""
    tid = sample_task["id"]
    client.delete("/api/tasks/%d" % tid)

    resp = client.delete("/api/tasks/%d/purge" % tid)
    assert resp.status_code == 200
    assert client.get("/api/tasks/trash").json()["count"] == 0


def test_api_purge_normal_task_returns_404(client, sample_task):
    """
    ★ 安全测试：直接彻底删除一条【正常】任务必须被拒绝。

    这正是防止"误把 purge 当普通删除用"的那道闸门。
    """
    tid = sample_task["id"]
    resp = client.delete("/api/tasks/%d/purge" % tid)
    assert resp.status_code == 404

    # 确认任务还在
    assert client.get("/api/tasks/%d" % tid).status_code == 200


def test_api_empty_trash_requires_confirm(client, sample_task):
    """
    ★ 清空回收站必须带 confirm=true。

    不传的时候返回 400，而且【一条都不能删】。
    """
    tid = sample_task["id"]
    client.delete("/api/tasks/%d" % tid)

    resp = client.delete("/api/tasks/trash")
    assert resp.status_code == 400
    assert client.get("/api/tasks/trash").json()["count"] == 1     # 还在

    resp = client.delete("/api/tasks/trash?confirm=true")
    assert resp.status_code == 200
    assert client.get("/api/tasks/trash").json()["count"] == 0     # 现在没了


def test_api_stats_excludes_deleted(client, sample_task):
    """统计数字不能把回收站里的任务算进去。"""
    client.post("/api/tasks", json={"title": "第二条"})
    assert client.get("/api/tasks/stats").json()["total"] == 2

    client.delete("/api/tasks/%d" % sample_task["id"])
    assert client.get("/api/tasks/stats").json()["total"] == 1


def test_api_search_excludes_deleted(client, sample_task):
    """搜索也不能搜出回收站里的任务。"""
    client.delete("/api/tasks/%d" % sample_task["id"])
    assert client.get("/api/tasks", params={"keyword": sample_task["title"]}).json() == []


def test_api_calendar_excludes_deleted(client, future_due_today_clock):
    """
    日历里不显示已删除的任务。

    【★ 这里的 due_at 为什么是算出来的，而不是写死 "23:59"？】
        原来的写法是 `today + " 23:59"` —— 白天跑没问题，
        **23:59 之后再跑就会被"截止时间不能早于当前时间"拒绝**，测试假失败。
        现在改成 `future_due_today_clock()`：它保证给出一个"今天之内、
        且还没到"的时刻；实在没有（23:56 之后）就诚实地 skip。
    """
    try:
        due_at = future_due_today_clock()
    except RuntimeError as exc:
        import pytest
        pytest.skip(str(exc))

    resp = client.post("/api/tasks", json={"title": "日历任务", "due_at": due_at})
    assert resp.status_code == 201, resp.text
    tid = resp.json()["id"]

    month = client.get("/api/calendar/month").json()
    assert month["month_task_count"] >= 1

    client.delete("/api/tasks/%d" % tid)

    # 删掉之后，本月任务数必须归零（回收站里的不该占日历格子）
    assert client.get("/api/calendar/month").json()["month_task_count"] == 0


def test_trash_page_loads(client):
    """
    回收站【页面】能打开。

    接口测过了不代表页面没问题 —— 模板文件漏了、路由忘了注册，
    接口测试全都发现不了（它们根本不碰 HTML）。
    所以页面至少要有一条"能不能打开"的测试。
    """
    resp = client.get("/trash")
    assert resp.status_code == 200
    assert "回收站" in resp.text
    # 页面必须加载它自己的脚本，否则页面上什么都不会发生
    assert "/static/js/trash.js" in resp.text


def test_trash_js_only_uses_existing_dom_ids(client):
    """
    ★ 交叉检查：trash.js 里 getElementById 用到的 id，必须在 trash.html 里真的存在。

    【为什么需要这条测试？】
        因为 JS 里 document.getElementById("xxx") 拿不到元素时，
        返回的是 null —— 【不报错】，直到你调用它的方法才崩。
        典型症状是"页面一片空白，控制台才看到 Cannot read properties of null"。
        本项目因此栽过两次（BUG-008、BUG-015，都是删了 HTML 忘了删 JS 引用）。

    有了这条测试，改 HTML 时手滑删错 id，测试立刻会红。
    """
    import re
    from pathlib import Path

    from app import config

    js = (config.STATIC_DIR / "js" / "trash.js").read_text(encoding="utf-8")
    html = (config.TEMPLATE_DIR / "trash.html").read_text(encoding="utf-8")

    used = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", js))
    declared = set(re.findall(r"id=['\"]([^'\"]+)['\"]", html))

    # ★ 防"假通过"：如果正则写错了，两个集合都会是空的，
    #   那么 missing 也是空的，测试会【毫无意义地通过】。
    #   测试最怕的不是失败，而是"什么都没测到却显示通过"。
    assert used, "没能从 trash.js 里解析出任何 id —— 正则可能写错了"
    assert declared, "没能从 trash.html 里解析出任何 id —— 正则可能写错了"

    missing = used - declared
    assert not missing, "trash.js 引用了 HTML 里不存在的 id: %s" % sorted(missing)
