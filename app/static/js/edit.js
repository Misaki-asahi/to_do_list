/**
 * edit.js -- 编辑待办的弹窗。
 *
 * 【为什么编辑用弹窗，而添加用内嵌表单？】
 *   添加是【高频】操作：你可能一口气录入五条，表单一直摆在眼前最快。
 *   编辑是【低频、需要专注】的操作：你要看清楚这条待办原来的内容再改，
 *   弹窗能把它从周围一堆信息里"拎出来"，不容易看错、也不容易被忽略。
 *   两者用不同形态，正好各取所长。
 *
 *   （这个改动来自用户反馈：就地填回顶部表单"不够突出，不好看清"）
 */
(function () {

  // ---------------- 元素 ----------------
  const modal = document.getElementById('edit-modal');
  const form = document.getElementById('edit-form');
  const heading = document.getElementById('edit-heading');

  const elTitle = document.getElementById('e-title');
  const elDesc = document.getElementById('e-desc');
  const elDue = document.getElementById('e-due');
  const elRemind = document.getElementById('e-remind');
  const elPriority = document.getElementById('e-priority');
  const elCategory = document.getElementById('e-category');

  const btnSubmit = document.getElementById('edit-submit');
  const btnCancel = document.getElementById('edit-cancel');
  const msgBox = document.getElementById('edit-msg');

  // ---------------- 状态 ----------------
  let currentId = null;       // 正在编辑哪一条
  let original = '';          // 打开时的原始内容（用于判断"有没有改过"）

  function showMessage(text, isError) {
    msgBox.textContent = text || '';
    msgBox.className = 'form-msg ' + (isError ? 'error' : 'ok');
  }

  /** 后端格式 -> 浏览器控件格式： "2026-09-20 08:30" -> "2026-09-20T08:30" */
  function toInputTime(value) {
    if (!value) return '';
    return value.replace(' ', 'T').slice(0, 16);
  }

  /** 浏览器控件格式 -> 后端格式 */
  function toApiTime(value) {
    if (!value) return null;
    return value.replace('T', ' ').slice(0, 16);
  }

  /**
   * 把表单里的内容抓成一个对象。
   * 用来做两件事：① 提交给后端；② 和打开时的快照对比，判断有没有改过。
   */
  function readForm() {
    return {
      title: elTitle.value.trim(),
      description: elDesc.value.trim(),
      due_at: elDue.value,
      remind_at: elRemind.value,
      priority: elPriority.value,
      category: elCategory.value.trim(),
    };
  }

  /* ======================================================================
     一、打开
     ====================================================================== */

  async function open(id) {
    try {
      // 从后端拉最新的数据来填充。
      // 【为什么不直接用列表里的数据？】
      //   列表可能是几分钟前加载的，别人（或另一个标签页）可能已经改过了。
      //   编辑前重新取一次，避免"用旧数据覆盖掉新数据"。
      const task = await api.getTask(id);

      currentId = id;
      heading.textContent = '编辑待办  #' + task.id;

      elTitle.value = task.title || '';
      elDesc.value = task.description || '';
      elDue.value = toInputTime(task.due_at);
      elRemind.value = toInputTime(task.remind_at);
      elPriority.value = String(task.priority);
      elCategory.value = task.category || '';

      original = JSON.stringify(readForm());   // 记下原始状态，用于"未保存提醒"

      showMessage('', false);
      modal.hidden = false;
      elTitle.focus();
      elTitle.select();                        // 全选标题，方便直接改写
    } catch (err) {
      // 打不开弹窗时，用页面上的提示告诉用户（列表那边也会刷新）
      window.alert('打不开编辑窗口：' + err.message);
    }
  }

  /* ======================================================================
     二、关闭（带"未保存修改"保护）
     ====================================================================== */

  function isDirty() {
    return JSON.stringify(readForm()) !== original;
  }

  function close(force) {
    // force=true 表示"不用问了，直接关"（比如保存成功之后）
    if (!force && isDirty()) {
      const ok = window.confirm('这条待办有修改还没保存，确定要放弃吗？');
      if (!ok) return false;
    }
    modal.hidden = true;
    currentId = null;
    original = '';
    return true;
  }

  /* ======================================================================
     三、保存
     ====================================================================== */

  async function onSubmit(event) {
    event.preventDefault();
    if (currentId === null) return;

    const data = readForm();

    if (!data.title) {
      showMessage('标题不能为空', true);
      elTitle.focus();
      return;
    }

    // 只提交【真正需要改】的字段。
    // 这不只是为了省流量 —— 后端用 PATCH 语义，
    // 传了 null 就表示"清空这个字段"，所以没改的字段干脆不传更安全。
    const payload = { title: data.title };
    if (data.description !== '') payload.description = data.description;
    else payload.description = null;

    payload.due_at = toApiTime(data.due_at);         // 没填就是 null（= 清空）
    payload.remind_at = toApiTime(data.remind_at);
    payload.remind_enabled = Boolean(data.remind_at);
    payload.priority = parseInt(data.priority, 10);
    payload.category = data.category || null;

    btnSubmit.disabled = true;
    showMessage('正在保存…', false);

    try {
      await api.updateTask(currentId, payload);
      close(true);                       // 保存成功，直接关掉
      window.dispatchEvent(new CustomEvent('tasks:changed'));   // 通知列表刷新
    } catch (err) {
      showMessage(err.message, true);
    } finally {
      btnSubmit.disabled = false;        // 无论成败都恢复按钮
    }
  }

  /* ======================================================================
     四、绑定事件
     ====================================================================== */

  form.addEventListener('submit', onSubmit);

  btnCancel.addEventListener('click', function () { close(false); });

  // 点遮罩空白处 = 取消
  modal.addEventListener('click', function (event) {
    if (event.target === modal) close(false);
  });

  // 按 Esc = 取消
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.hidden) {
      close(false);
    }
  });

  // 接收 list.js 的"要编辑这一条"广播
  window.addEventListener('edit:open', function (event) {
    if (event.detail && event.detail.id) open(event.detail.id);
  });

  // 暴露给别的文件用（目前主要是方便调试）
  window.EditModal = { open: open, close: close };
})();
