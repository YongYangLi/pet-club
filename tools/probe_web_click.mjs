// 网页版「点别人也有反应」端到端探针。
//
// 跑法：
//   1. python server.py --port 8865
//   2. python web_gateway.py --port 8866 --upstream 127.0.0.1:8865
//   3. node tools/probe_web_click.mjs            （或 PETCLUB_BASE=http://127.0.0.1:8766/）
//
// 要证明的是四件事：
//   · 点别人的角色，别人的角色在我这儿会动（有反馈）
//   · 他自己那边什么都没发生 —— 这不是远程互动
//   · 聊天里不落任何痕迹
//   · 说的话和点自己同一套（他自己的台词，不是「你摸了摸…」那种旁白）
//
// 反过来的「点自己还有反应」也顺手验一遍：这次改动只是放开了 role 判断，
// 不该把原来那条路弄坏。
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = Number(process.env.CDP_PORT || 9337);
const BASE = process.env.PETCLUB_BASE || 'http://127.0.0.1:8766/';
const PROFILE = `${process.env.TEMP || '/tmp'}/petclub-click-${Date.now()}`;

const failed = [];
function chk(name, ok, detail = '') {
  console.log(`${ok ? '[ok]  ' : '[FAIL]'} ${name}${!ok && detail ? '  <- ' + detail : ''}`);
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
  return { ready, send };
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
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await c.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {});
  return c;
}

async function join(c, role) {
  await waitFor(c, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(c, `window.petclub.join('${role}')`);
  await waitFor(c, `!!window.petclub.pets.get('${role}')`, 15000, role + ' 出生');
}

// 点角色身上某个高度（0=头顶，1=脚边）。左键按一下就走，不移动，不算拖动。
// 坐标必须取 .pet-hit：带 data-role 的那个外层节点里还有别的（气泡等）元素，
// 取到第一个不一定是角色本身，拿到的 rect 会是 0×0。
async function tap(c, role, frac) {
  const p = await evalJS(c, `(() => {
    const el = document.querySelector('[data-role="${role}"] .pet-hit');
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height * ${frac} };
  })()`);
  await c.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: p.x, y: p.y, button: 'left', buttons: 1, clickCount: 1,
  });
  await sleep(60);
  await c.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: p.x, y: p.y, button: 'left', buttons: 0, clickCount: 1,
  });
  await sleep(250);
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
  await join(A, 'p1');
  B = await openPage(BASE);
  await join(B, 'p2');
  await waitFor(A, `!!window.petclub.pets.get('p2')`, 15000, 'p1 页面出现 p2');

  const idle = (role) => `(() => {
    const p = window.petclub.pets.get('${role}');
    return !!p && p.landed && p.poseName === 'idle';
  })()`;
  await waitFor(A, idle('p2'), 15000, 'p2 在 A 页面落地');
  await waitFor(B, idle('p2'), 15000, 'p2 在自己页面落地');
  chk('两个页面都准备好了', true);

  const tipsBefore = await evalJS(A, `document.querySelectorAll('.msg-tip').length`);

  // ---- 点别人的头 ----
  await tap(A, 'p2', 0.15);
  const aHead = await evalJS(A, `window.petclub.pets.get('p2').poseName`);
  const aSelf = await evalJS(A, `window.petclub.pets.get('p1').poseName`);
  chk('点别人的角色，他在这边动了', aHead !== 'idle', aHead);
  chk('点别人不会动到自己', aSelf === 'idle', aSelf);

  // 远程那端不该有变化：从头到尾没发过 event
  const bHead = await evalJS(B, `window.petclub.pets.get('p2').poseName`);
  chk('他自己那边完全没动静（不是远程互动）', bHead === 'idle', bHead);

  const tipsAfter = await evalJS(A, `document.querySelectorAll('.msg-tip').length`);
  chk('聊天里没留下任何记录', tipsAfter === tipsBefore, `${tipsBefore} -> ${tipsAfter}`);

  // 和点自己同一套：说他自己那句台词（文案在 meta 的 voice 里），不套括号。
  const meta = await (await fetch(new URL('api/meta', BASE))).json();
  const pool = (role, key) => (meta.voice && meta.voice[role]
    && meta.voice[role][`s.${key}`]) || [];
  const bubbleText = await evalJS(A, `(() => {
    const it = window.petclub.pets.bubbles.items.get('p2');
    return it && it.el.classList.contains('show') ? it.el.textContent.trim() : '';
  })()`);
  chk('点别人时头顶说的是他自己那句台词（和点自己同一套）',
    pool('p2', 'pet').includes(bubbleText),
    `${bubbleText} / ${JSON.stringify(pool('p2', 'pet'))}`);
  chk('不再套括号旁白', !!bubbleText && !bubbleText.startsWith('（'), bubbleText);
  const bubbleOnly = await evalJS(A, `(() => {
    const it = window.petclub.pets.bubbles.items.get('p1');
    return !it || !it.el.classList.contains('show');
  })()`);
  chk('气泡只挂在被点的人头上', bubbleOnly);

  // ---- 等动作放完，再点脚边 ----
  await sleep(1600);
  await tap(A, 'p2', 0.95);
  const feet = await evalJS(A, `window.petclub.pets.get('p2').poseName`);
  chk('点脚边是另一个动作（轻拍）', feet === 'knock', feet);
  const feetText = await evalJS(A, `(() => {
    const it = window.petclub.pets.bubbles.items.get('p2');
    return it && it.el.classList.contains('show') ? it.el.textContent.trim() : '';
  })()`);
  chk('脚边的台词跟着部位换（轻拍那句）', pool('p2', 'pat').includes(feetText),
    `${feetText} / ${JSON.stringify(pool('p2', 'pat'))}`);

  // ---- 点自己仍然正常 ----
  await sleep(1600);
  await tap(A, 'p1', 0.15);
  const mine = await evalJS(A, `window.petclub.pets.get('p1').poseName`);
  chk('点自己照样有反应', mine !== 'idle', mine);
  const selfBubble = await evalJS(A, `(() => {
    const it = window.petclub.pets.bubbles.items.get('p1');
    return !!it && it.el.classList.contains('show');
  })()`);
  chk('点自己会冒一句（自己的气泡）', selfBubble);

  console.log(`\n通过 ${failed.length ? '有失败' : '全部'}，失败 ${failed.length} 项`);
  if (failed.length) console.log('失败：' + failed.join('、'));
} catch (e) {
  console.log('[ERR] ' + (e && e.message ? e.message : e));
  failed.push('探针异常');
} finally {
  chrome.kill();
  await sleep(200);
  process.exit(failed.length ? 1 : 0);
}
