/* ==========================================================================
   trash.js -- 回收站页面

   【这个页面存在的意义】
       以前删除是"真的删掉"，按下去就没了。现在删除 = 移到回收站，
       所以需要一个地方能看见它们、并且能把误删的捞回来。
       —— 这是用一次真实的数据丢失事故换来的功能（见 docs 的 BUG-011）。

   【为什么这个文件不用 innerHTML 拼字符串？】
       待办标题是用户自己输入的，可能包含 <script> 之类的内容。
       用 innerHTML 就有 XSS（跨站脚本）风险 —— 即使加了 escapeHtml，
       也要靠"记得正确转义"来保证安全，属于"靠自觉"。

       这里改用 DOM API（createElement + textContent）：
       textContent 永远只当纯文本处理，浏览器【不可能】把它解释成标签。
       属于"从原理上做不到"，而不是"小心一点就不会出错"。
       —— 这也正好是 docs/待优化与已知问题.md 里 OPT-008 想做的事。
   ========================================================================== */

(function () {
  'use strict';

  var listEl = document.getElementById('trash-list');
  var countEl = document.getElementById('trash-count');
  var msgEl = document.getElementById('trash-msg');
  var btnRefresh = document.getElementById('btn-refresh');
  var btnEmpty = document.getElementById('btn-empty');

  /* ------------------------------------------------------------------
     小工具
     ------------------------------------------------------------------ */

  function showMsg(text) {
    msgEl.textContent = text || '';
  }

  // 统一的错误提示。错误信息里会把后端返回的原因带上，方便排查。
  function showError(err) {
    showMsg('操作失败：' + (err && err.message ? err.message : String(err)));
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* ------------------------------------------------------------------
     渲染
     ------------------------------------------------------------------ */

  function buildRow(task) {
    var item = el('div', 'task-item');

    // ---- 左侧：标题 + 描述 + 删除时间 ----
    var main = el('div', 'task-main');
    main.appendChild(el('div', 'task-title', task.title));

    if (task.description) {
      main.appendChild(el('div', 'task-desc', task.description));
    }

    var meta = el('div', 'task-meta');
    meta.appendChild(el('span', 'meta', '删除于 ' + (task.deleted_at || '未知时间')));
    if (task.category) {
      meta.appendChild(el('span', 'badge badge-cat', task.category));
    }
    main.appendChild(meta);

    item.appendChild(main);

    // ---- 右侧：两个操作按钮 ----
    var side = el('div', 'task-side');
    var actions = el('div', 'task-actions');

    var btnRestore = el('button', 'btn btn-ghost btn-sm', '还原');
    btnRestore.type = 'button';
    btnRestore.addEventListener('click', function () {
      doRestore(task);
    });

    var btnPurge = el('button', 'btn btn-danger btn-sm', '彻底删除');
    btnPurge.type = 'button';
    btnPurge.addEventListener('click', function () {
      doPurge(task);
    });

    actions.appendChild(btnRestore);
    actions.appendChild(btnPurge);
    side.appendChild(actions);
    item.appendChild(side);

    return item;
  }

  function render(items) {
    listEl.textContent = '';                 // 清空（比 innerHTML='' 更安全）
    countEl.textContent = String(items.length);

    // 空状态
    if (!items.length) {
      var empty = el('div', 'empty');
      empty.appendChild(el('div', 'empty-icon', '🗑'));
      empty.appendChild(el('p', null, '回收站是空的'));
      empty.appendChild(el('p', 'muted', '你删掉的任务会出现在这里，不会立刻消失。'));
      listEl.appendChild(empty);
      return;
    }

    for (var i = 0; i < items.length; i++) {
      listEl.appendChild(buildRow(items[i]));
    }
  }

  /* ------------------------------------------------------------------
     数据加载
     ------------------------------------------------------------------ */

  async function load() {
    try {
      var data = await api.trashList();
      render(data.items || []);
      showMsg('');
    } catch (err) {
      showError(err);
      listEl.textContent = '';
      listEl.appendChild(el('div', 'placeholder', '加载失败，请点「刷新」重试'));
    }
  }

  /* ------------------------------------------------------------------
     三个操作
     ------------------------------------------------------------------ */

  async function doRestore(task) {
    try {
      await api.restoreTask(task.id);
      showMsg('已还原「' + task.title + '」');
      await load();
    } catch (err) {
      showError(err);
    }
  }

  async function doPurge(task) {
    // 彻底删除是不可恢复的，所以必须二次确认。
    // 确认框里把标题写出来，避免"手滑点错行"。
    var ok = window.confirm(
      '彻底删除「' + task.title + '」？\n\n' +
      '这一步【无法撤销】，数据会真的消失。\n' +
      '如果只是不想看见它，可以用「还原」把它放回列表。'
    );
    if (!ok) return;

    try {
      await api.purgeTask(task.id);
      showMsg('已彻底删除「' + task.title + '」，无法恢复');
      await load();
    } catch (err) {
      showError(err);
    }
  }

  async function doEmpty() {
    var count = countEl.textContent;
    var ok = window.confirm(
      '清空回收站里的全部 ' + count + ' 条任务？\n\n' +
      '这一步【无法撤销】。清空后这些数据就真的找不回来了。'
    );
    if (!ok) return;

    try {
      var res = await api.emptyTrash();
      showMsg(res && res.message ? res.message : '回收站已清空');
      await load();
    } catch (err) {
      showError(err);
    }
  }

  /* ------------------------------------------------------------------
     绑定与启动
     ------------------------------------------------------------------ */

  btnRefresh.addEventListener('click', function () {
    showMsg('正在刷新…');
    load();
  });

  btnEmpty.addEventListener('click', doEmpty);

  load();
})();
