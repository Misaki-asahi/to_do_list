/**
 * api.js -- 前端与后端通信的【唯一出口】。
 *
 * 设计思路（很重要）：
 *   页面里任何地方都不允许直接写 fetch(...)。
 *   全部改成调用 api.xxx()，好处是：
 *     1. 以后后端地址变了（比如部署到网上），只改下面这一个常量；
 *     2. 错误处理、请求头设置只写一次，不会处处重复；
 *     3. 出问题时只需要检查这一个文件。
 */

// 现在前端和后端在同一个地址下，所以留空字符串就行。
const API_BASE = '';

/* ==========================================================================
   把后端返回的错误，翻译成用户看得懂的中文
   ==========================================================================
   FastAPI 的报错有两种格式，都要处理：

     400 Bad Request（业务规则不通过）：
         {"detail": "提醒时间不能早于当前时间"}

     422 Unprocessable Entity（格式校验不通过）：
         {"detail": [{"loc": ["body","title"], "msg": "Field required", ...}]}

   其中 422 的 msg 是英文的，直接给用户看很不友好，所以下面做了翻译。
   ========================================================================== */

// 常见英文错误 -> 中文
const MESSAGE_MAP = {
  'Field required': '必填项，不能为空',
  'Input should be a valid integer': '必须是整数',
  'Input should be a valid string': '必须是文本',
  'Input should be a valid boolean': '必须是 true 或 false',
};

function humanizeMessage(msg) {
  if (!msg) return '格式不正确';

  // Pydantic 会在自定义校验器的报错前面加 "Value error, "，去掉它
  msg = msg.replace(/^Value error,\s*/, '');

  // 已经是我们自己写的中文，直接返回
  if (/[\u4e00-\u9fa5]/.test(msg)) return msg;

  // 查表
  if (MESSAGE_MAP[msg]) return MESSAGE_MAP[msg];

  // 长度限制类："String should have at most 200 characters"
  let m = msg.match(/at most (\d+) character/);
  if (m) return '长度不能超过 ' + m[1] + ' 个字符';
  m = msg.match(/at least (\d+) character/);
  if (m) return '长度不能少于 ' + m[1] + ' 个字符';
  m = msg.match(/less than or equal to (\d+)/);
  if (m) return '数值不能大于 ' + m[1];
  m = msg.match(/greater than or equal to (\d+)/);
  if (m) return '数值不能小于 ' + m[1];

  return msg;
}

function extractErrorMessage(text, status) {
  try {
    const data = JSON.parse(text);

    // 400：detail 是一个字符串
    if (typeof data.detail === 'string') return data.detail;

    // 422：detail 是一个数组
    if (Array.isArray(data.detail) && data.detail.length) {
      return data.detail.map(function (item) {
        const loc = item.loc || [];
        // loc 形如 ["body", "title"]，第一项是位置，后面才是字段名
        const field = loc.length > 1 ? loc.slice(1).join('.') : '参数';
        return field + '：' + humanizeMessage(item.msg);
      }).join('；');
    }
  } catch (e) {
    // 不是 JSON（比如服务器内部错误返回了 HTML），忽略，走下面的兜底
  }
  return text || ('请求失败，HTTP ' + status);
}

/**
 * 统一的请求函数。
 * @param {string} path    接口路径，例如 '/api/tasks'
 * @param {object} options { method: 'POST', body: {...} }
 * @returns {Promise<any>} 后端返回的 JSON 数据
 */
async function request(path, options) {
  options = options || {};

  const response = await fetch(API_BASE + path, {
    method: options.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    // 只有需要发送数据时才带 body；GET 请求不能带 body，否则浏览器报错
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  // fetch 有个坑：即使服务器返回 404 / 500，它也不会抛异常，
  // 所以必须自己检查 response.ok，否则错误会被悄悄吞掉。
  if (!response.ok) {
    const text = await response.text().catch(function () { return ''; });
    throw new Error(extractErrorMessage(text, response.status));
  }

  // 204 表示"成功但没有内容"，例如删除接口
  if (response.status === 204) return null;

  return await response.json();
}

/* ==========================================================================
   接口清单
   所有接口集中登记在这里，一眼就能看明白"这个项目有哪些功能"
   ========================================================================== */

const api = {
  // ---- 系统 ----
  health: function () {
    return request('/api/health');
  },

  // ---- 待办 ----
  listTasks: function (filters) {
    filters = filters || {};
    const parts = [];
    if (filters.status) parts.push('status=' + encodeURIComponent(filters.status));
    if (filters.keyword) parts.push('keyword=' + encodeURIComponent(filters.keyword));
    const query = parts.length ? ('?' + parts.join('&')) : '';
    return request('/api/tasks' + query);
  },

  createTask: function (data) {
    return request('/api/tasks', { method: 'POST', body: data });
  },

  // 按 id 查一条（编辑时用它拉取最新数据）
  getTask: function (id) {
    return request('/api/tasks/' + id);
  },

  // 修改：只传要改的字段。
  // 想清空某个字段就显式传 null，例如 updateTask(3, { remind_at: null })
  updateTask: function (id, changes) {
    return request('/api/tasks/' + id, { method: 'PATCH', body: changes });
  },

  // 切换完成 / 未完成（不需要前端判断当前状态）
  toggleTask: function (id) {
    return request('/api/tasks/' + id + '/toggle', { method: 'POST' });
  },

  deleteTask: function (id) {
    return request('/api/tasks/' + id, { method: 'DELETE' });
  },

  taskStats: function () {
    return request('/api/tasks/stats');
  },

  // ---- 提醒 ----
  // 取"已经弹过但页面还没显示"的提醒。
  // 【注意】它是只读快照，不会清空 —— 要调用 ackReminder() 才移除。
  // 这样页面刷新、请求丢失，提示都不会凭空消失。
  reminderInbox: function () {
    return request('/api/reminders/inbox');
  },

  // 确认"这条提醒我已经处理了"，从收件箱移除
  ackReminder: function (id) {
    return request('/api/reminders/' + id + '/ack', { method: 'POST' });
  },

  // 稍后提醒：把时间往后推 N 分钟
  snoozeReminder: function (id, minutes) {
    const qs = minutes ? ('?minutes=' + minutes) : '';
    return request('/api/reminders/' + id + '/snooze' + qs, { method: 'POST' });
  },

  reminderStats: function () {
    return request('/api/reminders/stats');
  },

  // 发一条测试通知（设置页用）
  testNotification: function () {
    return request('/api/reminders/test', { method: 'POST' });
  },

  // 后台提醒线程状态（排查"为什么不提醒"时用）
  schedulerStatus: function () {
    return request('/api/reminders/scheduler');
  },

  // ---- 日历 ----
  calendarMonth: function (year, month) {
    const parts = [];
    if (year) parts.push('year=' + encodeURIComponent(year));
    if (month) parts.push('month=' + encodeURIComponent(month));
    const qs = parts.length ? ('?' + parts.join('&')) : '';
    return request('/api/calendar/month' + qs);
  },

  calendarDay: function (day) {
    return request('/api/calendar/day?day=' + encodeURIComponent(day));
  },

  // ---- 设置 ----
  getSettings: function () {
    return request('/api/settings');
  },

  setAutostart: function (enabled, openBrowser) {
    return request('/api/settings/autostart', {
      method: 'POST',
      body: { enabled: enabled, open_browser: openBrowser },
    });
  },

  setPreference: function (key, value) {
    return request('/api/settings/preference', {
      method: 'POST',
      body: { key: key, value: value },
    });
  },

  getLogs: function (lines) {
    return request('/api/settings/logs' + (lines ? ('?lines=' + lines) : ''));
  },

  shutdown: function () {
    return request('/api/settings/shutdown', { method: 'POST' });
  },

  registerNotifyApp: function () {
    return request('/api/settings/register-notify-app', { method: 'POST' });
  },

  makeBackup: function () {
    return request('/api/settings/backup', { method: 'POST' });
  },

  openFolder: function (target) {
    return request('/api/settings/open-folder', {
      method: 'POST',
      body: { target: target },
    });
  },

  // ---- 回收站 ----
  // 删除 = 移到回收站，所以这里提供「捞回来」和「彻底清掉」两条出路。
  // 所有请求依然只从这一个文件出去（项目约定：页面里禁止直接写 fetch）。

  // 回收站列表（只读，不会改动任何数据）
  trashList: function () {
    return request('/api/tasks/trash');
  },

  // 从回收站还原一条
  restoreTask: function (id) {
    return request('/api/tasks/' + id + '/restore', { method: 'POST' });
  },

  // 彻底删除一条（不可恢复）
  purgeTask: function (id) {
    return request('/api/tasks/' + id + '/purge', { method: 'DELETE' });
  },

  // 清空回收站。注意 confirm=true 是必传的 —— 少传后端会返回 400
  emptyTrash: function () {
    return request('/api/tasks/trash?confirm=true', { method: 'DELETE' });
  },

  /* ======================================================================
     桌面悬浮窗（v0.3.0 新增）

     【这些接口是给谁用的？】
       给【设置页】用的。悬浮窗自己那个进程走的是另一条路
       （app/gui/client.py），因为它是桌面程序，不在浏览器里。

     【为什么改设置要用 PATCH？】
       因为它只改你传过去的那几个字段。
       如果用"整份替换"的思路，那么悬浮窗上"拖动一次位置"
       就可能把用户的字号、颜色、分块方式全都重置回默认值。
       —— 这和列表页"修改待办"用 PATCH 是同一个道理。
     ====================================================================== */

  // 悬浮窗的全部设置与运行状态
  getFloating: function () {
    return request('/api/floating');
  },

  // 改设置（只传要改的字段，例如 { opacity: 0.8 }）
  setFloating: function (changes) {
    return request('/api/floating/config', { method: 'PATCH', body: changes });
  },

  // 一键套用配色主题（名字见 getFloating() 返回的 themes）
  setFloatingTheme: function (name) {
    return request('/api/floating/theme?name=' + encodeURIComponent(name),
                   { method: 'POST' });
  },

  // 恢复默认设置（只重置外观和位置，不会碰任何待办数据）
  resetFloating: function () {
    return request('/api/floating/reset', { method: 'POST' });
  },

  // 立即显示（把隐藏了的窗口叫回来）
  showFloating: function () {
    return request('/api/floating/window/show', { method: 'POST' });
  },

  // 重启窗口（界面卡住时用）
  restartFloating: function () {
    return request('/api/floating/window/restart', { method: 'POST' });
  },

  // 隐藏窗口（等价于点窗口右上角的 ✕）
  hideFloating: function () {
    return request('/api/floating/window/close', { method: 'POST' });
  },
};

// 挂到 window 上，其它 JS 文件才能直接用 api.xxx()
window.api = api;

// "信号灯"：只要能执行到这一行，就说明 api.js 被成功加载了
window.APP_STATIC_READY = true;
