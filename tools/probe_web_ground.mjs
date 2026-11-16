// 网页版「重新进来后落点不对 / 拖不下去」的端到端探针。
//
// 跑法（不要用线上那套端口，会污染真实 data/）：
//   1. python .tmp/srv_test.py
//   2. python .tmp/gw_test.py
//   3. node tools/probe_web_ground.mjs
//
// 用户的说法是「偶发」：他大多数时候看到的是正常的，偶尔某个人物悬在半空、
// 而且怎么拖都拖不下来，仿佛「这个人的底就在那儿」。原因是这两件事共用
// 了一个变量 —— 出场时的 ground：
//
//   const ground = spot ? spot[1] * innerHeight : groundY();
//
// ground 是「脚底」，同时也是拖动时 y 的上限。拿上次记住的落点当 ground，
// 就等于把这层楼封成了天花板：人停在那个高度，而且永远拖不下去。
// 只有「上次记过位置、且那个位置和现在的真实地面不一样」时才会出现，
// 所以看起来是偶发。
//
// 探针盯四条：
//   · 记过的位置在真实地面以上 → 人还在那儿（这是「拖到哪儿待哪儿」）
//   · 但脚底（ground）永远是真实地面 → 拖得下去
//   · 记的是旧窗口高度下的地面 → 换高度后仍落回**现在**的地面
//   · 从来没记过 → 落在地面
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = 9335;
const BASE = process.env.PETCLUB_BASE || 'http://127.0.0.1:8776/';
const PROFILE = `${process.env.TEMP || '/tmp'}/petclub-ground-${Date.now()}`;

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

// 进页面 → 认领 → 等落地，返回一份现场读数
async function enter(c, role) {
  await evalJS(c, `window.petclub.join(${JSON.stringify(role)})`);
  await waitFor(c, `!!window.petclub.pets.get(${JSON.stringify(role)})`, 15000, role + ' 出生');
  await waitFor(c, `window.petclub.pets.get(${JSON.stringify(role)}).landed`, 15000,
    role + ' 落地');
  await sleep(200);
  return evalJS(c, `(() => {
    const p = window.petclub.pets.get(${JSON.stringify(role)});
    return {
      y: p.y,
      ground: p.ground,
      floor: window.petclub.pets.groundY(),
      winH: window.innerHeight,
      lowest: p.clamp(p.x, 1e6).y,
    };
  })()`);
}

async function reloadWith(c, url) {
  await c.send('Page.navigate', { url });
  await waitFor(c, 'window.petclub && window.petclub.net && window.petclub.net.connected',
    20000, '重新连上');
}

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--window-size=1280,860', '--mute-audio', 'about:blank',
], { stdio: 'ignore' });

let A;
try {
  for (let i = 0; i < 60; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).ok) break;
    } catch (_) { /* 还没起来 */ }
    await sleep(200);
  }

  A = await openPage(BASE);
  await waitFor(A, 'window.petclub && window.petclub.net && window.petclub.net.connected');

  // ---- 0. 干净状态：没记过位置，落在地面 ----
  await evalJS(A, `localStorage.removeItem('petclub.spots')`);
  await reloadWith(A, BASE);
  const fresh = await enter(A, 'p1');
  chk('没记过位置：落在真实地面上',
    Math.abs(fresh.y - fresh.floor) <= 1, JSON.stringify(fresh));
  chk('脚底就是真实地面', Math.abs(fresh.ground - fresh.floor) <= 1,
    JSON.stringify(fresh));

  // ---- 1. 记过一个「半空中」的位置 ----
  // 这是用户手动拖上去的那一层：人应该还在那儿（拖到哪儿待哪儿），
  // 但脚底（拖动上限）必须是真实地面，不能封在这一层。
  const want = Math.round(fresh.floor - 160);
  await evalJS(A, `(() => {
    const spots = {};
    spots.p1 = { x: 0.5, up: ${160} / window.innerHeight };
    localStorage.setItem('petclub.spots', JSON.stringify(spots));
  })()`);
  await reloadWith(A, BASE);
  const high = await enter(A, 'p1');
  chk('记过的半空位置：人还在那一层',
    Math.abs(high.y - want) <= 3, `${high.y} vs ${want}`);
  chk('但脚底已经是真实地面（不是记住的那层）',
    Math.abs(high.ground - high.floor) <= 1,
    `ground=${high.ground} floor=${high.floor}`);
  chk('拖得下去：能一直拖到地面',
    Math.abs(high.lowest - high.floor) <= 1,
    `最低只能到 ${high.lowest}，地面 ${high.floor}`);

  // ---- 2. 旧格式（整屏比例）站在旧地面上，换了窗口高度 ----
  // 上一次是 860 高，地面比例约 0.86；这次窗口 700 高，真实地面更低。
  await evalJS(A, `localStorage.setItem('petclub.spots',
    JSON.stringify({ p1: [0.5, 0.86] }))`);
  await A.send('Emulation.setDeviceMetricsOverride', {
    width: 1180, height: 700, deviceScaleFactor: 1, mobile: false,
  });
  await reloadWith(A, BASE);
  const tall = await enter(A, 'p1');
  chk('换窗口高度后仍落回**现在**的地面',
    Math.abs(tall.y - tall.floor) <= 3, JSON.stringify(tall));
  chk('旧格式不会把人顶在半空', tall.ground <= tall.floor + 1, JSON.stringify(tall));

  // ---- 3. 拖动之后记下来的是「离地面多高」，换个高度也还在同一层 ----
  await A.send('Emulation.clearDeviceMetricsOverride');
  await reloadWith(A, BASE);
  const back = await enter(A, 'p1');
  await evalJS(A, `(() => {
    const p = window.petclub.pets.get('p1');
    p.grab(); p.moveTo(p.x, p.y - 120); p.release();
  })()`);
  const saved = await evalJS(A, `localStorage.getItem('petclub.spots')`);
  chk('拖完存下来的是离地面的高度', saved.includes('"up"'), saved);
  await reloadWith(A, BASE);
  const again = await enter(A, 'p1');
  chk('再进来还是那一层（离地面 120）',
    Math.abs((again.floor - again.y) - 120) <= 3,
    `离地面 ${again.floor - again.y}`);
  chk('这一层也拖得下去', Math.abs(again.lowest - again.floor) <= 1,
    `最低只能到 ${again.lowest}，地面 ${again.floor}`);
  chk('窗口高度回原样时仍贴着地面那一层',
    Math.abs(again.floor - back.floor) <= 1, `${again.floor} vs ${back.floor}`);

  // ---- 4. 用户报的就是「一直刷」：连刷几次不能有时落地面、有时卡半空 ----
  // 以前 ground 只在 ResizeObserver / resize 事件里被纠正，所以同样是刷新，
  // 有的这一次纠正过了、有的没纠正 —— 看起来完全随机。
  const rounds = [];
  let stable = true;
  let draggable = true;
  for (let i = 0; i < 5; i += 1) {
    await reloadWith(A, BASE);
    const r = await enter(A, 'p1');
    rounds.push(Math.round(r.floor - r.y));
    if (Math.abs((r.floor - r.y) - 120) > 3) stable = false;
    if (Math.abs(r.lowest - r.floor) > 1) draggable = false;
  }
  chk('连刷 5 次都停在原来那一层（不是随机落地面/半空）',
    stable, JSON.stringify(rounds));
  chk('连刷 5 次都拖得下来', draggable, JSON.stringify(rounds));
} catch (e) {
  chk('探针跑通', false, e.message);
} finally {
  if (A) A.close();
  chrome.kill();
}

log(failed.length ? `\n${failed.length} 项失败：${failed.join('、')}` : '\n全部通过');
process.exit(failed.length ? 1 : 0);
