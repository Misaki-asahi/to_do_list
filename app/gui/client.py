# -*- coding: utf-8 -*-
"""
client.py -- 悬浮窗进程【唯一】的网络出口（v0.3.0 新增）。

【为什么要有这个文件？】

    项目约定（AGENTS.md 第 3 节）：
        "前端所有网络请求必须走 api.js，页面里禁止直接写 fetch"。

    悬浮窗也是一个"前端"，只不过它是画在桌面上的，不是画在浏览器里的。
    所以同样的规矩适用：**窗口代码里一行 urlopen 都不许写**，
    全部集中到这里。好处一模一样：
        1. 以后服务地址变了，只改这一个文件；
        2. 出错处理、超时、令牌校验只写一遍；
        3. 窗口那边调用的 api.get_data() 一眼就知道它在干什么。

【为什么要带令牌？】
    服务端的 /api/floating/* 接口会检查一个随机令牌。
    令牌只存在本机数据库里，窗口启动时从环境变量 TODO_FLOAT_TOKEN 拿到。
    这道闸门不是防"坏人"（本地服务本来就只监听 127.0.0.1），
    而是防"意外"：比如另一个网页刚好猜到了接口路径，
    想通过它把悬浮窗设置改掉或者把窗口关掉 —— 没有令牌就做不成。

【为什么不用 requests 库？】
    因为项目原则是"下载下来就能跑"，能不加依赖就不加。
    标准库的 urllib.request 足够干这件事（本地调用，没有复杂需求）。
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from app import config

from app.core import logging_setup

logger = logging_setup.get_logger("app.gui.client")

# 单次请求的超时（秒）。
# 【为什么给 5 秒？】本地服务正常时是"毫秒级"的。
# 如果它 5 秒还没回话，说明服务已经出问题了，窗口不该一直傻等 ——
# 卡住的界面比报错的界面更让人困惑。
DEFAULT_TIMEOUT = 5


class FloatingApiError(Exception):
    """调用接口失败（网络不通 / 服务返回错误）。"""


class FloatingApi:
    """悬浮窗用的接口封装。"""

    def __init__(self, host=None, port=None, token=None, timeout=DEFAULT_TIMEOUT):
        self.host = host or config.HOST
        self.port = int(port or config.PORT)
        self.token = token if token is not None else os.environ.get("TODO_FLOAT_TOKEN", "")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 底层：统一的请求函数
    # ------------------------------------------------------------------
    @property
    def base(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    def _request(self, path: str, method: str = "GET", body=None):
        """
        发一个请求并返回解析后的 JSON。

        无论失败原因是什么，都统一抛 FloatingApiError ——
        窗口那边只需要 except 一种异常，不用分辨
        "连不上"和"返回 500"的区别（那种区分在日志里会写清楚）。
        """
        url = self.base + path
        data = None
        headers = {"Accept": "application/json"}

        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        # 令牌放在请求头里，而不是网址里 ——
        # 网址会被写进日志和浏览器历史，令牌不该出现在那些地方。
        if self.token:
            headers["X-Float-Token"] = self.token

        request = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # 服务端明确回了一个错误码，把里面的 detail 拿出来（那是中文的原因说明）
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("detail") or ""
                if not isinstance(detail, str):
                    detail = json.dumps(detail, ensure_ascii=False)
            except Exception as exc:                         # noqa: BLE001
                # ★ 不许静默失败（AGENTS.md 3.2 / BUG-044）：至少留一行日志
                logger.debug("这里出错不影响主流程，按可忽略处理：%s", exc, exc_info=True)

            raise FloatingApiError("HTTP %s：%s" % (exc.code, detail or exc.reason))
        except urllib.error.URLError as exc:
            raise FloatingApiError("连接不上服务：%s" % exc.reason)
        except TimeoutError:
            raise FloatingApiError("请求超时（服务没有响应）")

        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise FloatingApiError("服务返回的不是合法 JSON：%s" % exc)

    # ------------------------------------------------------------------
    # 接口清单（窗口用得到的全部）
    # ------------------------------------------------------------------
    def health(self) -> dict:
        """健康检查：窗口启动时用它确认服务已经就绪。"""
        return self._request("/api/health")

    def get_data(self, force: bool = False) -> dict:
        """
        拉取窗口要显示的全部数据（任务分块 + 统计 + 最近提醒）。

        参数 force=True 表示"不管缓存，给我最新的"（用户点了刷新按钮）。
        """
        path = "/api/floating/data" + ("?force=true" if force else "")
        return self._request(path)

    def get_config(self) -> dict:
        """只拉设置（用来发现用户在设置页改了外观）。"""
        return self._request("/api/floating/config")

    def set_config(self, changes: dict) -> dict:
        """改设置（拖动窗口后保存位置、点关闭按钮保存"隐藏"状态）。"""
        return self._request("/api/floating/config", method="PATCH", body=changes)

    def toggle(self, task_id: int) -> dict:
        """勾选 / 取消勾选一条待办。"""
        return self._request("/api/floating/tasks/%d/toggle" % task_id, method="POST")

    def get_task(self, task_id: int) -> dict:
        """
        读一条待办的完整信息（打开编辑弹窗时用）。

        【为什么不用列表里那份数据预填？】
            列表最多是 10 秒前的快照。用它预填再保存，
            会把 10 秒内的改动覆盖掉。打开弹窗时重新拉一次才安全。
        """
        return self._request("/api/floating/tasks/%d" % task_id)

    def update_task(self, task_id: int, changes: dict) -> dict:
        """编辑一条待办。"""
        return self._request("/api/floating/tasks/%d" % task_id,
                             method="PATCH", body=changes)

    def quick_add(self, title: str, priority=None, category=None, remind_at=None,
                  due_at=None, description=None) -> dict:
        r"""
        新建一条待办（窗口底部的"打一句话回车"和「新建待办」弹窗都用它）。

        ★★【v0.4.3：把截止时间 / 描述也一起送过去 —— 修"整个待办消失"】

            【用户报的问题】
                「悬浮窗新建待办，设置提醒日期时日期格式错误会导致整个待办消失」

            【原来的写法为什么会导致"消失"】
                新建被拆成了**两次请求**：
                    ① quick_add(标题, 优先级, 分类, 提醒时间)  -> 建任务
                    ② update_task(新 id, {截止时间, 描述})      -> 再补两个字段

                只要 ② 失败（比如截止时间格式不对），任务其实已经建出来了，
                但用户看到的是报错、弹窗关掉，他会以为"整条没存上"，
                于是重新输一遍 —— 列表里就多出一条重复的。

                更糟的是 ① 失败时（提醒时间格式不对），**任务根本不会创建**，
                前面填的标题、分类、描述全白填。这就是用户说的"整个待办消失"。

            【现在怎么改】
                一次请求把该带的字段全带上（服务端本来就收 TaskCreate 的完整入参）。
                **要么整条建好，要么一条都不建**，不存在"建了一半"的中间状态。
        """
        body = {"title": title}
        if priority is not None:
            body["priority"] = priority
        if category:
            body["category"] = category
        if remind_at:
            body["remind_at"] = remind_at
            body["remind_enabled"] = True
        if due_at:
            body["due_at"] = due_at
        if description:
            body["description"] = description
        return self._request("/api/floating/tasks", method="POST", body=body)

    def snooze(self, task_id: int, minutes=None) -> dict:
        """稍后提醒。"""
        path = "/api/floating/tasks/%d/snooze" % task_id
        if minutes:
            path += "?minutes=%d" % int(minutes)
        return self._request(path, method="POST")

    def delete_task(self, task_id: int) -> dict:
        """删除一条待办（其实是移到回收站，可以还原）。"""
        return self._request("/api/tasks/%d" % task_id, method="DELETE")

    def quit_window(self) -> dict:
        """告诉服务端"我这个窗口要关了"（服务端会记一笔，用户仍可用"立即显示"叫回来）。"""
        return self._request("/api/floating/window/close", method="POST")

    def remind_at(self, choice: str) -> str:
        """
        把快捷选项（"10" / "today" / "tomorrow"）换算成提醒时间字符串。

        【为什么这个换算要问服务端，而不是在窗口里自己算？】
            新建 / 修改待办时，接口会校验"提醒时间不能早于当前时间"。
            如果换算在窗口进程里做、校验在服务进程里做，
            两边时钟只要差几秒，就会出现
            "我明明选了 10 分钟后，却提示时间早于当前"这种莫名其妙的错误。

            换成服务端来算，用的就是服务端自己的钟 ——
            自相矛盾从结构上就不可能发生（这叫"单一事实来源"）。
        """
        return self._request("/api/floating/remind-at?choice=" +
                             urllib.parse.quote(str(choice)))["remind_at"]

