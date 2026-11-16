// 「桌面端发起 → 网页端收到」端到端探针。
//
// 跑法（和 probe_web_custom 一样，先起 server.py + web_gateway.py）：
//   1. python server.py --port 8765
//   2. python web_gateway.py --port 8766
//   3. node tools/probe_web_from_desktop.mjs
//
// 为什么单独测：桌面端走的是**裸 TCP 8765 + 一行一条 JSON**，网页端走的是
// 网关的 WebSocket 8766。两条入口最后都进 server.py 的 event 分支，但
// 只要有一处不对称（字段名、to、kind 截断、成员判定），就会出现
// 「web 发起桌面能看见，桌面发起 web 没反应」这种单向故障。
//
// 这里用 node:net 精确模拟桌面端：连接 8765、发 join、发 event，
// 不依赖任何 GUI。网页端那侧是真的无头 Chrome。
import { spawn } from 'node:child_process';
import net from 'node:net';
import { readFile } from 'node:fs/promises';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = 9334;
const BASE = process.env.PETCLUB_BASE || 'http://127.0.0.1:8766/';
const GAME = process.env.PETCLUB_GAME || '127.0.0.1:8765';
const PROFILE = `${process.env.TEMP || '/tmp'}/petclub-probe-${Date.now()}`;
// 名字每次跑都不一样：同一个身份里动作名不能重复（服务端会拒），
// 跑第二遍还叫「桌面动作」就会撞上上一轮没清干净的残留。
const LABEL = '桌面动作' + String(Date.now() % 10000);

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
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await c.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {});
  return c;
}

/** 桌面端替身：裸 TCP，一行一条 JSON，和 petclub/net.py 发的格式逐字一致。 */
class Desktop {
  constructor(addr) {
    const i = addr.lastIndexOf(':');
    this.host = addr.slice(0, i);
    this.port = Number(addr.slice(i + 1));
    this.lines = [];
    this.waiters = [];
    this.sock = null;
  }

  async open() {
    await new Promise((res, rej) => {
      const s = net.createConnection(this.port, this.host, () => res());
      s.on('error', rej);
      s.setEncoding('utf8');
      let buf = '';
      s.on('data', (chunk) => {
        buf += chunk;
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let m;
          try { m = JSON.parse(line); } catch (_) { continue; }
          this.lines.push(m);
          for (const w of this.waiters.slice()) {
            if (w.pred(m)) {
              this.waiters.splice(this.waiters.indexOf(w), 1);
              w.res(m);
            }
          }
        }
      });
      this.sock = s;
    });
  }

  send(obj) {
    this.sock.write(JSON.stringify(obj) + '\n');
  }

  /** 等一条满足条件的消息（先查已收到的）。 */
  wait(pred, ms = 8000) {
    const hit = this.lines.find(pred);
    if (hit) return Promise.resolve(hit);
    return new Promise((res, rej) => {
      const w = { pred, res };
      this.waiters.push(w);
      setTimeout(() => {
        const i = this.waiters.indexOf(w);
        if (i >= 0) {
          this.waiters.splice(i, 1);
          rej(new Error('桌面端等待超时'));
        }
      }, ms);
    });
  }

  close() {
    try { this.sock && this.sock.destroy(); } catch (_) { /* 忽略 */ }
  }
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

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--window-size=1280,860', '--mute-audio', 'about:blank',
], { stdio: 'ignore' });

let B; let desk; let key = ''; let keyB = '';
try {
  for (let i = 0; i < 60; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).ok) break;
    } catch (_) { /* 还没起来 */ }
    await sleep(200);
  }

  key = await makeAction('p1', LABEL);
  // 网页端那个身份也建一个同名动作：名字一样、图各是各的。
  // 桌面端发起时，网页端该演**自己**那份，而不是照搬 p1 的图号。
  keyB = await makeAction('p2', LABEL);
  chk('给 p1（桌面身份）建好了动作 ' + key + '，p2 也有同名的一份 ' + keyB,
    !!key && !!keyB && key !== keyB);

  B = await openPage(BASE);
  await waitFor(B, 'window.petclub && window.petclub.net && window.petclub.net.connected');
  await evalJS(B, `window.petclub.join('p2')`);
  await waitFor(B, `!!window.petclub.pets.get('p2')`, 15000, 'p2 出生');

  // —— 桌面端登录 ——
  desk = new Desktop(GAME);
  await desk.open();
  desk.send({ t: 'join', role: 'p1', client: 'desktop' });
  const joined = await desk.wait((m) => m.t === 'joined' || m.t === 'reject');
  chk('桌面端认领 p1', joined.t === 'joined', JSON.stringify(joined));
  if (joined.t !== 'joined') throw new Error('桌面端没认领成功');

  await waitFor(B, `!!window.petclub.pets.get('p1')`, 15000, '网页端出现 p1');
  chk('网页端能看到 p1 的角色',
    await evalJS(B, `!!document.querySelector('[data-role="p1"] .pet-hit')`));

  const landed = `(() => {
    const p = window.petclub.pets.get('p1');
    return !!p && p.landed && p.poseName === 'idle';
  })()`;
  await waitFor(B, landed, 15000, '网页端 p1 落地');

  // —— 桌面端下发指令（对全员） ——
  desk.send({ t: 'event', kind: key, text: '', an: LABEL });
  await sleep(600);
  const poseAll = await evalJS(B, `window.petclub.pets.get('p1').poseName`);
  const poseAllB = await evalJS(B, `window.petclub.pets.get('p2').poseName`);
  chk('桌面端发「对全员」的自定义动作，网页端 p1 演了', poseAll === key, poseAll);
  chk('网页端的 p2 跟着演的是他自己那份', poseAllB === keyB, poseAllB);

  // 聊天里该留一条提示
  await evalJS(B, `window.petclub.chat.switch('all')`);
  await sleep(250);
  const tip = await evalJS(B, `(() => {
    const rows = [...document.querySelectorAll('.tip-row')];
    return rows.length ? rows[rows.length - 1].textContent.trim() : '';
  })()`);
  chk('网页端聊天里留下了提示', !!tip, JSON.stringify(tip));
  if (tip) log('       文案：' + tip);

  // —— 桌面端点名发给 p2 ——
  await waitFor(B, landed, 15000, '网页端 p1 回到站立');
  desk.send({ t: 'event', kind: key, text: '', to: 'p2', an: LABEL });
  await sleep(600);
  const poseOne = await evalJS(B, `window.petclub.pets.get('p1').poseName`);
  chk('桌面端点名发给 p2，网页端 p1 也演了', poseOne === key, poseOne);

  // —— 反向对照：to 指到自己身上，网页端就该一动不动 ——
  // 这一条钉的是曾经的 bug：桌面端右键自己时把「我的角色名」当成了发送目标，
  // 服务端只把指令投给发起人，本机演得很正常、别人一条都收不到。
  await waitFor(B, landed, 15000, '网页端 p1 回到站立');
  desk.send({ t: 'event', kind: key, text: '', to: 'p1', an: LABEL });
  await sleep(600);
  const poseSelf = await evalJS(B, `window.petclub.pets.get('p1').poseName`);
  chk('只发给自己的指令不会跑到别人屏幕上',
    poseSelf === 'idle', poseSelf);

  // —— 内置指令方向也确认一遍（排除「只有自定义动作坏」） ——
  await waitFor(B, landed, 15000, '网页端 p1 再次站立');
  desk.send({ t: 'event', kind: 'wave', text: '' });
  await sleep(600);
  const poseWave = await evalJS(B, `window.petclub.pets.get('p1').poseName`);
  chk('桌面端发内置动作（wave），网页端 p1 演了', poseWave === 'wave', poseWave);
} catch (e) {
  chk('探针跑完', false, e.message);
} finally {
  try { B && B.close(); } catch (_) { /* 忽略 */ }
  try { desk && desk.close(); } catch (_) { /* 忽略 */ }
  chrome.kill();
  if (key) await dropAction('p1', key);
  if (keyB) await dropAction('p2', keyB);
}

log(failed.length ? `\n失败 ${failed.length} 项：${failed.join('、')}` : '\n全部通过');
process.exit(failed.length ? 1 : 0);
