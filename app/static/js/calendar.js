/**
 * calendar.js -- 日历视图的交互逻辑。
 *
 * 【职责划分】
 *   后端（calendar_service.py）：算好"哪天是星期几、要补几格"，
 *                                返回一个已经排好版的二维数组。
 *   前端（本文件）              ：把网格画出来 + 处理点击。
 *
 *   为什么这样分？因为日期计算里的边界情况（跨年、闰年、月份天数）很容易写错，
 *   Python 的 datetime 处理这些是强项，放在后端算完再给前端，出错概率小得多。
 */
(function () {

  // ---------------- 状态 ----------------
  const state = {
    year: null,        // 当前显示哪一年；null 表示"让后端决定（本月）"
    month: null,
    selectedDate: null, // 当前选中的日期 "YYYY-MM-DD"
    dayInfo: null,      // 选中日期的详情（含 is_past 等）
  };

  // ---------------- 元素 ----------------
  const elTitle = document.getElementById('cal-title');
  const elWeekdays = document.getElementById('cal-weekdays');
  const elGrid = document.getElementById('cal-grid');
  const elStat = document.getElementById('cal-stat');

  const btnPrev = document.getElementById('cal-prev');
  const btnNext = document.getElementById('cal-next');
  const btnToday = document.getElementById('cal-today');

  const elDayTitle = document.getElementById('day-title');
  const elDayHint = document.getElementById('day-hint');
  const elDayTasks = document.getElementById('day-tasks');

  const quickForm = document.getElementById('quick-form');
  const qTitle = document.getElementById('q-title');
  const qTime = document.getElementById('q-time');
  const qSubmit = document.getElementById('q-submit');
  const qMsg = document.getElementById('q-msg');

  const elUndated = document.getElementById('undated-list');

  function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function showMsg(el, text, isError) {
    el.textContent = text || '';
    el.className = 'form-msg ' + (isError ? 'error' : 'ok');
  }

  /* ======================================================================
     一、渲染月历
     ====================================================================== */

  function renderWeekdays(names) {
    // ["一","二",...,"日"] -> 一排表头格子
    elWeekdays.innerHTML = names.map(function (n) {
      return '<div class="cal-weekday">' + escapeHtml(n) + '</div>';
    }).join('');
  }

  function cellHtml(cell) {
    // 一个日期格子。类名用来控制样式：
    //   in-month 本月 / out-month 上月的尾巴或下月的开头
    //   is-today 今天 / is-past 已经过去 / is-selected 当前选中
    const classes = ['cal-cell'];
    classes.push(cell.in_month ? 'in-month' : 'out-month');
    if (cell.is_today) classes.push('is-today');
    if (cell.is_past) classes.push('is-past');
    if (cell.date === state.selectedDate) classes.push('is-selected');
    if (cell.tasks.length) classes.push('has-tasks');

    // 每天最多在格子里显示 3 条，超出的用 "+N" 表示
    const shown = cell.tasks.slice(0, 3);
    const chips = shown.map(function (t) {
      return '<div class="cal-chip' + (t.status === 'done' ? ' done' : '') +
             (t.priority === 2 ? ' p2' : '') + '" title="' + escapeHtml(t.title) + '">' +
             escapeHtml(t.title) + '</div>';
    }).join('');
    const more = cell.tasks.length > 3
      ? '<div class="cal-more">+' + (cell.tasks.length - 3) + ' 条</div>'
      : '';

    return '<div class="' + classes.join(' ') + '" data-date="' + cell.date + '">' +
             '<div class="cal-daynum">' + cell.day + '</div>' +
             '<div class="cal-chips">' + chips + more + '</div>' +
           '</div>';
  }

  function renderMonth(data) {
    state.year = data.year;
    state.month = data.month;

    elTitle.textContent = data.title;
    elStat.textContent = '本月 ' + data.month_task_count + ' 条 · 未排期 ' + data.undated.length + ' 条';
    renderWeekdays(data.weekday_names);

    elGrid.innerHTML = data.weeks.map(function (week) {
      return week.map(cellHtml).join('');
    }).join('');

    renderUndated(data.undated);
  }

  /* ======================================================================
     二、渲染"未安排日期"面板
     ====================================================================== */

  function taskRowHtml(task) {
    return '' +
      '<div class="day-task' + (task.status === 'done' ? ' done' : '') + '">' +
        '<input type="checkbox" class="task-check" data-toggle="' + task.id + '"' +
               (task.status === 'done' ? ' checked' : '') + ' />' +
        '<span class="day-task-title">' + escapeHtml(task.title) + '</span>' +
        '<span class="day-task-time">' + escapeHtml((task.due_at || '').slice(11) || '—') + '</span>' +
      '</div>';
  }

  function renderUndated(tasks) {
    if (!tasks.length) {
      elUndated.innerHTML = '<p class="muted">没有未排期的待办。</p>';
      return;
    }
    elUndated.innerHTML = tasks.map(taskRowHtml).join('');
  }

  /* ======================================================================
     三、加载数据
     ====================================================================== */

  async function loadMonth(year, month) {
    try {
      const data = await api.calendarMonth(year, month);
      renderMonth(data);
      return data;
    } catch (err) {
      elGrid.innerHTML = '<div class="empty error">加载失败：' + escapeHtml(err.message) + '</div>';
      return null;
    }
  }

  async function loadDay(dayText) {
    state.selectedDate = dayText;
    try {
      const info = await api.calendarDay(dayText);
      state.dayInfo = info;
      renderDay(info);
      // 重新渲染月历，让选中的格子高亮
      const grid = await api.calendarMonth(state.year, state.month);
      renderMonth(grid);
    } catch (err) {
      elDayHint.textContent = '加载失败：' + err.message;
    }
  }

  function renderDay(info) {
    elDayTitle.textContent = info.date + (info.is_today ? '（今天）' : '');

    if (info.tasks.length) {
      elDayHint.textContent = '这天有 ' + info.tasks.length + ' 条待办。';
      elDayTasks.innerHTML = info.tasks.map(taskRowHtml).join('');
    } else {
      elDayHint.textContent = info.is_past ? '这天没有待办。' : '这天还没有待办，在下面添加一条吧。';
      elDayTasks.innerHTML = '';
    }

    // 过去的日期不允许新建（后端也会拦，但前端先给出更清楚的提示）
    if (info.is_past) {
      qTitle.disabled = true;
      qTime.disabled = true;
      qSubmit.disabled = true;
      showMsg(qMsg, '过去的日子不能新建待办', true);
    } else {
      qTitle.disabled = false;
      qTime.disabled = false;
      qSubmit.disabled = false;
      showMsg(qMsg, '', false);
    }
  }

  /* ======================================================================
     四、事件
     ====================================================================== */

  // 点日历格子 -> 选中那天
  elGrid.addEventListener('click', function (event) {
    const cell = event.target.closest('.cal-cell');
    if (!cell) return;
    loadDay(cell.getAttribute('data-date'));
  });

  // 勾选完成（日历页里也能勾）
  async function onToggleClick(event) {
    const box = event.target.closest('[data-toggle]');
    if (!box) return;
    const id = parseInt(box.getAttribute('data-toggle'), 10);
    try {
      await api.toggleTask(id);
    } catch (err) {
      showMsg(qMsg, err.message, true);
    }
    // 刷新当前视图
    const grid = await api.calendarMonth(state.year, state.month);
    renderMonth(grid);
    if (state.selectedDate) {
      const info = await api.calendarDay(state.selectedDate);
      state.dayInfo = info;
      renderDay(info);
    }
  }
  elDayTasks.addEventListener('click', onToggleClick);
  elUndated.addEventListener('click', onToggleClick);

  // 上/下个月
  btnPrev.addEventListener('click', async function () {
    const data = await api.calendarMonth(state.year, state.month);
    const p = data.prev;
    await loadMonth(p.year, p.month);
  });

  btnNext.addEventListener('click', async function () {
    const data = await api.calendarMonth(state.year, state.month);
    const n = data.next;
    await loadMonth(n.year, n.month);
  });

  btnToday.addEventListener('click', async function () {
    // 传 null 让后端返回"本月"
    const data = await loadMonth(null, null);
    if (data && data.today) loadDay(data.today);
  });

  // 快速添加
  quickForm.addEventListener('submit', async function (event) {
    event.preventDefault();     // 不加这行页面会刷新

    const title = qTitle.value.trim();
    if (!title) {
      showMsg(qMsg, '请填写标题', true);
      qTitle.focus();
      return;
    }
    if (!state.selectedDate) {
      showMsg(qMsg, '请先在日历上选择一天', true);
      return;
    }

    // 把"日期"和"时间"拼成后端要的 "YYYY-MM-DD HH:MM"
    // 时间留空时用 23:59，表示"当天结束前"
    const time = (qTime.value || '23:59').slice(0, 5);
    const payload = {
      title: title,
      due_at: state.selectedDate + ' ' + time,
    };

    qSubmit.disabled = true;
    showMsg(qMsg, '正在添加…', false);
    try {
      const created = await api.createTask(payload);
      qTitle.value = '';
      showMsg(qMsg, '已添加：「' + created.title + '」', false);

      const grid = await api.calendarMonth(state.year, state.month);
      renderMonth(grid);
      const info = await api.calendarDay(state.selectedDate);
      state.dayInfo = info;
      renderDay(info);
      qTitle.focus();
    } catch (err) {
      showMsg(qMsg, err.message, true);
    } finally {
      qSubmit.disabled = false;
    }
  });

  /* ======================================================================
     五、启动
     ====================================================================== */
  (async function init() {
    const data = await loadMonth(null, null);   // 本月
    if (data && data.today) {
      await loadDay(data.today);                // 默认选中今天
    }
  })();
})();
