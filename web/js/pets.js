// 悬浮在网页上的角色。
// 物理和 actors.py 一一对应：fall / hold / rise / crawl / idle。
// 位置只存在本机，拖动、起飞、集合落点都不上网 —— 和桌面版一样。
import { state, roleName } from './state.js';
import { loadRole } from './sprites.js';
import { BubbleLayer } from './bubble.js';

const CRAWL_FRAMES = ['crawl_1', 'crawl_2'];
const CRAWL_FRAME_SEC = 0.16;
const CRAWL_BODY_PER_SEC = 1.5;

export class PetLayer {
  constructor({ onPetClick, onMenu } = {}) {
    this.onPetClick = onPetClick;
    this.onMenu = onMenu;
    this.root = document.getElementById('pet-layer');
    this.bubbles = new BubbleLayer();
    this.pets = new Map();
    this.spawning = new Set();
    this.last = performance.now();
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
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
      const x = window.innerWidth * (i + 1) / (state.roles.length + 1);
      const pet = new Pet(this, role, sp, x, this.groundY(), drop);
      pet.landingText = landingText;
      this.pets.set(role, pet);
    } catch (e) {
      console.warn('spawn failed', role, e);
    } finally {
      this.spawning.delete(role);
    }
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

  async rebuild() {
    const roles = new Set(this.pets.keys());
    for (const role of state.roles) {
      if (state.players[role] || (state.me && state.me.role === role)) roles.add(role);
    }
    this.clear();
    for (const role of roles) await this.spawn(role);
  }

  play(role, what, dur) {
    const pet = this.pets.get(role);
    if (!pet) return;
    if (what === 'launch') pet.takeOff();
    else if (what && dur) pet.act(what, dur);
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
    roles.forEach((r, i) => this.pets.get(r).startCrawl(targets[i]));
    return roles;
  }

  resize() {
    for (const pet of this.pets.values()) pet.onResize();
  }

  _loop(now) {
    const dt = Math.min(0.05, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    for (const pet of this.pets.values()) pet.update(dt);
    this.bubbles.update();
    requestAnimationFrame(this._loop);
  }
}

class Pet {
  constructor(layer, role, sp, x, ground, drop) {
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
    this.timer = 0;
    this.landed = !drop;
    this.crawlTo = null;
    this.crawlT = 0;
    this.landingText = '';
    this.drag = null;
    this.alive = true;

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
        this.layer.onClick && this.layer.onClick(this.role);
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
    const img = this.sp.poses[name];
    this.img.src = img.src;
    const w = img.naturalWidth * this.scale;
    const h = img.naturalHeight * this.scale;
    this.img.style.width = `${w}px`;
    this.img.style.height = `${h}px`;
    const dx = (this.sp.dx[name] || 0) * this.scale;
    this.img.style.transform = `translateX(-50%) translateX(${dx}px)`;
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

  act(pose, dur) {
    this.stopCrawl();
    if (this.phase === 'fall' || this.phase === 'hold' || this.phase === 'rise') return;
    this.pose = pose;
    this.setPose(pose);
    this.timer = dur;
  }

  grab() {
    this.stopCrawl();
    this.vy = 0;
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
    this.phase = 'fall';
    this.pose = 'fall';
    this.setPose('fall');
    this.vy = 0;
  }

  takeOff() {
    this.stopCrawl();
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

  startCrawl(targetX) {
    this.crawlTo = targetX;
    if (this.phase !== 'fall' && this.phase !== 'hold' && this.phase !== 'rise') {
      this.beginCrawl();
    }
  }

  beginCrawl() {
    if (this.crawlTo === null || Math.abs(this.crawlTo - this.x) < 1) {
      this.crawlTo = null;
      return;
    }
    this.phase = 'crawl';
    this.pose = CRAWL_FRAMES[0];
    this.setPose(this.pose);
    this.timer = 0;
    this.crawlT = 0;
  }

  stopCrawl() {
    this.crawlTo = null;
    if (this.phase === 'crawl') this.phase = 'idle';
  }

  update(dt) {
    if (this.phase === 'fall') {
      this.vy += (state.meta?.gravity ?? 2400) * dt;
      this.y += this.vy * dt;
      if (this.y >= this.ground) {
        this.y = this.ground;
        this.vy = 0;
        this.phase = 'idle';
        this.pose = 'idle';
        this.setPose('idle');
        this.landed = true;
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
      const step = this.winW * CRAWL_BODY_PER_SEC * dt;
      this.x += Math.abs(remain) <= step ? remain : (remain > 0 ? step : -step);
      this.crawlT += dt;
      const frame = CRAWL_FRAMES[Math.floor(this.crawlT / CRAWL_FRAME_SEC) % 2];
      this.pose = frame;
      this.setPose(frame);
      if (Math.abs(this.crawlTo - this.x) <= 0.5) {
        this.x = this.crawlTo;
        this.crawlTo = null;
        this.phase = 'idle';
        this.pose = 'idle';
        this.setPose('idle');
      }
    } else if (this.timer > 0) {
      this.timer -= dt;
      if (this.timer <= 0) {
        this.pose = 'idle';
        this.setPose('idle');
      }
    }
    this.render();
  }

  onResize() {
    this.ground = this.layer.groundY();
    this.top = -this.winH - 40;
    const p = this.clamp(this.x, this.y);
    this.x = p.x;
    if (this.phase === 'idle' || this.phase === 'hold') this.y = this.ground;
    else if (this.y > this.ground) this.y = this.ground;
    if (this.phase === 'crawl') this.y = this.ground;
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
