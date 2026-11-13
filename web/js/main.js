// 网页版主程序：把网络、认领、聊天、角色、菜单接起来。
// 业务判断和 petclub/app.py 的 App 对应；Qt 窗口换成了 DOM。
import {
  state, CONV_ALL, roleName, nowSec, savePref,
} from './state.js';
import {
  loadMeta, getAction, what, sayLine, replyLine, mediaLine, tipText,
} from './actions.js';
import { Net } from './net.js';
import { ClaimUI } from './claim.js';
import { ChatUI } from './chat.js';
import { PetLayer } from './pets.js';
import { initMenu, showMenu, closeMenu } from './menu.js';
import { initHistory, loadHistory } from './history.js';

class App {
  constructor() {
    this.quitting = false;
    this.joinedAt = 0;

    this.claim = new ClaimUI({
      onJoin: (role) => this.join(role),
    });
    this.chat = new ChatUI({
      onSend: (text) => this.sendChat(text),
      onAction: (key, to) => this.doAction(key, to),
      onLeave: () => this.leave(),
      onQuit: () => this.quit(),
      onRemote: (on) => this.setRemote(on),
      onMedia: (kind, payload) => this.sendMedia(kind, payload),
      onPetSize: (level) => this.setPetSize(level),
    });
    this.pets = new PetLayer({
      onPetClick: (role) => this.onPetClick(role),
      onMenu: (role, x, y) => this.onPetMenu(role, x, y),
    });
    this.net = new Net({
      onMessage: (m) => this.onMessage(m),
      onOpen: () => this.onOpen(),
      onClose: () => this.onClose(),
    });
  }

  async init() {
    initMenu();
    try {
      await loadMeta();
    } catch (e) {
      this.claim.show('页面加载失败，刷新重试');
      this.claim.setStatus(`加载失败：${e.message || e}`, true);
      return;
    }

    try {
      await initHistory();
      const saved = await loadHistory();
      if (saved && typeof saved === 'object') state.logs = saved;
    } catch (_) {
      // 浏览器禁用存储时退化成当前会话内存记录
    }

    this.claim.init();
    this.chat.init();
    document.body.dataset.petSize = state.petSize;
    this.chat.setOffline(true);
    this.claim.show('');
    this.claim.setStatus('正在连接服务器…');
    this.net.connect();

    // 网关会替浏览器给服务端发心跳；这里再发一条应用层 ping，
    // 主要是让浏览器自己也能尽早发现连接异常。
    setInterval(() => {
      if (this.net.connected) this.net.send({ t: 'ping' });
    }, 10000);

    window.addEventListener('resize', () => this.pets.resize());
    window.addEventListener('online', () => this.net.reconnectNow());
    // 输入框变高（多行文字 / 换行）时，宠物站的“地面”也跟着抬起来
    if (window.ResizeObserver) {
      const composer = document.querySelector('.composer');
      if (composer) {
        new ResizeObserver(() => this.pets.resize()).observe(composer);
      }
    }
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      this.net.reconnectNow();
      if (this.net.connected) this.net.send({ t: 'query' });
    });
    window.addEventListener('beforeunload', () => {
      this.net.send({ t: 'leave' });
      this.net.stop();
    });
  }

  // ---------------- 连接 ----------------

  onOpen() {
    state.connected = true;
    state.offline = false;
    this.chat.setOffline(false);
    this.claim.render();
    this.claim.setStatus('已连接 · 选一个身份');
    if (state.me) {
      this.net.send({ t: 'join', role: state.me.role, client: 'web' });
    } else {
      this.net.send({ t: 'query' });
    }
  }

  onClose() {
    state.connected = false;
    state.offline = true;
    this.chat.setOffline(true);
    this.claim.setStatus('连不上服务器，正在重试…', true);
    this.claim.render();
  }

  onMessage(m) {
    switch (m.t) {
      case 'state': this.onState(m); break;
      case 'joined': this.onJoined(m); break;
      case 'reject': this.onReject(m); break;
      case 'event': this.onEvent(m); break;
      case 'error': this.onError(m); break;
      case 'left': this.onLeft(); break;
      default: break; // pong 等
    }
  }

  onState(m) {
    state.players = m.players || {};
    this.chat.updateOnline();

    if (state.claimVisible) this.claim.render();

    if (state.me) {
      const mine = state.players[state.me.role];
      if (!mine && Date.now() - this.joinedAt > 1500) {
        this.releaseLocal('你的身份已被释放，重新认领一个吧');
        return;
      }
      this.pets.sync(state.players);
      this.chat.renderAll();
    } else if (!state.pendingRole && !state.claimVisible) {
      this.showClaim('');
    }
  }

  onJoined(m) {
    const previous = state.me;
    const fresh = !previous || previous.role !== m.role;

    state.me = { role: m.role };
    state.pendingRole = null;
    this.joinedAt = Date.now();

    if (fresh) {
      this.chat.resetForClaim();
      this.pets.clear();
    }

    this.claim.hide();
    this.chat.showMain();
    this.chat.switch(CONV_ALL, { focus: false });
    this.claim.setStatus('');
    this.claim.setNote('');

    this.pets.spawn(m.role, {
      drop: fresh,
      landingText: fresh ? `我是${roleName(m.role)}，到位啦～` : '',
    });
    this.chat.updateOnline();
  }

  onReject(m) {
    state.pendingRole = null;
    if (m.players && typeof m.players === 'object') state.players = m.players;
    if (state.me) {
      state.me = null;
      this.pets.clear();
      this.chat.resetForClaim();
    }
    this.chat.hideMain();
    this.claim.resetSelection();
    this.claim.show(m.msg || '认领失败，换一个身份吧');
    this.claim.setStatus(m.msg || '', true);
    this.claim.render();
  }

  onLeft() {
    if (!state.me && state.claimVisible) return;
    this.releaseLocal('已释放身份，重新选一个吧');
  }

  onError(m) {
    const msg = m.msg || '这条没发出去';
    if (state.claimVisible) this.claim.setStatus(msg, true);
    else this.chat.setNote(msg);
  }

  // ---------------- 认领 / 释放 ----------------

  join(role) {
    state.pendingRole = role;
    this.claim.setStatus('正在进入…');
    if (!this.net.send({ t: 'join', role, client: 'web' })) {
      state.pendingRole = null;
      this.claim.setStatus('还没连上服务器，稍等一下', true);
    }
  }

  showClaim(note) {
    this.chat.hideMain();
    this.claim.show(note);
    this.claim.render();
    this.claim.setStatus(
      state.connected ? '已连接 · 选一个身份' : '连不上服务器，正在重试…',
      !state.connected,
    );
  }

  releaseLocal(note) {
    state.me = null;
    state.pendingRole = null;
    this.pets.clear();
    this.chat.resetForClaim();
    this.showClaim(note);
    this.chat.updateOnline();
  }

  leave() {
    if (this.quitting) return;
    this.net.send({ t: 'leave' });
    closeMenu();
    this.releaseLocal('已释放身份，重新选一个吧');
  }

  quit() {
    if (this.quitting) return;
    this.quitting = true;
    this.net.send({ t: 'leave' });
    this.net.stop();
    closeMenu();
    this.pets.clear();
    this.chat.hideMain();
    this.claim.show('已退出，刷新页面可重新进入');
    this.claim.setStatus('已退出', false);
    this.claim.render();
    this.chat.updateOnline();
  }

  setRemote(on) {
    state.remoteOn = !!on;
    savePref('petclub.remote', on ? '1' : '0');
    this.chat.setNote(on ? '已开启同步远程互动' : '已关闭同步远程互动');
  }

  setPetSize(level) {
    if (!state.meta || !state.meta.pet_size_factor
        || !state.meta.pet_size_factor[level] || level === state.petSize) {
      return;
    }
    state.petSize = level;
    savePref('petclub.petSize', level);
    document.body.dataset.petSize = level;
    this.pets.rebuild();
    this.chat.setNote(`桌宠大小：${{ small: '小', default: '默认', large: '大' }[level]}`);
  }

  // ---------------- 动作 ----------------

  onEvent(m) {
    const spec = getAction(m.kind);
    const actor = m.from;
    const hasPet = this.pets.has(actor);
    if (!spec || !roleName(actor)) return;

    const target = typeof m.to === 'string' ? m.to : null;
    const key = target ? actor : CONV_ALL;
    const text = (m.text || '').trim();
    const mt = m.mt || 'text';

    if (!state.remoteOn && m.kind !== 'say') {
      // 关了同步远程互动：人不动，但聊天记录里仍留一条
      this.chat.store(key, {
        kind: 'tip',
        text: tipText(m.kind, roleName(actor), !!target,
          target ? roleName(target) : null),
        ts: nowSec(),
      });
      return;
    }

    if (m.kind === 'say') {
      if (hasPet) {
        this.pets.play(actor, spec.pose, spec.dur);
        if (mt === 'text' || mt === 'emoji') this.pets.say(actor, text);
        else this.pets.say(actor, mediaLine(mt, actor));
      }
      const entry = { kind: 'msg', who: actor, mt, ts: nowSec() };
      if (mt === 'text') {
        entry.text = text;
      } else if (mt === 'emoji') {
        entry.char = text;
        entry.text = text;
      } else {
        entry.img = m.img || '';
        entry.text = mediaPlaceholder(mt, m.name);
        if (mt === 'file') {
          entry.name = m.name || '文件';
          entry.size = m.size || 0;
        }
      }
      this.chat.store(key, entry);
      return;
    }

    if (hasPet) {
      this.pets.play(actor, what(spec), spec.dur);
      this.pets.say(actor, text || sayLine(m.kind, actor));
    }
    this.chat.store(key, {
      kind: 'tip',
      text: tipText(m.kind, roleName(actor), !!target,
        target ? roleName(target) : null),
      ts: nowSec(),
    });

    const me = state.me ? state.me.role : null;
    if (spec.gather && me && me !== actor && (!target || target === me)) {
      const moved = this.pets.gather(actor, target ? [me] : null);
      if (moved.includes(me)) {
        const reply = replyLine(m.kind, me);
        if (reply) this.pets.say(me, reply);
      }
      return;
    }

    const mirror = spec.mirror;
    if (mirror && me && me !== actor) {
      let peers;
      if (target) peers = target === me ? [me] : [];
      else peers = [...this.pets.pets.keys()].filter((r) => r !== actor);
      for (const r of peers) this.pets.play(r, mirror, spec.dur);
      if (peers.includes(me)) {
        const reply = replyLine(m.kind, me);
        if (reply) this.pets.say(me, reply);
      }
    }
  }

  doAction(key, to) {
    const spec = getAction(key);
    const me = state.me ? state.me.role : null;
    if (!spec || !me) return;

    if (key === 'say') {
      this.chat.switch(to || CONV_ALL);
      this.chat.focusInput();
      return;
    }

    const pet = this.pets.get(me);
    if (!pet) return;

    const msg = { t: 'event', kind: key, text: '' };
    if (to) msg.to = to;
    this.net.send(msg);

    // 自己这边也要演：动作 + 镜像 + 台词，和桌面版 do_action 一致
    this.pets.play(me, what(spec), spec.dur);
    if (spec.gather) {
      this.pets.gather(me, to ? [to] : null);
    } else if (spec.mirror) {
      const peers = to
        ? [to]
        : [...this.pets.pets.keys()].filter((r) => r !== me);
      for (const r of peers) this.pets.play(r, spec.mirror, spec.dur);
    }

    const line = sayLine(key, me);
    if (line) this.pets.say(me, line);

    this.chat.store(to || CONV_ALL, {
      kind: 'tip',
      text: tipText(key, '你', !!to, to ? roleName(to) : null),
      ts: nowSec(),
    });
  }

  guardSay() {
    const me = state.me ? state.me.role : null;
    if (!me) return '还没认领身份';
    const to = state.currentKey === CONV_ALL ? null : state.currentKey;
    if (!this.net.connected) return '连不上服务器，正在重试…';
    if (to && !state.players[to]) {
      return `「${roleName(to)}」不在线，这句发不出去`;
    }
    return '';
  }

  emitSay(to, extra = {}) {
    const msg = { t: 'event', kind: 'say', mt: 'text', ...extra };
    if (to) msg.to = to;
    this.net.send(msg);
  }

  playSay() {
    const me = state.me ? state.me.role : null;
    const spec = getAction('say');
    if (spec) this.pets.play(me, what(spec), spec.dur);
  }

  sayMedia(mt, role) {
    const line = mediaLine(mt, role);
    if (line && this.pets.get(role)) this.pets.say(role, line);
  }

  sendChat(text) {
    const why = this.guardSay();
    if (why) return why;
    const me = state.me.role;
    const key = state.currentKey;
    const to = key === CONV_ALL ? null : key;

    this.emitSay(to, { mt: 'text', text });
    this.playSay();
    if (this.pets.get(me)) this.pets.say(me, text);

    this.chat.store(key, { kind: 'msg', who: me, mt: 'text', text, ts: nowSec() });
    return '';
  }

  async sendMedia(kind, payload) {
    const why = this.guardSay();
    if (why) return why;
    const me = state.me.role;
    const key = state.currentKey;
    const to = key === CONV_ALL ? null : key;

    if (kind === 'emoji') {
      const ch = String(payload || '');
      if (!ch) return '这个表情发不出去';
      this.emitSay(to, { mt: 'emoji', text: ch });
      this.playSay();
      this.sayMedia('emoji', me);
      this.chat.store(key, {
        kind: 'msg', who: me, mt: 'emoji', char: ch, text: ch, ts: nowSec(),
      });
      return '';
    }

    if (kind === 'sticker') {
      const b64 = await urlToBase64(payload);
      if (!b64) return '这个表情包读取失败';
      this.emitSay(to, { mt: 'sticker', img: b64 });
      this.playSay();
      this.sayMedia('sticker', me);
      this.chat.store(key, {
        kind: 'msg', who: me, mt: 'sticker', img: b64, text: '[表情包]', ts: nowSec(),
      });
      return '';
    }

    if (kind === 'file') {
      const file = payload;
      if (!file) return '';
      if (file.type && file.type.startsWith('image/')) {
        const b64 = await compressImage(file);
        if (!b64) return '这张图片处理失败';
        this.emitSay(to, { mt: 'img', img: b64 });
        this.playSay();
        this.sayMedia('img', me);
        this.chat.store(key, {
          kind: 'msg', who: me, mt: 'img', img: b64, text: '[图片]', ts: nowSec(),
        });
        return '';
      }
      if (file.size > 2 * 1024 * 1024) return '文件太大了（上限 2MB）';
      const b64 = await fileToBase64(file);
      this.emitSay(to, {
        mt: 'file', img: b64, name: file.name, size: file.size,
      });
      this.playSay();
      this.sayMedia('file', me);
      this.chat.store(key, {
        kind: 'msg',
        who: me,
        mt: 'file',
        img: b64,
        name: file.name,
        size: file.size,
        text: `[文件] ${file.name}`,
        ts: nowSec(),
      });
      return '';
    }
    return '';
  }

  // ---------------- 角色交互 ----------------

  onPetClick(role) {
    const me = state.me ? state.me.role : null;
    if (!me || role !== me) return;
    const pet = this.pets.get(role);
    if (!pet || pet.phase === 'hold') return;

    if (pet.poseName === 'sleep') {
      pet.act('jump', 1.0);
      const line = sayLine('wake', role);
      if (line) this.pets.say(role, line);
      return;
    }

    const spec = getAction('pet');
    if (!spec) return;
    pet.act(spec.pose, spec.dur);
    const line = sayLine('pet', role);
    if (line) this.pets.say(role, line);
  }

  onPetMenu(role, x, y) {
    showMenu(x, y, role, {
      onAction: (key, to) => this.doAction(key, to),
      onLeave: () => this.leave(),
      onQuit: () => this.quit(),
    });
  }
}

function mediaPlaceholder(mt, name) {
  if (mt === 'img') return '[图片]';
  if (mt === 'sticker') return '[表情包]';
  if (mt === 'file') return `[文件] ${name || '文件'}`;
  return '[消息]';
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || '');
      resolve(s.includes(',') ? s.slice(s.indexOf(',') + 1) : s);
    };
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}

async function urlToBase64(url) {
  const res = await fetch(url, { cache: 'force-cache' });
  const blob = await res.blob();
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('表情包读取失败'));
    reader.readAsDataURL(blob);
  });
  return data.includes(',') ? data.slice(data.indexOf(',') + 1) : '';
}

async function compressImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('图片读取失败'));
      im.src = url;
    });
    const maxSide = 1280;
    let w = img.naturalWidth || img.width;
    let h = img.naturalHeight || img.height;
    let k = Math.min(1, maxSide / Math.max(w, h));
    let quality = 0.86;
    let b64 = '';
    for (let i = 0; i < 5; i++) {
      const cw = Math.max(1, Math.round(w * k));
      const ch = Math.max(1, Math.round(h * k));
      const canvas = document.createElement('canvas');
      canvas.width = cw;
      canvas.height = ch;
      const ctx = canvas.getContext('2d');
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, 0, 0, cw, ch);
      const data = canvas.toDataURL('image/jpeg', quality);
      b64 = data.slice(data.indexOf(',') + 1);
      if (b64.length <= 1_600_000) break;
      k *= 0.82;
      quality = Math.max(0.45, quality - 0.08);
    }
    return b64;
  } finally {
    URL.revokeObjectURL(url);
  }
}

const app = new App();
window.petclub = app;
app.init();
