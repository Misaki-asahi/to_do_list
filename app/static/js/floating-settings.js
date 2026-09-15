/**
 * floating-settings.js -- 设置页里「桌面悬浮窗」那一块的交互逻辑（v0.3.0 新增）。
 *
 * 【为什么要单独一个文件，而不是塞进 settings.js？】
 *   settings.js 已经 380 多行了，负责开机自启、数据、备份、日志、通知、关于六块。
 *   悬浮窗的设置项有十几个（透明度、字号、三个颜色、分块方式……），
 *   再塞进去会变成 700 行的文件 —— 那种长度没人愿意读，
 *   改一个小地方也得先把整篇翻一遍。
 *
 *   拆开之后有个额外的好处：**这个文件可以整个删掉而不影响其它部分**
 *   （模板里就是一行 script 标签，删了页面上少一块而已）。
 *   这种"能整块拿走"的模块边界，是判断拆分是否合理的标准。
 *
 * 【它和其它脚本的约定】
 *   · 只通过 window.api 访问后端（项目铁律：页面里禁止直接写 fetch）；
 *   · 不碰别人的 DOM，只管 id 以 float- 开头的元素；
 *   · settings.js 里那个 setText('ab-...') 之类的工具函数不共用，
 *     本文件自带一份小工具 —— 复制三行比制造一个跨文件依赖更划算。
 */
(function () {
  'use strict';

  const api_ = window.api;
  if (!api_) return;

  // 全局状态（这一小段是"设置页需要知道的东西"）
  let state = {
    config: {},       // 当前设置值
    status: null,     // 服务端给的状态（含窗口进程信息）
    ready: false,     // 有没有成功拉到过数据
  };

  // 拖动滑块时不要每移动一格就发一次请求 —— 那会瞬间打出几十个请求。
  // 停手 250 毫秒后再发一次，这个手法叫「防抖」（debounce）。
  let saveTimer = null;

  /* ======================================================================
     小工具
     ====================================================================== */

  function $(id) { return document.getElementById(id); }

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = (value === null || value === undefined || value === '')
      ? '—' : String(value);
  }

  function msg(text, isError) {
    const el = $('float-msg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'form-msg ' + (isError ? 'error' : 'ok');
    if (text) {
      // 4 秒后自动淡掉，免得旧消息一直挂在那里让人以为是新消息
      clearTimeout(msg._timer);
      msg._timer = setTimeout(() => { el.textContent = ''; }, 4000);
    }
  }

  function debouncedSave(changes) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => save(changes), 250);
  }

  /**
   * 提交设置改动。
   *
   * @param {object} changes 只包含要改的字段（PATCH 语义，见 api.js 的说明）
   */
  async function save(changes) {
    try {
      // 后端返回的是【改动之后的完整状态】（含 config + window），
      // 而不是"你刚改的那几个字段"——因为改设置会有副作用：
      // 开启会让窗口被拉起来、关闭会让窗口停掉。
      // 拿到完整状态，界面上"运行中 / 未运行"才能立刻跟着变。
      const result = await api_.setFloating(changes);
      const fresh = (result && result.status) || {};
      state.status = fresh;
      state.config = fresh.config || state.config;
      render_state();
      msg('已保存');
    } catch (err) {
      msg(err.message || '保存失败', true);
    }
  }

  /* ======================================================================
     渲染
     ====================================================================== */

  function render_state() {
    const c = state.config || {};
    const info = state.status || {};
    const win = info.window || {};
    const limits = info.limits || {};

    // ---- 标题区：状态说明 + 那个小圆点 ----
    setText('float-summary', win.summary || '—');
    const dot = $('float-dot');
    if (dot) {
      dot.className = 'dot ' + (!info.supported ? 'bad'
        : (win.running ? 'ok' : (c.enabled ? 'warn' : '')));
    }
    setText('float-window-summary', win.pythonw || '—');
    setText('float-log-path', win.log_file || '—');

    // ---- 自动重试状态（守护线程的"刹车"）----
    //
    // 【为什么要在界面上显示它？】
    //   因为"退避"和"放弃重试"都是**看不见的行为**：
    //   程序不再尝试拉起窗口这件事本身不弹窗、不报错。
    //   不告诉用户，他只会觉得"这功能怎么坏了"。
    //   所以正常的时候整行隐藏；一旦有问题，就把"原因 + 该怎么办"一起显示出来。
    const health = info.supervisor || {};
    const healthRow = $('float-health-row');
    if (healthRow) {
      if (health.ok) {
        healthRow.hidden = true;
      } else {
        healthRow.hidden = false;
        const summaryEl = $('float-health-summary');
        if (summaryEl) {
          summaryEl.textContent = health.summary || '';
          summaryEl.style.color = health.level === 'error' ? 'var(--bad)' : '#b45309';
        }
        setText('float-health-advice', health.advice || '');
      }
    }

    // ---- 开关 ----
    const toggle = $('float-toggle');
    if (toggle) {
      toggle.checked = !!c.enabled;
      toggle.disabled = !info.supported;   // 没有 tkinter 就没得开
    }
    const autoToggle = $('float-autostart-toggle');
    if (autoToggle) autoToggle.checked = !!c.autostart;
    const onTop = $('float-ontop-toggle');
    if (onTop) onTop.checked = !!c.always_on_top;
    const showDone = $('float-showdone-toggle');
    if (showDone) showDone.checked = !!c.show_completed;
    const highlight = $('float-highlight-toggle');
    if (highlight) highlight.checked = !!c.highlight_reminders;

    // ---- 滑块 ----
    const opacity = $('float-opacity');
    if (opacity) {
      opacity.value = Math.round((c.opacity !== undefined ? c.opacity : 0.70) * 100);
      const transparency = 100 - Number(opacity.value);
      setText('float-opacity-value', opacity.value + '%');
      // 【这段文案的措辞很关键，别随手改】
      //   用户澄清过："字体清晰指的是字体分辨率保持高" —— 也就是**锐利度**。
      //   而整窗透明度降低影响的是**对比度**（字和背景的亮度差），
      //   不是锐利度（半透明只是让颜色等比变淡，笔画边缘依然是锐的）。
      //   所以这里不能说"会发虚"（那是错的），要说"字会被桌面透淡、变难读"。
      const low = Math.round((limits.opacity ? limits.opacity[0] : 0.30) * 100);
      const warn = Math.round((limits.clarity_warn_below || 0.60) * 100);
      setText('float-opacity-label',
        '当前不透明度 ' + opacity.value + '%（= 透明度 ' + transparency + '%）。' +
        '可调 ' + low + '% ~ 100%。数字越大越不透明、字越好读；' +
        '调到 ' + warn + '% 以下时桌面会明显透进来，对比度下降、读起来吃力。');
    }
    const fontsize = $('float-fontsize');
    if (fontsize) {
      fontsize.value = c.font_size !== undefined ? c.font_size : 9;
      setText('float-fontsize-value', String(fontsize.value));
      const fs = limits.font_size || [6, 72];
      setText('float-fontsize-label',
        '当前 ' + fontsize.value + ' 磅（可调 ' + fs[0] + ' ~ ' + fs[1] +
        '）。只支持整数磅 —— 非整数会被系统取整，反而更容易发虚。');
    }

    // ---- 字体 ----
    const family = $('float-fontfamily');
    if (family) family.value = c.font_family || '宋体';
    const familySelect = $('float-fontfamily-select');
    if (familySelect && c.font_family) familySelect.value = c.font_family;
    const fg = $('float-fg');
    if (fg && c.fg_color) fg.value = c.fg_color;
    const bg = $('float-bg');
    if (bg && c.bg_color) bg.value = c.bg_color;
    const accent = $('float-accent');
    if (accent && c.accent_color) accent.value = c.accent_color;

    // ---- 下拉框 ----
    const groupBy = $('float-groupby');
    if (groupBy && c.group_by) groupBy.value = c.group_by;
    const scope = $('float-scope');
    if (scope && c.scope) scope.value = c.scope;

    // ---- 数字区 ----
    setText('float-stat-size', (c.width || 0) + ' × ' + (c.height || 0));
    setText('float-stat-pos', (c.x || 0) + ', ' + (c.y || 0));
    setText('float-stat-pid', win.running ? win.pid : '未运行');
    setText('float-stat-start', win.started_at || '');

    // ---- 主题色块（只建一次，之后只更新选中态） ----
    render_themes(info.themes || {});
  }

  let themes_rendered = false;
  function render_themes(themes) {
    const box = $('float-themes');
    if (!box || themes_rendered) return;
    const names = Object.keys(themes);
    if (!names.length) return;

    box.innerHTML = '';
    names.forEach((name) => {
      const theme = themes[name];
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'theme-chip';
      btn.title = '背景 ' + theme.bg + ' / 文字 ' + theme.fg;
      btn.style.background = theme.bg;
      btn.style.color = theme.fg;
      btn.style.borderColor = theme.accent;
      btn.textContent = name;
      btn.addEventListener('click', async () => {
        try {
          await api_.setFloatingTheme(name);
          msg('已套用配色：' + name);
          refresh();
        } catch (err) {
          msg(err.message || '换配色失败', true);
        }
      });
      box.appendChild(btn);
    });
    themes_rendered = true;
  }

  /* ======================================================================
     从服务端拉数据
     ====================================================================== */

  async function refresh() {
    try {
      const data = await api_.getFloating();
      state.status = data;
      state.config = data.config || {};
      state.ready = true;

      // 下拉框的选项由服务端给（"分块方式有哪几种"是后端说了算的），
      // 这样以后后端加一种分块方式，前端不用改一个字。
      const groupBy = $('float-groupby');
      if (groupBy && !groupBy.options.length) {
        Object.keys(data.choices.group_by).forEach((key) => {
          groupBy.add(new Option(data.choices.group_by[key], key));
        });
      }
      const scope = $('float-scope');
      if (scope && !scope.options.length) {
        Object.keys(data.choices.scope).forEach((key) => {
          scope.add(new Option(data.choices.scope[key], key));
        });
      }

      // 字体下拉框的预设（宋体 / 楷体 / 仿宋 / 黑体 / …）同样来自服务端。
      // 这样以后想加一款预设字体，只改 config.py 一处。
      const familySelect = $('float-fontfamily-select');
      if (familySelect && !familySelect.options.length) {
        (data.fonts || []).forEach((name) => {
          familySelect.add(new Option(name, name));
        });
      }

      // 滑块的上下限也由服务端给：前端不写死任何数字，
      // 以后调整范围只要改 config.py，页面会自己跟上。
      const opacity = $('float-opacity');
      if (opacity && data.limits && data.limits.opacity) {
        opacity.min = Math.round(data.limits.opacity[0] * 100);
        opacity.max = Math.round(data.limits.opacity[1] * 100);
      }
      const fontsize = $('float-fontsize');
      if (fontsize && data.limits && data.limits.font_size) {
        fontsize.min = data.limits.font_size[0];
        fontsize.max = data.limits.font_size[1];
      }

      // 服务端给出一份"推荐默认值"，用它提示用户"默认长什么样"
      const hint = $('float-defaults-hint');
      if (hint && data.limits) {
        hint.textContent =
          '出厂默认：黑底白字 · 透明度 ' +
          Math.round(data.limits.opacity_default * 100) + '% · 字号 ' +
          data.limits.font_size_default + ' 磅 · 宋体';
      }

      render_state();
    } catch (err) {
      setText('float-summary', '读不到悬浮窗状态：' + (err.message || err));
      const dot = $('float-dot');
      if (dot) dot.className = 'dot bad';
    }
  }

  /* ======================================================================
     绑定事件
     ====================================================================== */

  function bind() {
    // ---- 总开关 ----
    const toggle = $('float-toggle');
    if (toggle) {
      toggle.addEventListener('change', () => {
        // 注意这里同时传了 visible：
        //   "开启"的本意一定是"我要看到它"，所以顺手把显示也打开
        //   （后端也有同样的规则，两边都写着是为了让意图更明显）
        save({ enabled: toggle.checked, visible: toggle.checked });
      });
    }

    const autoToggle = $('float-autostart-toggle');
    if (autoToggle) {
      autoToggle.addEventListener('change', async () => {
        // 这个开关要同时做两件事，顺序很重要：
        //   ① 记住偏好（悬浮窗启动时读它，决定要不要写进自启脚本）
        //   ② 如果自启已经开着，立刻按新偏好重写一次自启脚本
        //      —— 否则用户会觉得"我改了，但下次开机没变化"
        try {
          await api_.setPreference('autostart_floating',
                                   autoToggle.checked ? '1' : '0');
          await save({ autostart: autoToggle.checked });

          const auto = (state.status && state.status.autostart) || {};
          if (auto.enabled) {
            // 用现有的两个偏好值重写自启脚本
            const pref = await api_.getSettings();
            const prefs = pref.settings || {};
            await api_.setAutostart(true,
                                    prefs.autostart_open_browser === '1',
                                    autoToggle.checked);
            msg(autoToggle.checked
              ? '已开启：下次登录 Windows 会显示悬浮窗'
              : '已关闭：下次登录不再自动显示悬浮窗');
          } else {
            msg(autoToggle.checked
              ? '偏好已记住。想让它生效，请到上面打开「开机自动启动」'
              : '偏好已记住');
          }
          await load_autostart();
        } catch (err) {
          msg(err.message || '设置失败', true);
        }
      });
    }

    // ---- 滑块：透明度 ----
    const opacity = $('float-opacity');
    if (opacity) {
      opacity.addEventListener('input', () => {
        setText('float-opacity-value', opacity.value + '%');
      });
      opacity.addEventListener('change', () => {
        debouncedSave({ opacity: Number(opacity.value) / 100 });
      });
    }

    // ---- 滑块：字号 ----
    const fontsize = $('float-fontsize');
    if (fontsize) {
      fontsize.addEventListener('input', () => {
        setText('float-fontsize-value', String(fontsize.value));
      });
      fontsize.addEventListener('change', () => {
        debouncedSave({ font_size: Number(fontsize.value) });
      });
    }

    // ---- 字体：下拉框选预设 ----
    const familySelect = $('float-fontfamily-select');
    if (familySelect) {
      familySelect.addEventListener('change', () => {
        const value = familySelect.value;
        const input = $('float-fontfamily');
        if (input) input.value = value;      // 下拉框和输入框始终显示同一件事
        save({ font_family: value });
      });
    }

    // ---- 字体：手打一个别的（失焦或回车时才提交，避免每敲一个字母就存一次） ----
    const family = $('float-fontfamily');
    if (family) {
      const submit = () => {
        const value = family.value.trim();
        if (!value) return;
        // 手打的值如果正好是某个预设，就把下拉框也同步过来，
        // 免得两个控件显示得不一样（用户会以为是 Bug）
        if (familySelect) {
          const match = Array.prototype.find.call(
            familySelect.options, (opt) => opt.value === value);
          familySelect.value = match ? value : '';
        }
        save({ font_family: value });
      };
      family.addEventListener('change', submit);
      family.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    }

    // ---- 颜色：input 事件在拖动取色器时连续触发，所以用防抖 ----
    [['float-fg', 'fg_color'], ['float-bg', 'bg_color'], ['float-accent', 'accent_color']]
      .forEach(([id, field]) => {
        const el = $(id);
        if (!el) return;
        el.addEventListener('input', () => {
          const changes = {};
          changes[field] = el.value;
          debouncedSave(changes);
        });
      });

    // ---- 下拉框 ----
    const groupBy = $('float-groupby');
    if (groupBy) groupBy.addEventListener('change', () => save({ group_by: groupBy.value }));
    const scope = $('float-scope');
    if (scope) scope.addEventListener('change', () => save({ scope: scope.value }));

    // ---- 三个开关 ----
    const onTop = $('float-ontop-toggle');
    if (onTop) onTop.addEventListener('change', () => save({ always_on_top: onTop.checked }));
    const showDone = $('float-showdone-toggle');
    if (showDone) showDone.addEventListener('change', () => save({ show_completed: showDone.checked }));
    const highlight = $('float-highlight-toggle');
    if (highlight) highlight.addEventListener('change', () => save({ highlight_reminders: highlight.checked }));

    // ---- 按钮：立即显示 ----
    const btnShow = $('btn-float-show');
    if (btnShow) {
      btnShow.addEventListener('click', async () => {
        msg('正在启动窗口…');
        try {
          const result = await api_.showFloating();
          msg(result.message || '已请求显示');
          // 窗口起来要一点点时间，等 1.2 秒再刷新状态，看到的才是真的
          setTimeout(refresh, 1200);
        } catch (err) {
          msg(err.message || '启动失败', true);
        }
      });
    }

    // ---- 按钮：重启窗口 ----
    const btnRestart = $('btn-float-restart');
    if (btnRestart) {
      btnRestart.addEventListener('click', async () => {
        msg('正在重启窗口…');
        try {
          const result = await api_.restartFloating();
          msg(result.message || '已重启');
          setTimeout(refresh, 1200);
        } catch (err) {
          msg(err.message || '重启失败', true);
        }
      });
    }

    // ---- 按钮：隐藏窗口 ----
    const btnHide = $('btn-float-hide');
    if (btnHide) {
      btnHide.addEventListener('click', async () => {
        try {
          const result = await api_.hideFloating();
          msg(result.message || '已隐藏');
          setTimeout(refresh, 800);
        } catch (err) {
          msg(err.message || '隐藏失败', true);
        }
      });
    }

    // ---- 按钮：打开窗口日志目录 ----
    const btnLog = $('btn-float-log');
    if (btnLog) {
      btnLog.addEventListener('click', async () => {
        try {
          await api_.openFolder('data');
          msg('已打开数据目录，悬浮窗日志在 floating\\window.log');
        } catch (err) {
          msg(err.message || '打开失败', true);
        }
      });
    }

    // ---- 按钮：恢复默认 ----
    const btnReset = $('btn-float-reset');
    if (btnReset) {
      btnReset.addEventListener('click', async () => {
        // 【为什么这里不需要二次确认？】
        //   因为它只重置"外观和位置"，不会碰任何待办数据
        //   （"数据安全铁律二"管的是会丢数据的操作，这里不丢）。
        //   不过它会把窗口关掉（位置回到默认），所以提示里要说清楚。
        try {
          const result = await api_.resetFloating();
          msg(result.message || '已恢复默认设置');
          themes_rendered = false;         // 让色块重画一次（状态可能变了）
          await refresh();
        } catch (err) {
          msg(err.message || '恢复失败', true);
        }
      });
    }
  }

  /* ======================================================================
     开机自启那一块的联动
     ====================================================================== */

  async function load_autostart() {
    /**
     * 读设置页里"开机自启"的状态，同步到悬浮窗这一块。
     *
     * 【为什么要读这一块？】
     *   因为"开机自启时也显示悬浮窗"这个开关的文案依赖它：
     *   自启没开的时候，用户勾了它其实不会发生任何事 ——
     *   必须在界面上说清楚，否则就是一个"看起来生效其实没生效"的坑。
     *   （这类"静默无效"的设置，是用户体验里最让人困惑的一类。）
     */
    try {
      const data = await api_.getSettings();
      const auto = data.autostart || {};
      if (state.status) state.status.autostart = auto;

      const note = $('float-autostart-toggle');
      if (note) {
        const desc = note.parentElement.parentElement.querySelector('.setting-desc');
        if (desc) {
          desc.textContent = auto.enabled
            ? '已开启：登录 Windows 后会自动显示悬浮窗。'
            : '⚠️ 上面的「开机自动启动」还没开启 —— 现在勾选它也暂时不会生效，'
              + '请先去那里打开。';
        }
      }
      if (auto.supported !== undefined) {
        note.disabled = !auto.supported;
      }
      // 自启脚本里到底带没带 --floating，以【文件】为准（它才是系统真正执行的）
      note.checked = !!auto.floating;
    } catch (err) {
      /* 读不到就保持默认，不影响其它功能 */
    }
  }

  /* ======================================================================
     启动
     ====================================================================== */

  function init() {
    if (!$('float-toggle')) return;      // 这个页面上没有悬浮窗区块，直接退出
    bind();
    refresh();
    load_autostart();

    // 每 5 秒刷新一次状态：用户可能在窗口那边拖动/关闭，
    // 设置页上的"窗口尺寸 / 位置 / 进程号"应该跟着变，而不是要手动刷新页面。
    setInterval(refresh, 5000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
