/**
 * settings.js -- 设置页的交互逻辑。
 *
 * 负责：
 *   1. 拉取并显示各种状态（开机自启、数据统计、提醒参数、关于）
 *   2. 处理开机自启开关
 *   3. 处理"打开某个目录"按钮
 */
(function () {

  const api_ = window.api;   // 避免和下面的变量重名

  // ---------------- 元素 ----------------
  const autoToggle = document.getElementById('autostart-toggle');
  const browserToggle = document.getElementById('openbrowser-toggle');
  const autoSummary = document.getElementById('autostart-summary');
  const autoPath = document.getElementById('autostart-path');
  const autoMsg = document.getElementById('autostart-msg');
  const btnOpenStartup = document.getElementById('btn-open-startup');

  const bkCount = document.getElementById('bk-count');
  const bkKeep = document.getElementById('bk-keep');
  const bkAge = document.getElementById('bk-age');
  const bkFolder = document.getElementById('bk-folder');
  const backupList = document.getElementById('backup-list');
  const backupMsg = document.getElementById('backup-msg');
  const btnBackup = document.getElementById('btn-backup');
  const btnOpenBackups = document.getElementById('btn-open-backups');

  const notifySummary = document.getElementById('notify-summary');
  const notifyDot = document.getElementById('notify-dot');
  const appidSummary = document.getElementById('appid-summary');
  const appidDot = document.getElementById('appid-dot');
  const appidMode = document.getElementById('appid-mode');
  const btnRegisterAppid = document.getElementById('btn-register-appid');
  const soundToggle = document.getElementById('sound-toggle');
  const schedSummary = document.getElementById('sched-summary');
  const schedDot = document.getElementById('sched-dot');
  const btnTestNotify = document.getElementById('btn-test-notify');
  const notifyMsg = document.getElementById('notify-msg');

  const logCount = document.getElementById('log-count');
  const logSize = document.getElementById('log-size');
  const logKeep = document.getElementById('log-keep');
  const logPath = document.getElementById('log-path');
  const logView = document.getElementById('log-view');
  const logMsg = document.getElementById('log-msg');
  const btnViewLog = document.getElementById('btn-view-log');
  const btnOpenLogs = document.getElementById('btn-open-logs');
  const btnCopyLog = document.getElementById('btn-copy-log');

  const btnShutdown = document.getElementById('btn-shutdown');
  const shutdownMsg = document.getElementById('shutdown-msg');

  const btnOpenData = document.getElementById('btn-open-data');
  const btnOpenProject = document.getElementById('btn-open-project');
  const btnOpenDocs = document.getElementById('btn-open-docs');

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = (value === null || value === undefined) ? '—' : String(value);
  }

  function showMsg(text, isError) {
    autoMsg.textContent = text || '';
    autoMsg.className = 'form-msg ' + (isError ? 'error' : 'ok');
  }

  /* ======================================================================
     一、渲染
     ====================================================================== */

  function renderAutostart(info) {
    autoToggle.checked = info.enabled;
    // 值来自自启脚本本身（.vbs 里有没有 --no-browser），所以它反映的是"实际会怎样"
    browserToggle.checked = info.open_browser;
    autoSummary.textContent = info.summary;
    autoPath.textContent = info.launcher_path || '（当前系统不支持）';

    // 不支持的系统（非 Windows）直接禁用开关
    const supported = info.supported;
    autoToggle.disabled = !supported;
    browserToggle.disabled = !supported;
    btnOpenStartup.disabled = !supported;
  }

  function renderStats(stats) {
    setText('stat-total', stats.tasks.total);
    setText('stat-todo', stats.tasks.todo);
    setText('stat-done', stats.tasks.done);
    setText('stat-size', stats.db.size_kb + ' KB');
    setText('db-path', stats.db.path);

    setText('rem-poll', stats.reminders.poll_seconds);
    setText('rem-snooze', stats.reminders.snooze_minutes);
    setText('rem-batch', stats.reminders.max_batch);
    setText('rem-enabled', stats.reminders.enabled);
    if (notifySummary) {
      notifySummary.textContent = stats.notify.summary;
      notifyDot.className = 'dot ' + (stats.notify.supported ? 'ok' : 'bad');
    }
    if (appidSummary) {
      const n = stats.notify || {};
      appidSummary.textContent = n.hint || '—';
      appidDot.className = 'dot ' + (n.supported ? 'ok' : 'bad');
      if (appidMode) appidMode.value = n.saved_mode || 'auto';
      if (btnRegisterAppid) {
        // "注册"按钮只在【自有身份】模式下才有意义
        btnRegisterAppid.hidden = (n.mode !== 'own') || !!n.app_id_registered;
      }
    }
    if (soundToggle) {
      soundToggle.checked = data.settings
        ? (data.settings.notify_sound_enabled !== '0')
        : true;
    }
    if (schedSummary) {
      const last = stats.scheduler.last_run || {};
      let text = stats.scheduler.running ? '运行中' : '未运行';
      if (last.at) text += ' · 上次检查 ' + last.at;
      if (last.error) text += ' · 出错：' + last.error;
      schedSummary.textContent = text;
      schedDot.className = 'dot ' + (stats.scheduler.running ? 'ok' : 'bad');
    }
  }

  function renderLogs(info) {
    if (!info) return;
    logCount.textContent = info.file_count;
    logSize.textContent = info.total_kb + ' KB';
    logKeep.textContent = info.keep_days + ' 天';
    logPath.textContent = info.file;
  }

  function renderBackups(info) {
    bkCount.textContent = info.count;
    bkKeep.textContent = info.keep;
    bkAge.textContent = (info.newest_age_minutes === null)
      ? '从未'
      : (info.newest_age_minutes < 60
          ? info.newest_age_minutes + ' 分钟前'
          : Math.round(info.newest_age_minutes / 60) + ' 小时前');
    bkFolder.textContent = info.folder;

    if (!info.items.length) {
      backupList.innerHTML = '<p class="muted">还没有任何备份。下次启动程序时会自动创建第一份。</p>';
      return;
    }
    backupList.innerHTML = info.items.map(function (b) {
      return '<div class="backup-item">' +
               '<span class="bk-time">' + b.created + '</span>' +
               '<span class="bk-size">' + b.size_kb + ' KB · ' + b.name + '</span>' +
             '</div>';
    }).join('');
  }

  function renderAbout(info) {
    setText('ab-name', info.name);
    setText('ab-version', info.version);
    setText('ab-python', info.python);
    setText('ab-platform', info.platform);
    setText('ab-server', info.server);
    setText('ab-now', info.now);
  }

  /* ======================================================================
     二、加载
     ====================================================================== */

  async function load() {
    try {
      const data = await api_.getSettings();
      renderAutostart(data.autostart);
      renderStats(data.stats);
      renderBackups(data.backups);
      renderLogs(data.stats.logs);
      renderAbout(data.about);
    } catch (err) {
      autoSummary.textContent = '加载失败：' + err.message;
    }
  }

  /* ======================================================================
     三、开机自启开关
     ====================================================================== */

  async function onToggleAutostart() {
    const want = autoToggle.checked;
    autoToggle.disabled = true;
    showMsg('正在' + (want ? '开启' : '关闭') + '…', false);

    try {
      const result = await api_.setAutostart(want, browserToggle.checked);
      renderAutostart(result.status);
      showMsg(result.message, !result.ok);
    } catch (err) {
      showMsg(err.message, true);
      // 失败时把开关恢复成真实状态，避免界面和实际不一致
      autoToggle.checked = !want;
    } finally {
      autoToggle.disabled = false;
    }
  }

  async function onToggleBrowser() {
    // "启动时打开浏览器"这个选项，只有在自启已开启时才需要立刻生效；
    // 没开启的话，记住偏好即可，等开启时一起写入。
    //
    // ★ v0.4.2 说明：这一项【出厂默认是关的】（用户要求"自启动静默启动"），
    //   但功能保留 —— 用户想开机就看到网页界面时，自己把它打开即可。
    if (!autoToggle.checked) {
      showMsg('已记住偏好，开启自启时会一起生效', false);
      return;
    }
    try {
      const result = await api_.setAutostart(true, browserToggle.checked);
      renderAutostart(result.status);
      showMsg(result.message, !result.ok);
    } catch (err) {
      showMsg(err.message, true);
    }
  }

  /* ======================================================================
     四、打开目录
     ====================================================================== */

  async function openFolder(target, btn) {
    btn.disabled = true;
    try {
      await api_.openFolder(target);
      showMsg('已打开', false);
    } catch (err) {
      showMsg(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  /* ======================================================================
     五、绑定并启动
     ====================================================================== */

  autoToggle.addEventListener('change', onToggleAutostart);
  browserToggle.addEventListener('change', onToggleBrowser);

  btnOpenStartup.addEventListener('click', function () { openFolder('startup', btnOpenStartup); });
  btnBackup.addEventListener('click', async function () {
    btnBackup.disabled = true;
    backupMsg.textContent = '正在备份…';
    backupMsg.className = 'form-msg';
    try {
      const result = await api_.makeBackup();
      renderBackups(result.backups);
      backupMsg.textContent = result.message || '备份完成';
      backupMsg.className = 'form-msg ' + (result.ok ? 'ok' : 'error');
    } catch (err) {
      backupMsg.textContent = err.message;
      backupMsg.className = 'form-msg error';
    } finally {
      btnBackup.disabled = false;
    }
  });

  btnOpenBackups.addEventListener('click', function () { openFolder('backups', btnOpenBackups); });

  appidMode.addEventListener('change', async function () {
    const want = appidMode.value;
    appidMode.disabled = true;
    try {
      await api_.setPreference('notify_app_id_mode', want);
      const names = { auto: '自动', system: '兼容模式', own: '自有身份' };
      notifyMsg.textContent = '已切换到：' + (names[want] || want);
      notifyMsg.className = 'form-msg ok';
      await load();
    } catch (err) {
      notifyMsg.textContent = err.message;
      notifyMsg.className = 'form-msg error';
    } finally {
      appidMode.disabled = false;
    }
  });

  btnRegisterAppid.addEventListener('click', async function () {
    btnRegisterAppid.disabled = true;
    notifyMsg.textContent = '正在注册…';
    notifyMsg.className = 'form-msg';
    try {
      const result = await api_.registerNotifyApp();
      notifyMsg.textContent = result.message || (result.ok ? '已注册' : '注册失败');
      notifyMsg.className = 'form-msg ' + (result.ok ? 'ok' : 'error');
      await load();
    } catch (err) {
      notifyMsg.textContent = err.message;
      notifyMsg.className = 'form-msg error';
    } finally {
      btnRegisterAppid.disabled = false;
    }
  });

  soundToggle.addEventListener('change', async function () {
    const want = soundToggle.checked;
    soundToggle.disabled = true;
    try {
      await api_.setPreference('notify_sound_enabled', want ? '1' : '0');
      notifyMsg.textContent = want ? '已开启页面提示音' : '已关闭页面提示音';
      notifyMsg.className = 'form-msg ok';
    } catch (err) {
      showMsg(err.message, true);
      soundToggle.checked = !want;
    } finally {
      soundToggle.disabled = false;
    }
  });

  btnTestNotify.addEventListener('click', async function () {
    btnTestNotify.disabled = true;
    notifyMsg.textContent = '正在发送…';
    notifyMsg.className = 'form-msg';
    try {
      const result = await api_.testNotification();
      notifyMsg.textContent = result.message || (result.ok ? '已发送' : '发送失败');
      notifyMsg.className = 'form-msg ' + (result.ok ? 'ok' : 'error');
    } catch (err) {
      notifyMsg.textContent = err.message;
      notifyMsg.className = 'form-msg error';
    } finally {
      btnTestNotify.disabled = false;
    }
  });

  btnOpenData.addEventListener('click', function () { openFolder('data', btnOpenData); });
  btnOpenProject.addEventListener('click', function () { openFolder('project', btnOpenProject); });
  btnOpenDocs.addEventListener('click', function () { openFolder('docs', btnOpenDocs); });

  async function loadLog() {
    try {
      const data = await api_.getLogs(150);
      renderLogs(data.info);
      logView.textContent = data.content || '（日志是空的）';
      logView.hidden = false;
      logView.scrollTop = logView.scrollHeight;      // 自动滚到最新
      lastLogText = data.content || '';
    } catch (err) {
      logMsg.textContent = err.message;
      logMsg.className = 'form-msg error';
    }
  }

  let lastLogText = '';

  btnViewLog.addEventListener('click', async function () {
    btnViewLog.disabled = true;
    logMsg.textContent = '正在读取…';
    logMsg.className = 'form-msg';
    try {
      await loadLog();
      logMsg.textContent = '已加载（显示最后 150 行）';
      logMsg.className = 'form-msg ok';
    } finally {
      btnViewLog.disabled = false;
    }
  });

  btnOpenLogs.addEventListener('click', function () { openFolder('logs', btnOpenLogs); });

  btnCopyLog.addEventListener('click', async function () {
    try {
      if (!lastLogText) await loadLog();
      await navigator.clipboard.writeText(lastLogText);
      logMsg.textContent = '日志内容已复制到剪贴板，可以直接粘贴给别人看';
      logMsg.className = 'form-msg ok';
    } catch (err) {
      logMsg.textContent = '复制失败（浏览器可能没给剪贴板权限）：' + err.message;
      logMsg.className = 'form-msg error';
    }
  });

  btnShutdown.addEventListener('click', async function () {
    if (!window.confirm('确定要退出程序吗？\n\n退出后待办提醒也会停止，下次可以重新打开程序。')) return;
    btnShutdown.disabled = true;
    shutdownMsg.textContent = '正在退出…';
    shutdownMsg.className = 'form-msg';
    try {
      const r = await api_.shutdown();
      shutdownMsg.textContent = r.message || '已退出';
      shutdownMsg.className = 'form-msg ok';
    } catch (err) {
      // 程序退出时连接会被中断，这是正常现象，不是错误
      shutdownMsg.textContent = '程序已退出，可以关闭这个页面了';
      shutdownMsg.className = 'form-msg ok';
    }
  });

  load();
})();
