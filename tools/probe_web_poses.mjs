// 网页版「我的动作」管理 + 位置记忆的端到端探针。
//
// 跑法：
//   1. python server.py --port 8765
//   2. python web_gateway.py --port 8766
//   3. node tools/probe_web_poses.mjs
//
// 覆盖：
//   · 头像菜单里有「我的动作」→「加个动作…」
//   · 换图页给每个内置动作显示出厂原图，且站立（idle）也在可换之列
//   · 换掉站立 → 菜单里出现「站立 已换 [还原]」→ 还原回出厂图
//   · 拖完刷新，角色回到原地；两个角色记在同一个位置时出生会自动错开
//
// 需要 Node 22+ 和本机 Chrome。
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = 9335;
const BASE = process.env.PETCLUB_BASE || 'http://127.0.0.1:8766/';
const PROFILE = `${process.env.TEMP || '/tmp'}/petclub-probe-poses-${Date.now()}`;

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
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && waiting.has(m.id)) {
      const { res, rej } = waiting.get(m.id);
      waiting.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
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

async function reload(c, role) {
  await c.send('Page.reload', { ignoreCache: true });
  await waitFor(c, 'window.petclub && window.petclub.net && window.petclub.net.connected',
    20000, '重新连上');
  // 「刷新后自动坐回上次身份」有竞态：旧连接还没断，新页面就发现身份被占了。
  // 探针里兜个底，真出问题时也只会退到认领页，不会假通过。
  await sleep(800);
  const needJoin = await evalJS(c, `(() => {
    const cs = document.getElementById('claim-screen');
    return !!cs && !cs.classList.contains('hidden');
  })()`);
  if (needJoin) {
    await evalJS(c, `window.petclub.join('${role}')`);
    await waitFor(c, `document.getElementById('claim-screen').classList.contains('hidden')`,
      15000, '重新进入');
  }
}

async function postIdle(role) {
  const png = await readFile(new URL('../assets/roles/p1/idle.png', import.meta.url));
  const r = await fetch(`${BASE}api/sprites`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, action: 'idle', data: png.toString('base64') }),
  });
  return r.json();
}

async function restoreIdle(role) {
  await fetch(`${BASE}api/sprites/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, action: 'idle' }),
  }).catch(() => {});
}

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--window-size=1280,860', '--mute-audio', 'about:blank',
], { stdio: 'ignore' });

let A; let B;
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

  // ---- 头像菜单里的「我的动作」 ----
  await evalJS(A, `window.petclub.chat.toggleMeMenu()`);
  const hasPoseSub = await evalJS(A, `[...document.querySelectorAll('.me-menu .ctx-parent')]
    .some((b) => b.textContent.includes('我的动作'))`);
  chk('头像菜单里有「我的动作」', hasPoseSub);

  await evalJS(A, `(() => {
    const p = [...document.querySelectorAll('.me-menu .ctx-parent')]
      .find((b) => b.textContent.includes('我的动作'));
    p.parentElement.classList.add('open');
  })()`);
  chk('子菜单里有「加个动作…」',
    await evalJS(A, `!!document.querySelector('.me-menu .pose-sub [data-cmd="pose-add"]')`));

  // ---- 换图页：动作选择带原图 ----
  await evalJS(A, `window.petclub.uploadSprite('p1')`);
  await waitFor(A, `!document.querySelector('.upload-mask').classList.contains('hidden')`,
    10000, '弹层打开');

  const tiles = await evalJS(A, `[...document.querySelectorAll('#up-poses .pose-tile')]
    .map((t) => ({ key: t.dataset.key, name: t.querySelector('.pose-tile-name').textContent,
      src: (t.querySelector('img') || {}).src || '', badge: (t.querySelector('.pose-badge') || {}).textContent || '' }))`);
  chk('第一个格子是「新建动作」', tiles[0] && tiles[0].key === '', JSON.stringify(tiles[0] || {}));
  chk('站立（idle）在可换之列',
    tiles.some((t) => t.key === 'idle' && t.name === '站立'), JSON.stringify(tiles.map((t) => t.key)));
  const idleTile = tiles.find((t) => t.key === 'idle') || {};
  chk('站立显示的是出厂原图', (idleTile.src || '').includes('/assets/roles/p1/idle.png'), idleTile.src);

  const waveTile = tiles.find((t) => t.key === 'wave') || {};
  chk('内置动作显示原图而不是文字',
    (waveTile.src || '').includes('/assets/roles/p1/wave.png') && waveTile.name === '打招呼',
    JSON.stringify(waveTile));

  await evalJS(A, `document.querySelector('#up-poses [data-key="idle"]').click()`);
  chk('选中站立后按钮变成「换这张」',
    await evalJS(A, `document.querySelector('#up-send').textContent`) === '换这张');
  chk('没换过的图不显示还原按钮',
    await evalJS(A, `document.querySelector('#up-reset').classList.contains('hidden')`));
  await evalJS(A, `window.petclub.chat.closeMeMenu()`);
  await evalJS(A, `document.querySelector('.upload-mask').classList.add('hidden')`);

  // ---- 换掉站立 ----
  const body = await postIdle('p1');
  chk('服务端接受替换站立', !!body.ok, JSON.stringify(body).slice(0, 120));

  await reload(A, 'p1');
  await waitFor(A, `!!window.petclub.pets.get('p1')`, 15000, '刷新后 p1 出生');
  const idleSrc = await evalJS(A, `window.petclub.pets.get('p1').sp.poses.idle.src`);
  chk('刷新后站立用的是覆盖图', idleSrc.includes('/sprites/p1/idle.png'), idleSrc);

  // ---- 菜单里能还原 ----
  await evalJS(A, `window.petclub.chat.toggleMeMenu()`);
  const rows = await evalJS(A, `[...document.querySelectorAll('.me-menu .pose-row')]
    .map((r) => ({ key: r.dataset.key, name: r.querySelector('.pose-row-name').textContent,
      act: (r.querySelector('.pose-row-act') || {}).textContent || '' }))`);
  chk('菜单列出「站立 还原」',
    rows.some((r) => r.key === 'idle' && r.act === '还原'), JSON.stringify(rows));

  await evalJS(A, `(() => {
    const p = [...document.querySelectorAll('.me-menu .ctx-parent')]
      .find((b) => b.textContent.includes('我的动作'));
    p.parentElement.classList.add('open');
  })()`);
  await evalJS(A, `document.querySelector('.me-menu .pose-row[data-key="idle"] .pose-row-act').click()`);
  // 还原要跑一趟网络 + 重建所有角色，等它真的换回来，别用固定 sleep 赌
  await waitFor(A, `(() => {
    const p = window.petclub.pets.get('p1');
    return !!p && p.sp.poses.idle.src.includes('/assets/roles/p1/idle.png');
  })()`, 12000, '还原成出厂图');
  const after = await evalJS(A, `window.petclub.pets.get('p1').sp.poses.idle.src`);
  chk('还原后回到出厂图', after.includes('/assets/roles/p1/idle.png'), after);

  // ---- 拖完记住位置 ----
  // 重建过一轮角色，先等新的那只落地（fall 期间 act / 拖动都处在物理态）
  await waitFor(A, `(() => {
    const p = window.petclub.pets.get('p1');
    return !!p && p.landed && p.poseName === 'idle';
  })()`, 15000, 'p1 落地');
  const from = await evalJS(A, `(() => {
    const el = document.querySelector('[data-role="p1"] .pet-hit');
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  const toX = 320;
  await A.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: from.x, y: from.y, button: 'left', buttons: 1, clickCount: 1,
  });
  for (const x of [from.x - 40, from.x - 120, Math.max(60, toX)]) {
    await A.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', x, y: from.y, button: 'left', buttons: 1,
    });
    await sleep(60);
  }
  await A.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: Math.max(60, toX), y: from.y, button: 'left', buttons: 0, clickCount: 1,
  });
  await sleep(300);

  // 存的是 {x, up}：x 是屏幕宽比例，up 是离地面多高 —— 存 y 的绝对比例的话，
  // 换个窗口高度人就会悬在半空 / 压到输入框上（probe_web_ground 盯这一条）。
  const stored = await evalJS(A, `JSON.parse(localStorage.getItem('petclub.spots') || '{}').p1`);
  chk('松手后写进了 localStorage',
    !!stored && typeof stored === 'object' && !Array.isArray(stored)
    && typeof stored.x === 'number' && typeof stored.up === 'number',
    JSON.stringify(stored));
  const draggedX = await evalJS(A, `window.petclub.pets.get('p1').x`);
  chk('确实拖走了', Math.abs(draggedX - from.x) > 40, `${from.x} -> ${draggedX}`);

  await reload(A, 'p1');
  await waitFor(A, `!!window.petclub.pets.get('p1')`, 15000, '刷新后出生');
  const back = await evalJS(A, `window.petclub.pets.get('p1').x`);
  chk('刷新后回到拖到的位置', Math.abs(back - draggedX) < 6, `${draggedX} -> ${back}`);

  // ---- 两个角色记在同一个位置 → 自动错开 ----
  B = await openPage(BASE);
  await waitFor(B, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(B, `window.petclub.join('p2')`);
  await waitFor(B, `!!window.petclub.pets.get('p2')`, 15000, 'p2 出生');
  await waitFor(A, `!!window.petclub.pets.get('p2')`, 15000, 'p1 页面上出现 p2');

  await evalJS(A, `localStorage.setItem('petclub.spots', JSON.stringify({
    p1: { x: 0.35, up: 0 }, p2: { x: 0.35, up: 0 } }))`);
  await reload(A, 'p1');
  await waitFor(A, `!!window.petclub.pets.get('p1')`, 20000, 'p1 回来');
  await waitFor(A, `!!window.petclub.pets.get('p2')`, 20000, 'p2 回来');
  const gap = await evalJS(A, `Math.abs(window.petclub.pets.get('p1').x - window.petclub.pets.get('p2').x)`);
  chk('同一位置出生时自动错开', gap >= 56, `${gap.toFixed(1)}px`);
} catch (e) {
  chk('探针跑完', false, e.message);
} finally {
  try { A && A.close(); } catch (_) { /* 忽略 */ }
  try { B && B.close(); } catch (_) { /* 忽略 */ }
  chrome.kill();
  await restoreIdle('p1');
}

log(failed.length ? `\n失败 ${failed.length} 项：${failed.join('、')}` : '\n全部通过');
process.exit(failed.length ? 1 : 0);
