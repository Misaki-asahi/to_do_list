/**
 * list.js -- 待办列表页：列表渲染、筛选搜索、添加、勾选完成、删除。
 *
 * 【编辑功能不在这里，在 edit.js】
 *   因为编辑是"弹窗"形态，和列表的交互模式差别较大，单独一个文件更清晰。
 *   list.js 只负责"点编辑按钮 -> 通知弹窗打开"，具体怎么编辑它不管。
 *
 * 通信方式：自定义事件（见文件末尾的说明）
 *   list.js  发出  "edit:open"（带 id）
 *   edit.js  接收并打开弹窗
 *   edit.js  发出  "tasks:changed"（保存成功后）
 *   list.js  接收并刷新列表
 */
(function () {

  /* ======================================================================
     一、状态
     ====================================================================== */
  const state = {
    status: 'all',    // 'all' | 'todo' | 'done'
    keyword: '',      // 搜索关键字
  };

  /* ======================================================================
     二、页面元素
     ====================================================================== */
  const form = document.getElementById('task-form');
  const elTitle = document.getElementById('f-title');
  const elDesc = document.getElementById('f-desc');
  const elDue = document.getElementById('f-due');
  const elRemind = document.getElementById('f-remind');
  const elPriority = document.getElementById('f-priority');
  const elCategory = document.getElementById('f-category');

  const btnSubmit = document.getElementById('btn-submit');
  const formMsg = document.getElementById('form-msg');

  const tabsBox = document.getElementById('tabs');
  const searchBox = document.getElementById('search');
  const listBox = document.getElementById('task-list');
  const statsBox = document.getElementById('stats');

  const PRIORITY_NAMES = ['低', '中', '高'];

  /* ======================================================================
     三、工具函数
     ====================================================================== */

  /** 转义 HTML 特殊字符，防止 XSS 攻击（原理见 docs/阶段二-03-添加待办.md 3.10） */
  function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** "2026-09-20T08:30" -> "2026-09-20 08:30" */
  function toApiTime(value) {
    if (!value) return null;
    return value.replace('T', ' ').slice(0, 16);
  }

  function showMessage(text, isError) {
    formMsg.textContent = text || '';
    formMsg.className = 'form-msg ' + (isError ? 'error' : 'ok');
  }

  /** 防抖：一连串触发中只在停下来之后执行一次（搜索框用） */
  function debounce(fn, delay) {
    let timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, delay);
    };
  }

  /* ======================================================================
     四、渲染
     ====================================================================== */

  function renderStats(stats) {
    statsBox.textContent = '共 ' + stats.total + ' 条 · 未完成 ' + stats.todo + ' · 已完成 ' + stats.done;
    document.getElementById('count-all').textContent = stats.total;
    document.getElementById('count-todo').textContent = stats.todo;
    document.getElementById('count-done').textContent = stats.done;
  }

  function renderTabs() {
    const buttons = tabsBox.querySelectorAll('.tab');
    for (let i = 0; i < buttons.length; i++) {
      const isActive = buttons[i].getAttribute('data-status') === state.status;
      buttons[i].className = 'tab' + (isActive ? ' active' : '');
    }
  }

  function taskHtml(task) {
    const isDone = task.status === 'done';

    const meta = [];
    if (task.priority > 0) {
      meta.push('<span class="badge badge-p' + task.priority + '">' + PRIORITY_NAMES[task.priority] + '</span>');
    }
    if (task.category) {
      meta.push('<span class="badge badge-cat">' + escapeHtml(task.category) + '</span>');
    }
    if (task.due_at) {
      meta.push('<span class="meta">截止 ' + escapeHtml(task.due_at) + '</span>');
    }
    if (task.remind_at) {
      meta.push('<span class="meta meta-remind">🔔 ' + escapeHtml(task.remind_at) + '</span>');
    }
    if (isDone && task.completed_at) {
      meta.push('<span class="meta meta-done">✓ 完成于 ' + escapeHtml(task.completed_at) + '</span>');
    }
    if (!meta.length) meta.push('<span class="meta">无时间要求</span>');

    return '' +
      '<div class="task-item p' + task.priority + (isDone ? ' done' : '') + '" data-id="' + task.id + '">' +
        '<input type="checkbox" class="task-check" data-action="toggle" data-id="' + task.id + '"' +
               (isDone ? ' checked' : '') + ' />' +
        '<div class="task-main">' +
          '<div class="task-title">' + escapeHtml(task.title) + '</div>' +
          (task.description ? '<div class="task-desc">' + escapeHtml(task.description) + '</div>' : '') +
          '<div class="task-meta">' + meta.join('') + '</div>' +
        '</div>' +
        '<div class="task-side">' +
          '<div class="task-actions">' +
            '<button type="button" class="icon-btn" data-action="edit" data-id="' + task.id + '">编辑</button>' +
            '<button type="button" class="icon-btn danger" data-action="delete" data-id="' + task.id + '">删除</button>' +
          '</div>' +
          '<span class="task-id">#' + task.id + '</span>' +
        '</div>' +
      '</div>';
  }

  function renderList(tasks) {
    if (!tasks.length) {
      let title = '还没有任何待办';
      let hint = '在上面填写标题，点"添加"就能创建第一条。';
      if (state.keyword) {
        title = '没有找到匹配「' + escapeHtml(state.keyword) + '」的待办';
        hint = '换个关键字试试，或者清空搜索框。';
      } else if (state.status === 'done') {
        title = '还没有已完成的待办';
        hint = '完成一条待办后，它会出现在这里。';
      } else if (state.status === 'todo') {
        title = '所有待办都完成啦 🎉';
        hint = '休息一下吧。';
      }
      listBox.innerHTML = '<div class="empty"><div class="empty-icon">📝</div>' +
        '<p>' + title + '</p><p class="muted">' + hint + '</p></div>';
      return;
    }

    listBox.innerHTML = tasks.map(taskHtml).join('');
  }

  /* ======================================================================
     五、和后端交互
     ====================================================================== */

  async function loadTasks() {
    renderTabs();
    try {
      const filters = {};
      if (state.status !== 'all') filters.status = state.status;
      if (state.keyword) filters.keyword = state.keyword;

      const results = await Promise.all([api.listTasks(filters), api.taskStats()]);
      renderList(results[0]);
      renderStats(results[1]);
    } catch (err) {
      listBox.innerHTML = '<div class="empty error">加载失败：' + escapeHtml(err.message) + '</div>';
      statsBox.textContent = '—';
    }
  }

  /* ======================================================================
     六、添加（顶部表单只负责"新增"，编辑走弹窗）
     ====================================================================== */

  async function onSubmit(event) {
    event.preventDefault();   // 不加这行页面会刷新，后面的 JS 全白写

    const title = elTitle.value.trim();
    if (!title) {
      showMessage('请先填写标题', true);
      elTitle.focus();
      return;
    }

    const payload = {
      title: title,
      description: elDesc.value.trim() || null,
      priority: parseInt(elPriority.value, 10),
      due_at: toApiTime(elDue.value),
      remind_at: toApiTime(elRemind.value),
      remind_enabled: Boolean(elRemind.value),
      category: elCategory.value.trim() || null,
    };

    btnSubmit.disabled = true;
    showMessage('正在添加…', false);
    try {
      const created = await api.createTask(payload);
      showMessage('已添加：「' + created.title + '」', false);
      form.reset();
      elTitle.focus();          // 光标回到标题框，方便连续录入
      await loadTasks();
    } catch (err) {
      showMessage(err.message, true);
    } finally {
      btnSubmit.disabled = false;   // 无论成败都恢复按钮
    }
  }

  /* ======================================================================
     七、列表上的操作（事件委托：只在父容器绑一个监听器）
     ====================================================================== */
  async function onListClick(event) {
    const target = event.target;
    const action = target.getAttribute && target.getAttribute('data-action');
    if (!action) return;

    const id = parseInt(target.getAttribute('data-id'), 10);
    if (!id) return;

    if (action === 'toggle') {
      try {
        await api.toggleTask(id);
        await loadTasks();
      } catch (err) {
        showMessage(err.message, true);
        await loadTasks();     // 失败也要刷新，让复选框回到真实状态
      }
      return;
    }

    if (action === 'edit') {
      // 【不在这里实现编辑】只把"要编辑哪一条"广播出去，由 edit.js 打开弹窗。
      // 这样列表逻辑和弹窗逻辑互不依赖。
      window.dispatchEvent(new CustomEvent('edit:open', { detail: { id: id } }));
      return;
    }

    if (action === 'delete') {
      const row = target.closest('.task-item');
      const titleEl = row ? row.querySelector('.task-title') : null;
      const name = titleEl ? titleEl.textContent : ('#' + id);
      // 【这段话很重要，别说错】
      // 以前这里写的是「删除后无法恢复」—— 那是硬删除时代的说法。
      // 现在删除只是移到回收站，随时能还原。
      // 如果还沿用旧文案，用户会以为数据没了，白白紧张一场；
      // 更糟的是，他可能因此不敢删任何东西，回收站就白做了。
      if (!window.confirm(
        '删除「' + name + '」？\n\n' +
        '它会先被放进回收站，之后可以还原。\n' +
        '（想彻底删除，请到回收站页面操作。）'
      )) return;

      try {
        await api.deleteTask(id);
        showMessage('已移到回收站（可到回收站页面还原）', false);
        await loadTasks();
      } catch (err) {
        showMessage(err.message, true);
      }
    }
  }

  /* ======================================================================
     八、绑定事件并启动
     ====================================================================== */

  form.addEventListener('submit', onSubmit);

  tabsBox.addEventListener('click', function (event) {
    const btn = event.target.closest('.tab');
    if (!btn) return;
    const value = btn.getAttribute('data-status');
    state.status = (value === 'all') ? 'all' : value;
    loadTasks();
  });

  searchBox.addEventListener('input', debounce(function () {
    state.keyword = searchBox.value.trim();
    loadTasks();
  }, 300));

  listBox.addEventListener('click', onListClick);

  // 编辑弹窗保存成功后，edit.js 会广播这个事件，我们刷新列表
  window.addEventListener('tasks:changed', loadTasks);

  loadTasks();
})();
