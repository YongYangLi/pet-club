// 网页版「传图之后不刷新就变」端到端探针。
//
// 跑法（不要用线上那套端口，会污染真实 data/）：
//   1. python .tmp/srv_test.py
//   2. python .tmp/gw_test.py
//   3. node tools/probe_web_live.mjs
//
// 之前 probe_web_poses.mjs 是「传完 —— reload —— 再看」，所以它证明了
// 刷新后是对的，却漏掉了用户真正会遇到的那一步：**不刷新**。
// 这个探针专门盯住不刷新的路径，四条都要过：
//   · 换图     → 自己的角色当场换脸
//   · 新增动作 → 动作表多一项，右键菜单里能点
//   · 菜单点击 → 真的演出来
//   · 还原     → 当场回到出厂图
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = 9334;
const BASE = 'http://127.0.0.1:8776/';
const PROFILE = `${process.env.TEMP || '/tmp'}/petclub-live-${Date.now()}`;

const log = (...a) => console.log(...a);
const failed = [];

function chk(name, ok, detail = '') {
  log(`${ok ? '[ok]  ' : '[FAIL]'} ${name}${!ok && detail ? '  <- ' + detail : ''}`);
  if (!ok) failed.push(name);
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const waiting = new Map();
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (!m.id || !waiting.has(m.id)) return;
    const { res, rej } = waiting.get(m.id);
    waiting.delete(m.id);
    m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
  });
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id;
    waiting.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  return { ready, send, close: () => ws.close() };
}

async function evalJS(c, expression) {
  const r = await c.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true, userGesture: true,
  });
  if (r.exceptionDetails) {
    throw new Error(r.exceptionDetails.text + ' '
      + JSON.stringify(r.exceptionDetails.exception));
  }
  return r.result.value;
}

async function waitFor(c, expr, ms = 15000, label = expr) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      if (await evalJS(c, expr)) return true;
    } catch (_) { /* 还没就绪 */ }
    await sleep(150);
  }
  throw new Error('超时等待：' + label);
}

async function openPage(url) {
  const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(url)}`,
    { method: 'PUT' });
  const t = await r.json();
  const c = connect(t.webSocketDebuggerUrl);
  await c.ready;
  await c.send('Runtime.enable');
  await c.send('Page.enable');
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await c.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {});
  return c;
}

// 直接用 fetch 走接口，然后把服务端返回的 manifest 交给页面 —— 这正是
// upload.js 的 submit() 收尾时做的事（`done(body.manifest)`）。
async function post(path, payload) {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return r.json();
}

async function b64(rel) {
  const buf = await readFile(new URL('..' + rel, import.meta.url));
  return buf.toString('base64');
}

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--window-size=1280,860', '--mute-audio', 'about:blank',
], { stdio: 'ignore' });

let A;
let B;
try {
  for (let i = 0; i < 60; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).ok) break;
    } catch (_) { /* 还没起来 */ }
    await sleep(200);
  }

  A = await openPage(BASE);
  await waitFor(A, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(A, `window.petclub.join('p1')`);
  await waitFor(A, `!!window.petclub.pets.get('p1')`, 15000, 'p1 出生');
  await waitFor(A, `window.petclub.pets.get('p1').landed`, 15000, 'p1 落地');

  // 第二个人：别人的图换了，他那边应该**不用刷新、也不用等 60 秒**就变
  B = await openPage(BASE);
  await waitFor(B, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(B, `window.petclub.join('p2')`);
  await waitFor(B, `!!window.petclub.pets.get('p1')`, 15000, 'B 看到 p1');

  const before = await evalJS(A, `window.petclub.pets.get('p1').sp.poses.wave.src`);
  chk('起始是出厂图', before.includes('/assets/roles/p1/wave.png'), before);

  // ---- 1. 换图：不刷新，当场换脸 ----
  const swap = await post('api/sprites', {
    role: 'p1', action: 'wave', data: await b64('/assets/roles/p2/idle.png'),
  });
  chk('服务端接受换图', !!swap.ok, JSON.stringify(swap).slice(0, 120));
  await evalJS(A, `window.petclub.afterUpload(${JSON.stringify(swap.manifest)})`);
  const swapped = await evalJS(A, `new Promise((res) => setTimeout(
    () => res((window.petclub.pets.get('p1') || {sp:{poses:{}}}).sp.poses.wave.src || ''), 900))`);
  chk('换图后不刷新就换了 URL', swapped.includes('/sprites/p1/wave.png'), swapped);
  // URL 变了不代表画上去了：确认那张图真的解码成功（naturalWidth > 0）
  const painted = await evalJS(A, `(() => {
    const p = window.petclub.pets.get('p1');
    return p && p.img ? p.img.naturalWidth : -1;
  })()`);
  chk('换上去的图真的解码出来了', typeof painted === 'number' && painted > 0,
    String(painted));

  // ---- 1b. 别人那边也要立刻换（这条是这次改的核心）----
  const otherBefore = await evalJS(B, `window.petclub.pets.get('p1').sp.poses.wave.src`);
  const got = await evalJS(B, `new Promise((res) => {
    const t0 = Date.now();
    const tick = () => {
      const p = window.petclub.pets.get('p1');
      const src = p ? p.sp.poses.wave.src : '';
      if (src.includes('/sprites/p1/wave.png')) return res(Date.now() - t0);
      if (Date.now() - t0 > 5000) return res(-1);
      setTimeout(tick, 100);
    };
    tick();
  })`);
  chk('别人不用刷新就换上了新图', got >= 0, `等了 ${got}ms（起始 ${otherBefore}）`);
  chk('确实是收到推送后几秒内，而不是等 60 秒轮询', got >= 0 && got < 5000, `${got}ms`);

  // ---- 2. 新增动作：动作表 + 右键菜单都要多一项 ----
  const add = await post('api/sprites', {
    role: 'p1', label: '探针动作', dur: 2, data: await b64('/assets/roles/p3/jump.png'),
  });
  chk('服务端接受新动作', !!add.ok, JSON.stringify(add).slice(0, 160));
  const key = add.spec && add.spec.key;
  chk('服务端发了新 key', !!key, JSON.stringify(add.spec || {}));
  await evalJS(A, `window.petclub.afterUpload(${JSON.stringify(add.manifest)})`);
  await sleep(900);

  const menuLabels = await evalJS(A, `(() => {
    const app = window.petclub;
    const el = document.querySelector('[data-role="p1"] .pet-hit');
    const r = el.getBoundingClientRect();
    app.onPetMenu('p1', r.left + r.width / 2, r.top + r.height / 2);
    return [...document.querySelectorAll('#ctx-menu .ctx-item')].map((b) => b.textContent.trim());
  })()`);
  chk('右键菜单里立刻出现新动作', menuLabels.includes('探针动作'), JSON.stringify(menuLabels));
  // 自定义动作单独成组：排在烟花后面，中间用双横线隔开
  const order = await evalJS(A, `(() => {
    const out = [];
    for (const el of document.querySelectorAll('#ctx-menu .ctx-item, #ctx-menu .ctx-sep')) {
      out.push(el.classList.contains('ctx-sep') ? '<线>' : el.textContent.trim());
    }
    return out;
  })()`);
  const iFx = order.indexOf('烟花');
  const iMine = order.indexOf('探针动作');
  chk('自定义动作排在烟花之后', iMine > iFx && iFx >= 0, JSON.stringify(order));
  chk('烟花和我的动作之间有一条分隔线',
    order.slice(iFx + 1, iMine).includes('<线>'), JSON.stringify(order));

  chk('新动作的图已进角色图集',
    await evalJS(A, `Object.keys(window.petclub.pets.get('p1').sp.poses).includes(${JSON.stringify(key)})`),
    String(key));

  // ---- 3. 点新动作，真的演出来 ----
  await evalJS(A, `document.querySelector('#ctx-menu .ctx-item[data-key="${key}"]').click()`);
  await sleep(500);
  const acting = await evalJS(A, `(() => {
    const p = window.petclub.pets.get('p1');
    return { pose: p.pose, name: p.poseName };
  })()`);
  chk('点击后角色在演这个动作', acting.poseName === key || acting.pose === key,
    JSON.stringify(acting));

  // ---- 4. 还原：不刷新，当场回到出厂图 ----
  const back = await post('api/sprites/restore', { role: 'p1', action: 'wave' });
  chk('服务端接受还原', !!back.ok, JSON.stringify(back).slice(0, 120));
  await evalJS(A, `window.petclub.afterUpload(${JSON.stringify(back.manifest)})`);
  const restored = await evalJS(A, `new Promise((res) => setTimeout(
    () => res((window.petclub.pets.get('p1') || {sp:{poses:{}}}).sp.poses.wave.src || ''), 900))`);
  chk('还原后不刷新就回到出厂图', restored.includes('/assets/roles/p1/wave.png'), restored);

  // ---- 收尾：把探针动作删掉 ----
  await post('api/sprites/restore', { role: 'p1', action: key });
} catch (e) {
  chk('探针跑通', false, e.message);
} finally {
  if (A) A.close();
  chrome.kill();
}

log(failed.length ? `\n${failed.length} 项失败：${failed.join('、')}` : '\n全部通过');
process.exit(failed.length ? 1 : 0);
