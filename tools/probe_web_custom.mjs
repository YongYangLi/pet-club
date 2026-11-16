// 网页版「同名自定义动作」端到端探针。
//
// 跑法：
//   1. python server.py --port 8765
//   2. python web_gateway.py --port 8766
//   3. node tools/probe_web_custom.mjs
//
// 语义（和桌面端 App._perform_custom 一致）：
//   · 动作是「我做」的，菜单里列的永远是我自己的那一组 —— 右键别人只是
//     把目标收窄成「只对他」；
//   · 名字是共享语义，图是各人一份：A 有「比耶」、B 也有「比耶」，
//     A 发起时 B 演的是**他自己那张图**，不是 A 的；
//   · 对全员时桌上所有有同名动作的人都跟上，没有的人站着不动。
//
// 这条路径单独测的原因：姿势名（c1、c2…）只标识「谁的那张图」。让收信端
// 直接演 kind，他会去翻一个自己没有的姿势，渲染回落 idle —— 表现就是
// 「点了没反应」，而且不报任何错。tools/test_custom.py 只能测到路由决策，
// 这里测的是真的浏览器。
//
// 需要 Node 22+（用全局 fetch / WebSocket）和本机 Chrome。
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = 9333;
const BASE = process.env.PETCLUB_BASE || 'http://127.0.0.1:8766/';
const PROFILE = `${process.env.TEMP || '/tmp'}/petclub-probe-${Date.now()}`;
// 名字每次跑都不一样：同一个身份里动作名不能重复（服务端会拒），
// 跑第二遍还叫「比耶」就会撞上上一轮没清干净的残留。
const LABEL = '比耶' + String(Date.now() % 10000);

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
    } catch (_) { /* 页面还没就绪 */ }
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
  // 后台标签页会暂停 rAF，角色就永远停在画面外（还在 fall 阶段，
  // act() 会被忽略）。让它以为自己是前台。
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await c.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {});
  return c;
}

async function makeAction(role, label) {
  const png = await readFile(new URL('../assets/roles/p2/wave.png', import.meta.url));
  const r = await fetch(`${BASE}api/sprites`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, data: png.toString('base64'), label, dur: 2.5 }),
  });
  const j = await r.json();
  if (!j.ok) throw new Error('建动作失败：' + JSON.stringify(j));
  return j.spec.key;
}

async function dropAction(role, key) {
  await fetch(`${BASE}api/sprites/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, action: key }),
  }).catch(() => {});
}

async function rightClick(c, role) {
  const box = await evalJS(c, `(() => {
    const el = document.querySelector('[data-role="${role}"] .pet-hit');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  if (!box) throw new Error('找不到 ' + role + ' 的角色');
  await c.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: box.x, y: box.y, button: 'right', buttons: 2, clickCount: 1,
  });
  await c.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: box.x, y: box.y, button: 'right', buttons: 0, clickCount: 1,
  });
  await sleep(250);
}

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--window-size=1280,860', '--mute-audio', 'about:blank',
], { stdio: 'ignore' });

let A; let B; let keyA = ''; let keyB = '';
try {
  for (let i = 0; i < 60; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).ok) break;
    } catch (_) { /* 还没起来 */ }
    await sleep(200);
  }

  // 两个身份各建一个同名动作：名字一样，图 / 编号各是各的
  keyA = await makeAction('p1', LABEL);
  keyB = await makeAction('p2', LABEL);
  chk(`p1 的「${LABEL}」是 ${keyA}，p2 的是 ${keyB}`, !!keyA && !!keyB && keyA !== keyB);

  A = await openPage(BASE);
  await waitFor(A, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(A, `window.petclub.join('p1')`);
  await waitFor(A, `!!window.petclub.pets.get('p1')`, 15000, 'p1 出生');

  B = await openPage(BASE);
  await waitFor(B, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(B, `window.petclub.join('p2')`);
  await waitFor(B, `!!window.petclub.pets.get('p2')`, 15000, 'p2 出生');

  await waitFor(A, `!!window.petclub.pets.get('p2')`, 15000, 'p1 页面出现 p2');

  // 落地静止：fall 期间 act() 会被直接忽略
  const landed = (role) => `(() => {
    const p = window.petclub.pets.get('${role}');
    return !!p && p.landed && p.poseName === 'idle';
  })()`;
  await waitFor(A, landed('p2'), 15000, 'p1 页面上 p2 落地');
  await waitFor(B, landed('p2'), 15000, 'p2 自己页面落地');

  // —— 右键别人：菜单列的是我自己的动作 ——
  await rightClick(A, 'p2');
  const menu = await evalJS(A, `[...document.querySelectorAll('#ctx-menu .ctx-item')]
    .map(b => b.dataset.key || b.dataset.cmd).join(',')`);
  chk('右键 p2 的菜单里有我的「' + LABEL + '」', menu.includes(keyA), menu);
  chk('菜单里没有对方的同名动作（那是他的图）',
    !menu.split(',').includes(keyB), menu);

  // —— 点名 p2：我和他各做各的那份 ——
  await evalJS(A, `document.querySelector('#ctx-menu [data-key="${keyA}"]').click()`);
  await sleep(500);

  const aP1 = await evalJS(A, `window.petclub.pets.get('p1').poseName`);
  const aP2 = await evalJS(A, `window.petclub.pets.get('p2').poseName`);
  const bP1 = await evalJS(B, `window.petclub.pets.get('p1').poseName`);
  const bP2 = await evalJS(B, `window.petclub.pets.get('p2').poseName`);

  chk('发起方（p1）演自己那份', aP1 === keyA, aP1);
  chk('被点的人（p2）演他自己那份', aP2 === keyB, aP2);
  chk('对方屏幕上也是各演各的', bP1 === keyA && bP2 === keyB, `${bP1}/${bP2}`);

  // 提示落在和 p2 的会话里
  await evalJS(A, `window.petclub.chat.switch('p2')`);
  await sleep(250);
  const tip = await evalJS(A, `(() => {
    const rows = [...document.querySelectorAll('.tip-row')];
    return rows.length ? rows[rows.length - 1].textContent.trim() : '';
  })()`);
  chk('聊天里留了一条非空提示', !!tip, JSON.stringify(tip));
  if (tip) log('       文案：' + tip);

  // —— 对全员：有同名动作的都跟上 ——
  // 开 B 之后 A 变成后台页，rAF 会被暂停：动作的计时器不走，姿势永远停在
  // 那一帧。把 A 提到前台再等，否则这里等的是「后台标签页也会动」。
  await A.send('Page.bringToFront');
  await A.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await waitFor(A, landed('p1'), 15000, 'p1 回到站立（自己）');
  await waitFor(A, landed('p2'), 15000, 'p1 页面上 p2 回到站立');
  await rightClick(A, 'p1');
  const menu2 = await evalJS(A, `[...document.querySelectorAll('#ctx-menu .ctx-item')]
    .map(b => b.dataset.key || b.dataset.cmd).join(',')`);
  chk('右键自己也能看到我的动作', menu2.includes(keyA), menu2);
  await evalJS(A, `document.querySelector('#ctx-menu [data-key="${keyA}"]').click()`);
  await sleep(500);

  const c1 = await evalJS(A, `window.petclub.pets.get('p1').poseName`);
  const c2 = await evalJS(A, `window.petclub.pets.get('p2').poseName`);
  chk('对全员：两个人各做各的', c1 === keyA && c2 === keyB, `${c1}/${c2}`);
} catch (e) {
  chk('探针跑完', false, e.message);
} finally {
  try { A && A.close(); } catch (_) { /* 忽略 */ }
  try { B && B.close(); } catch (_) { /* 忽略 */ }
  chrome.kill();
  if (keyA) await dropAction('p1', keyA);
  if (keyB) await dropAction('p2', keyB);
}

log(failed.length ? `\n失败 ${failed.length} 项：${failed.join('、')}` : '\n全部通过');
process.exit(failed.length ? 1 : 0);
