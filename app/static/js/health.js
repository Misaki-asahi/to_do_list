/**
 * health.js -- 底部状态栏的自检脚本。
 *
 * 它的使命：随时告诉你"链路哪一层是活的"。
 * 四个圆点分别代表：静态资源 / 后端服务 / 接口通信 / 数据库。
 *
 * 程序出怪问题时，先看这一排点，能 3 秒定位故障在哪一层：
 *   全绿      -> 链路没问题，去查具体功能
 *   某个红点  -> 直接锁定是哪一层坏了
 */
(function () {
  const dotStatic = document.getElementById('dot-static');
  const dotServer = document.getElementById('dot-server');
  const dotApi = document.getElementById('dot-api');
  const dotDb = document.getElementById('dot-db');

  function setDot(el, ok) {
    // 页面里可能没有这个元素（比如别的页面也引用了本文件），所以要判空
    if (el) el.className = 'dot ' + (ok ? 'ok' : 'bad');
  }

  async function check() {
    setDot(dotStatic, window.APP_STATIC_READY === true);
    setDot(dotServer, false);
    setDot(dotApi, false);
    setDot(dotDb, false);

    try {
      const data = await api.health();   // 调用后端 /api/health
      setDot(dotServer, true);
      setDot(dotApi, true);

      // 数据库这一项：文件存在 + tasks 表已建好，才算通过
      const db = data.db || {};
      setDot(dotDb, db.exists === true && (db.tables || []).indexOf('tasks') >= 0);
    } catch (err) {
      // 检测失败时保持红点即可，不打扰用户。
      // 想排查原因就打开 F12 的 Console 看具体报错。
      if (window.console && console.warn) {
        console.warn('[health] 后端检测失败：' + err.message);
      }
    }
  }

  // 页面一打开就检测一次
  check();
})();
