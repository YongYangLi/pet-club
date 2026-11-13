// 版本看门狗：页面手里的 build 号和服务端当前的不一样，就说明有新版了。
//
// 为什么不用 WebSocket 推：网关刻意不解析业务消息，为这件事去改协议、
// 改 server.py 不划算。一条 HTTP 轮询就够，服务端读的是个小 json。
//
// 为什么页面必须自带 build 号：旧页面去问服务端「你是哪版」，问到的
// 永远是最新那版，于是永远觉得自己也是新的 —— 这个检测就不成立了。
// 所以 build 号在 HTML 里（web_gateway.py 渲染时填的），不靠 JS 去取。

const RELOAD_KEY = 'petclub.reloadedFor';

function myBuild() {
  const el = document.querySelector('meta[name="petclub-build"]');
  return el ? (el.content || '') : '';
}

function markReloaded(build) {
  try { localStorage.setItem(RELOAD_KEY, build); } catch (_) {}
}

function reloadedBefore(build) {
  try { return localStorage.getItem(RELOAD_KEY) === build; } catch (_) { return false; }
}

export function watchVersion(onUpdate) {
  const mine = myBuild();
  // 直接双击 index.html 打开时没有 meta（那说明没走网关），不折腾。
  if (!mine) return null;

  let stopped = false;
  let busy = false;

  async function check() {
    if (stopped || busy) return;
    busy = true;
    try {
      const res = await fetch('/api/version', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !data.build || data.build === mine) return;

      stopped = true;
      // 页面在后台时直接刷：用户看不到，切回来就是新版。
      // 但同一个 build 只自动刷一次 —— 万一 HTML 被中间层缓存住，
      // 刷完还是旧的，会变成无限刷新。
      if (document.hidden && !reloadedBefore(data.build)) {
        markReloaded(data.build);
        location.reload();
        return;
      }
      onUpdate(data);
    } catch (_) {
      // 网络抖动下一轮再说，别把提示条抖出来
    } finally {
      busy = false;
    }
  }

  // 刚打开就提示会很唐突，等 8 秒再查第一次
  setTimeout(check, 8000);
  setInterval(check, 60000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) check();
  });
  window.addEventListener('online', check);
  return check;
}
