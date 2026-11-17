// 悬浮在网页上的角色。
// 物理和 actors.py 一一对应：fall / hold / rise / crawl / idle。
// 位置只存在本机，拖动、起飞、集合落点都不上网 —— 和桌面版一样。
import { state, roleName } from './state.js';
import { loadRole } from './sprites.js';
import { BubbleLayer } from './bubble.js';
import { moveOffset } from './motion.js';

const CRAWL_FRAMES = ['crawl_1', 'crawl_2'];
const CRAWL_FRAME_SEC = 0.16;
const CRAWL_BODY_PER_SEC = 1.5;

// 「上次把谁放在哪儿」。x 存屏幕宽度比例，y 存**离地面多高**。
// 不能存整屏比例：地面会动（输入框变高、窗口变矮），同一个比例换算出来的
// 高度和现在的地面差几十像素 —— 人会悬在半空，或者压在输入框上，
// 而且拖动上限（地面）也被这个数带偏，看起来就是「怎么拖都拖不下去」。
const SPOT_KEY = 'petclub.spots';

function readSpots() {
  try {
    const v = JSON.parse(localStorage.getItem(SPOT_KEY) || '{}');
    return (v && typeof v === 'object' && !Array.isArray(v)) ? v : {};
  } catch (_) {
    return {};
  }
}

// 读成像素：{x, y}。floor 是当前地面（脚底）的高度。
function readSpot(role, floor) {
  const s = readSpots()[role];
  if (Array.isArray(s)) {
    // 旧格式：[x, y] 都是整屏比例。y 是绝对高度，只能夹到地面以内 ——
    // 旧数据没记过「地面在哪儿」，按现在的算。
    if (s.length < 2 || !Number.isFinite(s[0]) || !Number.isFinite(s[1])) return null;
    return {
      x: s[0] * window.innerWidth,
      y: Math.min(s[1] * window.innerHeight, floor),
    };
  }
  if (s && typeof s === 'object') {
    const x = Number(s.x);
    const up = Number(s.up);
    if (!Number.isFinite(x)) return null;
    return {
      x: x * window.innerWidth,
      y: floor - (Number.isFinite(up) ? Math.max(0, up) : 0) * window.innerHeight,
    };
  }
  return null;
}

function writeSpot(role, x, y, floor) {
  try {
    const all = readSpots();
    all[role] = {
      x: Math.round((x / Math.max(1, window.innerWidth)) * 1e5) / 1e5,
      up: Math.round((Math.max(0, floor - y) / Math.max(1, window.innerHeight)) * 1e5) / 1e5,
    };
    localStorage.setItem(SPOT_KEY, JSON.stringify(all));
  } catch (_) { /* 隐私模式下写不进去，位置就只在这一次有效 */ }
}

export class PetLayer {
  constructor({ onPetClick, onMenu } = {}) {
    this.onPetClick = onPetClick;
    this.onClick = onPetClick;
    this.onMenu = onMenu;
    this.root = document.getElementById('pet-layer');
    this.bubbles = new BubbleLayer();
    this.pets = new Map();
    this.spawning = new Set();
    this.last = performance.now();
    this.pointerX = window.innerWidth / 2;
    this.pointerY = window.innerHeight / 2;
    this.toy = null;
    this._loop = this._loop.bind(this);
    window.addEventListener('pointermove', (e) => {
      this.pointerX = e.clientX;
      this.pointerY = e.clientY;
    }, { passive: true });
    requestAnimationFrame(this._loop);
  }

  startToy(role) {
    if (!this.pets.has(role)) return;
    this.toy = { role, until: performance.now() / 1000 + 8 };
  }

  get(role) { return this.pets.get(role) || null; }
  has(role) { return this.pets.has(role); }

  groundY() {
    // 全屏聊天时，角色站在输入卡片的上沿：这样宠物完整可见，
    // 又不会压住输入框；没有输入卡片时退回屏幕底部。
    const composer = document.querySelector('.composer');
    if (composer) {
      const top = composer.getBoundingClientRect().top;
      if (top > 100) return top - 6;
    }
    return window.innerHeight - (state.meta?.ground_margin ?? 34);
  }

  sizeFactor() {
    return state.meta?.pet_size_factor?.[state.petSize] ?? 1;
  }

  async sync(players) {
    const jobs = [];
    for (const role of state.roles) {
      const online = !!players[role];
      if (online && !this.pets.has(role)) {
        jobs.push(this.spawn(role));
      } else if (!online && this.pets.has(role)
        && !(state.me && state.me.role === role)) {
        this.remove(role);
      }
    }
    await Promise.all(jobs);
  }

  async spawn(role, { drop = true, landingText = '' } = {}) {
    if (this.pets.has(role) || this.spawning.has(role)) return;
    this.spawning.add(role);
    try {
      const sp = await loadRole(role);
      const isMine = state.me && state.me.role === role;
      if (!state.players[role] && !isMine) return;
      if (this.pets.has(role)) return;
      const i = state.roles.indexOf(role);
      const f = this.sizeFactor();
      // 地面永远按现在这一屏算；记住的位置只是「这次停在哪儿」。
      const floor = this.groundY();
      const spot = readSpot(role, floor);
      const x0 = spot ? spot.x
        : window.innerWidth * (i + 1) / (state.roles.length + 1);
      const y0 = spot
        ? Math.min(Math.max(spot.y, sp.h * f), floor)
        : floor;
      const x = this.freeSpot(x0, y0, sp, role);
      const pet = new Pet(this, role, sp, x, floor, drop, y0);
      pet.landingText = landingText;
      this.pets.set(role, pet);
    } catch (e) {
      console.warn('spawn failed', role, e);
    } finally {
      this.spawning.delete(role);
    }
  }

  // 这个位置跟别人**真的重叠了**，才往旁边挪一个身位。
  // 判定是二维的：一上一下分两层站着、彼此根本不挡的人不该被推开
  // （桌面端以前只比 x，实测把三层站位的人推了 90 / 270 像素）。
  freeSpot(x, y, sp, role) {
    const pad = 12;
    const w = window.innerWidth;
    const factor = this.sizeFactor();
    const bodyW = (sp.bodyW || sp.w || 100) * factor;
    const myH = (sp.h || 240) * factor;
    const gap = Math.max(56, Math.min(160, bodyW * 0.9));
    const others = [...this.pets.entries()]
      .filter(([r]) => r !== role)
      .map(([, p]) => ({
        x: p.x,
        // 还在下落的按落点算：那时 y 是负数，会被当成「上下两层」。
        // 落点可能是记住的那一层（半空），不是地面。
        y: (p.phase === 'fall' || p.phase === 'rise')
          ? (p.landY === null ? p.ground : p.landY) : p.y,
        w: (p.sp?.bodyW || p.sp?.w || 100) * p.scale,
        h: p.winH,
      }));
    const clamp = (v) => Math.max(pad, Math.min(w - pad, v));
    const free = (v) => others.every((o) => (
      Math.abs(o.y - y) >= ((myH + o.h) / 2) * 0.85
      || Math.abs(o.x - v) >= ((bodyW + o.w) / 2) * 0.9
    ));

    x = clamp(x);
    if (free(x)) return x;
    for (let k = 1; k <= 4; k += 1) {
      for (const dir of [1, -1]) {
        const cand = clamp(x + dir * gap * k);
        if (free(cand)) return cand;
      }
    }
    return x;
  }

  rememberSpot(role, x, y, floor) {
    writeSpot(role, x, y, floor);
  }

  remove(role) {
    const pet = this.pets.get(role);
    if (pet) pet.remove();
    this.pets.delete(role);
    this.bubbles.remove(role);
  }

  clear() {
    for (const role of [...this.pets.keys()]) this.remove(role);
    this.bubbles.clear();
  }

  // 重建贴图。传了 only 就只换这几个人，其余原样不动 —— 别人换了
  // 他自己的图，我屏幕上不该所有人都掉一下再站起来。
  async rebuild(only = null) {
    let roles;
    if (only && only.length) {
      roles = [...only];
    } else {
      const s = new Set(this.pets.keys());
      for (const role of state.roles) {
        if (state.players[role] || (state.me && state.me.role === role)) s.add(role);
      }
      roles = [...s];
    }
    // 重建是为了换贴图（素材更新 / 改桌宠大小），不是重新站位：
    // 用户拖到哪儿就还回哪儿，否则每传一张图所有人的角色都弹回默认位置。
    const spot = new Map();
    for (const [role, pet] of this.pets) {
      if (roles.includes(role)) spot.set(role, [pet.x, pet.y]);
    }
    // 局部重建不碰别人的气泡，也不该让主角从天上掉下来 —— 换张图而已。
    if (only && only.length) {
      for (const role of roles) this.remove(role);
    } else {
      this.clear();
    }
    for (const role of roles) {
      await this.spawn(role, { drop: !only || !only.length });
      const pet = this.pets.get(role);
      if (pet && spot.has(role)) {
        [pet.x, pet.y] = spot.get(role);
        pet.render();
      }
    }
  }

  play(role, what, dur, move = null) {
    const pet = this.pets.get(role);
    if (!pet) return;
    if (what === 'launch') pet.takeOff();
    else if (what && dur) pet.act(what, dur, move);
  }

  say(role, text) {
    this.bubbles.say(role, text);
  }

  sayWhenLanded(role, text) {
    const pet = this.pets.get(role);
    if (pet) pet.landingText = text;
  }

  gather(anchor, only = null) {
    const host = this.pets.get(anchor);
    if (!host) return [];
    const roles = [...this.pets.keys()].filter((r) => (
      r !== anchor && (only === null || only.includes(r))
    ));
    if (!roles.length) return [];

    const pets = roles.map((r) => this.pets.get(r));
    const body = Math.max(host.bodyW, ...pets.map((p) => p.bodyW));
    const half = body / 2;
    const targets = gatherTargets(
      host.x,
      pets.map((p) => p.x),
      pets.map((p) => p.bodyW),
      host.bodyW,
      body * (state.meta?.gather_gap ?? 0.10),
      half,
      Math.max(half, window.innerWidth - half),
    );
    roles.forEach((r, i) => this.pets.get(r).startCrawl(targets[i], anchor.y));
    return roles;
  }

  resize() {
    for (const pet of this.pets.values()) pet.onResize();
  }

  _loop(now) {
    const dt = Math.min(0.05, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    for (const pet of this.pets.values()) pet.update(dt);
    this._toyTick(now / 1000);
    this.bubbles.update();
    requestAnimationFrame(this._loop);
  }

  _toyTick(now) {
    const toy = this.toy;
    if (!toy) return;
    const pet = this.pets.get(toy.role);
    if (!pet || now >= toy.until) {
      if (pet) pet.stopChase();
      this.toy = null;
      return;
    }
    if (pet.phase === 'hold' || pet.phase === 'fall'
      || pet.phase === 'rise' || !pet.landed) return;
    const half = pet.winW / 2;
    const targetX = Math.min(Math.max(this.pointerX, half),
      Math.max(half, window.innerWidth - half));
    const targetY = Math.min(Math.max(this.pointerY, pet.winH),
      Math.max(pet.winH, this.groundY()));
    if (Math.abs(this.pointerX - pet.x) > 48
      || Math.abs(targetY - pet.y) > 48) {
      pet.chase(targetX, targetY);
    } else {
      pet.stopChase();
      if (!toy.hop || now >= toy.hop) {
        toy.hop = now + 1.2;
        pet.act('jump', 0.6);
      }
    }
  }
}

class Pet {
  constructor(layer, role, sp, x, ground, drop, landY = null) {
    this.layer = layer;
    this.role = role;
    this.sp = sp;
    this.scale = layer.sizeFactor();
    this.winW = sp.w * this.scale;
    this.winH = sp.h * this.scale;
    this.bodyW = sp.bodyW * this.scale;
    this.top = -this.winH - 40;
    this.ground = ground;
    this.x = x;
    this.y = drop ? this.top : ground;
    this.vy = 0;
    this.phase = drop ? 'fall' : 'idle';
    this.pose = drop ? 'fall' : 'idle';
    this.poseName = '';
    this.anim = null;
    this.animT = 0;
    this.animFps = 8;
    // 带轨迹的动作（御剑飞行）：起点 + 进度 + 时长，见 motion.js
    this.moveCfg = null;
    this.moveT = 0;
    this.moveDur = 0;
    this.moveX0 = 0;
    this.moveY0 = 0;
    this.timer = 0;
    this.landed = !drop;
    this.crawlTo = null;
    this.crawlToY = null;
    this.landY = null;
    this.afterCrawl = null;
    this.crawlT = 0;
    this.landingText = '';
    this.drag = null;
    this.alive = true;
    // 「这次停在哪儿」：可以比地面高（上次被摆在半空）。ground 仍只是地面，
    // 用来定拖动上限和默认落点 —— 以前两者合一，记住的落点就成了天花板。
    if (landY !== null && landY < ground - 0.5) {
      if (drop) this.landY = landY;
      else this.y = landY;
    }

    this.el = document.createElement('div');
    this.el.className = 'pet';
    this.el.dataset.role = role;
    this.el.style.width = `${this.winW}px`;
    this.el.style.height = `${this.winH}px`;

    this.img = document.createElement('img');
    this.img.alt = roleName(role);
    this.img.draggable = false;

    this.hit = document.createElement('div');
    this.hit.className = 'pet-hit';
    this.hit.style.width = `${this.bodyW}px`;
    this.hit.style.height = `${this.winH}px`;

    this.el.append(this.img, this.hit);
    this._bind();
    layer.root.appendChild(this.el);
    this.setPose(this.pose);
    this.render();
  }

  _bind() {
    this.hit.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.layer.onMenu && this.layer.onMenu(this.role, e.clientX, e.clientY);
    });

    this.hit.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      this.drag = {
        startX: e.clientX,
        startY: e.clientY,
        dx: this.x - e.clientX,
        dy: this.y - e.clientY,
        moved: false,
        id: e.pointerId,
      };
      try { this.hit.setPointerCapture(e.pointerId); } catch (_) {}
    });

    this.hit.addEventListener('pointermove', (e) => {
      const d = this.drag;
      if (!d || d.id !== e.pointerId) return;
      if (!d.moved) {
        if (Math.abs(e.clientX - d.startX) <= 4
          && Math.abs(e.clientY - d.startY) <= 4) return;
        d.moved = true;
        this.grab();
      }
      const p = this.clamp(e.clientX + d.dx, e.clientY + d.dy);
      this.moveTo(p.x, p.y);
    });

    const finish = (e) => {
      const d = this.drag;
      if (!d || d.id !== e.pointerId) return;
      this.drag = null;
      try { this.hit.releasePointerCapture(e.pointerId); } catch (_) {}
      if (d.moved) {
        this.release();
      } else {
        const rect = this.el.getBoundingClientRect();
        const zone = Math.min(1, Math.max(0,
          (e.clientY - rect.top) / Math.max(1, rect.height)));
        this.layer.onClick && this.layer.onClick(this.role, zone);
      }
    };
    this.hit.addEventListener('pointerup', finish);
    this.hit.addEventListener('pointercancel', (e) => {
      const d = this.drag;
      if (!d || d.id !== e.pointerId) return;
      this.drag = null;
      try { this.hit.releasePointerCapture(e.pointerId); } catch (_) {}
      if (d.moved) this.release();
    });
  }

  setPose(pose) {
    const name = this.sp.poses[pose] ? pose : 'idle';
    if (name === this.poseName) return;
    this.poseName = name;
    this.paintImage(this.sp.poses[name], name);
  }

  // 把某张图画到 <img> 上。多帧动作每帧调一次，所以抽出来。
  paintImage(img, name) {
    this.img.src = img.src;
    const w = img.naturalWidth * this.scale;
    const h = img.naturalHeight * this.scale;
    this.img.style.width = `${w}px`;
    this.img.style.height = `${h}px`;
    const dx = (this.sp.dx[name] || 0) * this.scale;
    this.img.style.transform = `translateX(-50%) translateX(${dx}px)`;
  }

  // 多帧动作：按 8fps 循环，poseName 置空让动作结束后 setPose('idle') 一定生效。
  paintFrame(index) {
    if (!this.anim || !this.anim.length) return;
    this.poseName = '';
    this.paintImage(this.anim[index % this.anim.length], '');
  }

  render() {
    this.el.style.left = `${this.x - this.winW / 2}px`;
    this.el.style.top = `${this.y - this.winH}px`;
    this.layer.bubbles.position(this.role, this.x, this.y - this.winH - 6);
  }

  clamp(x, y) {
    const half = this.winW / 2;
    return {
      x: Math.min(Math.max(x, half), Math.max(half, window.innerWidth - half)),
      y: Math.min(Math.max(y, this.winH), this.ground),
    };
  }

  act(pose, dur, move = null) {
    this.stopCrawl();
    if (this.phase === 'fall' || this.phase === 'hold' || this.phase === 'rise') return;
    const frames = this.sp.frames && this.sp.frames[pose];
    if (move && move.kind) {
      this.moveCfg = { ...move };
      this.moveX0 = this.x;
      this.moveY0 = this.y;
      this.moveT = 0;
      this.moveDur = Math.max(0.6, Number(dur) || 0);
    } else {
      this.moveCfg = null;
    }
    if (frames && frames.length > 1) {
      this.anim = frames;
      this.animFps = (this.sp.anims[pose] && this.sp.anims[pose].fps) || 8;
      this.animT = 0;
      this.pose = pose;
      this.paintFrame(0);
      this.timer = dur;
      return;
    }
    this.anim = null;
    this.pose = pose;
    this.setPose(pose);
    this.timer = dur;
  }

  stepMove(dt) {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const left = this.winW / 2;
    const right = w - this.winW / 2;
    const top = this.winH;
    this.moveT += dt;
    const t = Math.min(1, this.moveT / this.moveDur);
    const [dx, dy] = moveOffset(
      this.moveCfg, t, w, h,
      Math.max(0, this.moveX0 - left),
      Math.max(0, right - this.moveX0),
      Math.max(0, this.moveY0 - top),
      Math.max(0, this.ground - this.moveY0));
    this.x = this.moveX0 + dx;
    this.y = this.moveY0 + dy;
    if (t >= 1) {
      this.moveCfg = null;
      this.x = this.moveX0;
      this.y = this.moveY0;
    }
  }

  grab() {
    this.stopCrawl();
    this.moveCfg = null;        // 手一抓，飞行立刻作废
    this.vy = 0;
    this.anim = null;
    this.phase = 'hold';
    this.pose = 'drag';
    this.setPose('drag');
    this.timer = 0;
  }

  moveTo(x, y) {
    if (this.phase !== 'hold') return;
    this.x = x;
    this.y = y;
  }

  release() {
    if (this.phase !== 'hold') return;
    // 松手就停在原地：桌宠可以摆在桌面任意位置，不再掉回底边
    this.anim = null;
    this.phase = 'idle';
    this.pose = 'idle';
    this.setPose('idle');
    this.vy = 0;
    this.landed = true;
    this.landY = null;
    // 拖完就记下来：刷新之后还在这儿，不用每次重新摆
    this.layer.rememberSpot(this.role, this.x, this.y, this.ground);
  }

  takeOff() {
    this.stopCrawl();
    this.moveCfg = null;
    this.anim = null;
    this.landY = this.y;        // 飞一圈还回到起飞前那一层
    if (this.y <= this.top + 1) {
      this.phase = 'fall';
      this.pose = 'fall';
      this.setPose('fall');
      this.vy = 0;
      return;
    }
    this.phase = 'rise';
    this.pose = 'fall';
    this.setPose('fall');
    this.vy = 0;
    this.timer = 0;
    this.landed = false;
  }

  startCrawl(targetX, targetY = null, onArrive = null) {
    this.crawlTo = targetX;
    // targetY 为 null 时保持当前高度；集合会传发起人的高度，横竖一起爬
    this.crawlToY = targetY === null ? null : targetY;
    this.afterCrawl = onArrive;
    if (this.phase !== 'fall' && this.phase !== 'hold' && this.phase !== 'rise') {
      this.beginCrawl();
    }
  }

  beginCrawl() {
    if (this.crawlTo === null) return;
    this.moveCfg = null;        // 集合优先：爬过去，不再飞
    if (this.crawlToY === null) this.crawlToY = this.y;
    if (Math.abs(this.crawlTo - this.x) < 1
      && Math.abs(this.crawlToY - this.y) < 1) {
      this.crawlTo = null;
      this.crawlToY = null;
      const cb = this.afterCrawl;
      this.afterCrawl = null;
      if (cb) cb();
      return;
    }
    this.phase = 'crawl';
    this.anim = null;
    this.pose = CRAWL_FRAMES[0];
    this.setPose(this.pose);
    this.timer = 0;
    this.crawlT = 0;
  }

  stopCrawl() {
    this.crawlTo = null;
    this.crawlToY = null;
    this.afterCrawl = null;
    if (this.phase === 'crawl') this.phase = 'idle';
  }

  chase(targetX, targetY = null) {
    this.crawlTo = targetX;
    this.crawlToY = targetY === null ? this.y : targetY;
    this.afterCrawl = null;
    if (this.phase === 'fall' || this.phase === 'hold'
      || this.phase === 'rise') return;
    if (this.phase !== 'crawl') this.beginCrawl();
  }

  stopChase() {
    this.crawlTo = null;
    this.crawlToY = null;
    if (this.phase === 'crawl') {
      this.phase = 'idle';
      this.pose = 'idle';
      this.setPose('idle');
    }
  }

  update(dt) {
    if (this.phase === 'fall') {
      this.vy += (state.meta?.gravity ?? 2400) * dt;
      this.y += this.vy * dt;
      const landY = this.landY === null ? this.ground : this.landY;
      if (this.y >= landY) {
        this.y = landY;
        this.vy = 0;
        this.anim = null;
        this.phase = 'idle';
        this.pose = 'idle';
        this.setPose('idle');
        this.landed = true;
        this.landY = null;
        if (this.crawlTo !== null) this.beginCrawl();
        if (this.landingText) {
          this.layer.say(this.role, this.landingText);
          this.landingText = '';
        }
      }
    } else if (this.phase === 'rise') {
      this.y -= (state.meta?.rise_speed ?? 2400) * dt;
      if (this.y <= this.top) {
        this.y = this.top;
        this.phase = 'fall';
        this.pose = 'fall';
        this.setPose('fall');
        this.vy = 0;
      }
    } else if (this.phase === 'hold') {
      // 位置由拖动直接写入
    } else if (this.phase === 'crawl') {
      const remain = this.crawlTo - this.x;
      const remainY = (this.crawlToY === null ? this.y : this.crawlToY) - this.y;
      const step = this.winW * CRAWL_BODY_PER_SEC * dt;
      this.x += Math.abs(remain) <= step ? remain : (remain > 0 ? step : -step);
      this.y += Math.abs(remainY) <= step ? remainY : (remainY > 0 ? step : -step);
      this.crawlT += dt;
      const frame = CRAWL_FRAMES[Math.floor(this.crawlT / CRAWL_FRAME_SEC) % 2];
      this.pose = frame;
      this.setPose(frame);
      if (Math.abs(this.crawlTo - this.x) <= 0.5
        && Math.abs(remainY) <= step) {
        this.x = this.crawlTo;
        this.y = this.crawlToY === null ? this.y : this.crawlToY;
        this.crawlTo = null;
        this.crawlToY = null;
        this.phase = 'idle';
        this.pose = 'idle';
        this.setPose('idle');
        const cb = this.afterCrawl;
        this.afterCrawl = null;
        if (cb) cb();
      }
    } else if (this.timer > 0) {
      this.timer -= dt;
      if (this.anim) {
        this.animT += dt;
        this.paintFrame(Math.floor(this.animT * this.animFps));
      }
      if (this.moveCfg) this.stepMove(dt);
      if (this.timer <= 0) {
        this.anim = null;
        this.pose = 'idle';
        this.setPose('idle');
        if (this.moveCfg) {
          // 时长到了就回原位，别把人留在天上。
          this.moveCfg = null;
          this.x = this.moveX0;
          this.y = this.moveY0;
        }
      }
    }
    this.render();
  }

  onResize() {
    // 站着的人跟着地面走（输入框变高、窗口变矮都会挪地面）；摆到半空的人
    // 保住「离地面那一截」。两边都是相对地面算的，换个窗口高度就不会被
    // 顶到半空、或者压到输入框上。
    const gap = Math.max(0, this.ground - this.y);
    const resting = this.phase === 'idle' && this.drag === null;
    this.ground = this.layer.groundY();
    this.top = -this.winH - 40;
    const p = this.clamp(this.x, resting ? this.ground - gap : this.y);
    this.x = p.x;
    this.y = p.y;
    if (this.crawlToY !== null) {
      this.crawlToY = Math.min(this.crawlToY, this.ground);
    }
    if (this.landY !== null) {
      this.landY = Math.min(this.landY, this.ground);
    }
    this.render();
  }

  remove() {
    this.alive = false;
    this.el.remove();
  }
}

// actors.gather_targets 的 JS 版本：按身体宽度排左右，贴边时换边/收窄。
function gatherTargets(anchorX, xs, widths, anchorW, gap, lo, hi) {
  const n = xs.length;
  if (!n) return [];
  gap = Math.max(0, gap);
  const near = [...xs.keys()].sort((a, b) => Math.abs(xs[a] - anchorX) - Math.abs(xs[b] - anchorX));
  const left = near.filter((i) => xs[i] < anchorX);
  const right = near.filter((i) => xs[i] >= anchorX);
  const roomL = Math.max(0, anchorX - lo);
  const roomR = Math.max(0, hi - anchorX);

  const offsets = (ids, g) => {
    let cur = anchorW / 2;
    const out = [];
    for (const i of ids) {
      cur += g + widths[i] / 2;
      out.push(cur);
      cur += widths[i] / 2;
    }
    return out;
  };
  const fits = (g) => (
    (!left.length || offsets(left, g)[offsets(left, g).length - 1] <= roomL + 1e-9)
    && (!right.length || offsets(right, g)[offsets(right, g).length - 1] <= roomR + 1e-9)
  );

  for (let k = 0; k <= n; k++) {
    const loff = left.length ? offsets(left, gap)[left.length - 1] : 0;
    const roff = right.length ? offsets(right, gap)[right.length - 1] : 0;
    if (left.length && loff > roomL + 1e-9) {
      right.push(left.pop());
    } else if (right.length && roff > roomR + 1e-9) {
      left.push(right.pop());
    } else {
      break;
    }
  }

  let g = gap;
  if (!fits(g)) {
    if (!fits(0)) {
      const total = roomL + roomR;
      const want = total > 0 ? Math.round(n * roomL / total) : 0;
      while (left.length > want) right.push(left.pop());
      while (left.length < want && right.length) left.push(right.pop());
    }
    let low = 0;
    let high = gap;
    for (let i = 0; i < 24; i++) {
      const mid = (low + high) / 2;
      if (fits(mid)) low = mid;
      else high = mid;
    }
    g = low;
  }

  const targets = new Array(n).fill(0);
  const loff = offsets(left, g);
  left.forEach((i, k) => { targets[i] = anchorX - loff[k]; });
  const roff = offsets(right, g);
  right.forEach((i, k) => { targets[i] = anchorX + roff[k]; });
  return targets;
}
