/**
 * reminder.js -- 右下角的提醒卡片。
 *
 * 【为什么改成右下角常驻卡片，而不是居中弹窗？】
 *   用户反馈："提示的优先级不高，能不能做成系统级的，不点取消就一直挂在右下角"
 *
 *   居中弹窗的问题：
 *     1. 挡住页面内容，打断感太强；
 *     2. 一关掉就没了，容易漏看；
 *     3. 不和系统通知的位置一致。
 *
 * 【现在的三层提醒】
 *   ① 后端发的 Windows 系统通知 —— 右下角，不点掉不会消失（哪怕浏览器关着）
 *   ② 本文件的页面内卡片     —— 右下角，不点掉也不会消失，并且【带操作按钮】
 *   ③ 提示音                 —— 只在系统通知不可用时才播，避免响两次
 *
 * 【为什么卡片不能自动消失？】
 *   提醒的价值在于"被处理"。自动消失的提示，用户一走神就错过了。
 *   而这条待办的时间已经过了，错过就是真的错过了。
 */
(function () {

  const UI_POLL_MS = 5000;    // 页面查询收件箱的频率（后端每 20 秒扫描一次）

  const stack = document.getElementById('toast-stack');

  const shown = {};           // {taskId: true} 已经在页面上的提醒，避免重复插入
  let checking = false;
  let audioCtx = null;
  // 用户可以在设置页关掉页面提示音（默认开启）。
  // 【为什么不再用"系统通知是否可用"来判断？】
  //   因为"系统支持"不等于"通知真的弹出来了" —— 实测发现系统通知
  //   被 Windows 静默丢弃时，页面以为"系统会响"而保持安静，结果完全没声音。
  let soundEnabled = true;

  function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ======================================================================
     一、提示音（用 Web Audio 现场合成，不需要音频文件）
     ====================================================================== */

  function getAudioContext() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!audioCtx) audioCtx = new Ctx();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      return audioCtx;
    } catch (e) {
      return null;
    }
  }

  // 用户第一次点击页面时"解锁"音频（浏览器自动播放策略要求）
  document.addEventListener('click', getAudioContext);

  function playChime() {
    const ctx = getAudioContext();
    if (!ctx) return;
    try {
      const start = ctx.currentTime;
      [[880, 0], [1174.66, 0.18]].forEach(function (note) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = note[0];
        gain.gain.setValueAtTime(0.0001, start + note[1]);
        gain.gain.exponentialRampToValueAtTime(0.22, start + note[1] + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + note[1] + 0.45);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start + note[1]);
        osc.stop(start + note[1] + 0.5);
      });
    } catch (e) {
      // 播放失败不能影响提醒本身
    }
  }

  /* ======================================================================
     二、渲染一张提醒卡片
     ====================================================================== */

  function cardHtml(task) {
    const lines = [];
    if (task.remind_at) lines.push('提醒时间：' + escapeHtml(task.remind_at));
    if (task.due_at) lines.push('截止时间：' + escapeHtml(task.due_at));
    if (task.category) lines.push('分类：' + escapeHtml(task.category));

    return '' +
      '<div class="toast-item" data-id="' + task.id + '">' +
        '<div class="toast-head">' +
          '<span class="toast-icon">🔔</span>' +
          '<span class="toast-heading">待办提醒</span>' +
          '<button class="toast-close" type="button" data-action="close" ' +
                  'data-id="' + task.id + '" title="关闭这条提醒">×</button>' +
        '</div>' +
        '<div class="toast-title">' + escapeHtml(task.title) + '</div>' +
        (task.description ? '<div class="toast-desc">' + escapeHtml(task.description) + '</div>' : '') +
        '<div class="toast-meta">' + lines.join('<br>') + '</div>' +
        '<div class="toast-actions">' +
          '<button class="btn btn-sm" type="button" data-action="done" data-id="' + task.id + '">标记完成</button>' +
          '<button class="btn btn-ghost btn-sm" type="button" data-action="snooze" data-id="' + task.id + '">稍后提醒</button>' +
        '</div>' +
      '</div>';
  }

  function addCard(task) {
    if (shown[task.id]) return;          // 已经在页面上了，不重复插入
    shown[task.id] = true;
    stack.insertAdjacentHTML('afterbegin', cardHtml(task));
    stack.hidden = false;
  }

  function removeCard(id) {
    delete shown[id];
    const el = stack.querySelector('.toast-item[data-id="' + id + '"]');
    if (el) el.remove();
    if (!stack.children.length) stack.hidden = true;
  }

  /* ======================================================================
     三、轮询收件箱
     ====================================================================== */

  async function poll() {
    if (checking) return;      // 上一次还没回来就跳过，避免请求堆积
    checking = true;
    try {
      const data = await api.reminderInbox();
      soundEnabled = data.sound_enabled !== false;

      const items = data.items || [];
      let added = 0;
      items.forEach(function (task) {
        if (!shown[task.id]) {
          addCard(task);
          added += 1;
        }
      });

      // 只在"真的新来了提醒"时响 —— 不然每 5 秒轮询一次会一直响
      if (added && soundEnabled) playChime();
    } catch (err) {
      // 静默失败：提醒轮询不该因为一次网络抖动就弹错误框打扰用户
    } finally {
      checking = false;
    }
  }

  /* ======================================================================
     四、三个操作
     ====================================================================== */

  async function onAction(action, id) {
    try {
      if (action === 'done') {
        await api.toggleTask(id);
        window.dispatchEvent(new CustomEvent('tasks:changed'));
      } else if (action === 'snooze') {
        await api.snoozeReminder(id);
        window.dispatchEvent(new CustomEvent('tasks:changed'));
      }
      // 无论"完成 / 稍后 / 关闭"，都要告诉后端"这条我处理了"，
      // 否则收件箱里一直挂着它，下次轮询又会冒出来
      await api.ackReminder(id);
    } catch (err) {
      // 出错也要把卡片收掉，不能把用户卡在一个关不掉的提示上
    }
    removeCard(id);
  }

  stack.addEventListener('click', function (event) {
    const btn = event.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = parseInt(btn.getAttribute('data-id'), 10);
    if (id) onAction(action, id);
  });

  /* ======================================================================
     五、启动
     ====================================================================== */

  poll();
  setInterval(poll, UI_POLL_MS);
})();
