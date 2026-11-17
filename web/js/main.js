// 网页版主程序：把网络、认领、聊天、角色、菜单接起来。
// 业务判断和 petclub/app.py 的 App 对应；Qt 窗口换成了 DOM。
import {
  state, CONV_ALL, roleName, nowSec, savePref,
  loadToken, saveToken, clearToken, loadCode, saveCode,
} from './state.js';
import {
  loadMeta, getAction, what, sayLine, replyLine, mediaLine, actionTip,
  normName,
} from './actions.js';
import { Net } from './net.js';
import { ClaimUI } from './claim.js';
import { ChatUI } from './chat.js';
import { PetLayer } from './pets.js';
import { initMenu, showMenu, closeMenu } from './menu.js';
import { initHistory, loadHistory } from './history.js';
import { playFireworks as showFireworks } from './fx.js';
import { watchVersion } from './version.js';
import {
  loadSprites, startSpritePolling, applySprites, refreshSprites,
} from './spritefeed.js';
import { spriteManifest } from './asset.js';
import { clearRole } from './sprites.js';
import { openUpload, resetPose } from './upload.js';

// 下载桌面客户端：优先用对象存储里的 exe（和自动更新同一目录）。
const GITHUB_REPO = 'https://github.com/YongYangLi/pet-club';

function downloadUrl() {
  // 网关下发的是桶里的地址（和自动更新同一个目录）；拿不到再退回 GitHub。
  const url = state.meta && state.meta.download_url;
  if (url) return url;
  const v = (state.meta && (state.meta.web_version
    || state.meta.server_version)) || '';
  return v ? `${GITHUB_REPO}/releases/download/v${v}/pet-club.exe`
    : `${GITHUB_REPO}/releases/latest`;
}

class App {
  constructor() {
    this.quitting = false;
    this.joinedAt = 0;
    // 刷新回来后自动坐回上次身份的尝试只有一次，失败就老老实实回认领页
    this.autoTried = false;
    this.auth = false;
    this.lastJoin = null;
    this.kickedAt = 0;          // 刚被服务端断开（顶号）时不要闪「连不上」
    this.taps = new Map();
    // 上一次见到的「每个角色有哪些覆盖图」。清单是整份下发的，
    // 但别人换的是他自己的图 —— 靠这份快照只重建真的变了的角色。
    this.posesSeen = {};
    this.animsSeen = {};
    this.reminderTimer = null;
    this.waterOn = false;
    this.waterMin = 45;
    this.sitTimer = null;
    this.sitOn = false;
    this.sitMin = 60;
    this.sitDue = false;

    this.claim = new ClaimUI({
      onJoin: (what) => this.join(what),
      codeFor: (role) => loadCode(role),
    });
    this.chat = new ChatUI({
      onSend: (text, re) => this.sendChat(text, re),
      onAction: (key, to) => this.doAction(key, to),
      onLeave: () => this.leave(),
      onQuit: () => this.quit(),
      onRemote: (on) => this.setRemote(on),
      onMedia: (kind, payload, re) => this.sendMedia(kind, payload, re),
      onGame: (key, to) => this.playGame(key, to),
      onFx: (to) => this.playFireworks(to),
      onToy: (role) => this.startToy(role),
      onReminder: (kind, on, min) => this.setReminder(kind, on, min),
      reminderState: () => ({
        water: { on: this.waterOn, min: this.waterMin },
        sit: { on: this.sitOn, min: this.sitMin },
      }),
      onPetSize: (level) => this.setPetSize(level),
      onPoseAdd: (role) => this.uploadSprite(role),
      onPoseReset: (role, key, act) => this.removeSpriteChange(role, key, act),
    });
    this.pets = new PetLayer({
      onPetClick: (role, zone) => this.onPetClick(role, zone),
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
      // 素材清单要先于第一次 loadRole：角色出生时才需要知道该去哪儿取图。
      // 拿不到不算失败 —— 会退回出厂素材，页面照常能用。
      await loadSprites();
      // 记下第一屏的素材快照：后面判断「谁变了」要和它比。
      // 不记的话，进页面后第一次改图会被算成「什么都没变」——
      // 因为对比基准是空的，空对空永远是空。
      this.posesSeen = JSON.parse(JSON.stringify(spriteManifest().poses || {}));
      this.animsSeen = JSON.parse(JSON.stringify(spriteManifest().anims || {}));
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
    this.initAccountLinks();
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
    // 有新版本就提示刷新。页面在后台时 version.js 会自己刷掉，
    // 走到这里的一定是用户正盯着的页面，不能抢他的输入框。
    document.getElementById('update-go').addEventListener('click', () => location.reload());
    watchVersion((info) => this.showUpdate(info));
    // 别人传了新图/新动作：不走版本机制（那会整页刷新），只换素材
    startSpritePolling(() => this.onSpritesChanged());

    window.addEventListener('beforeunload', () => {
      this.net.send({ t: 'leave' });
      this.net.stop();
    });
  }

  showUpdate(info) {
    const bar = document.getElementById('update-banner');
    if (!bar) return;
    const text = document.getElementById('update-text');
    if (text) text.textContent = `新版本 ${info.version || ''} 已发布`;
    bar.classList.remove('hidden');
  }

  // ---------------- 口令 / 下载 ----------------

  initAccountLinks() {
    const dl = document.getElementById('download-client');
    if (dl) dl.href = downloadUrl();

    const modal = document.getElementById('code-modal');
    const news = document.getElementById('code-new');
    const again = document.getElementById('code-new2');
    const msg = document.getElementById('code-msg');
    if (!modal || !news || !again || !msg) return;

    const close = () => modal.classList.add('hidden');
    const open = () => {
      if (!state.me || !this.auth) return;
      news.value = '';
      again.value = '';
      msg.textContent = '';
      modal.classList.remove('hidden');
      news.focus();
    };
    const save = () => {
      const a = news.value.trim();
      const b = again.value.trim();
      if (a.length < 6) { msg.textContent = '口令至少 6 位'; return; }
      if (a !== b) { msg.textContent = '两次输入不一致'; return; }
      this.net.send({ t: 'setcode', code: a });
      close();
      this.chat.setNote('正在保存口令…');
    };
    document.getElementById('set-code').addEventListener('click', open);
    document.getElementById('code-cancel').addEventListener('click', close);
    document.getElementById('code-save').addEventListener('click', save);
    again.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); save(); }
    });
  }

  syncAccountLinks() {
    const btn = document.getElementById('set-code');
    if (btn) btn.classList.toggle('hidden', !(this.auth && state.me));
  }

  // ---------------- 连接 ----------------

  onOpen() {
    state.connected = true;
    state.offline = false;
    this.chat.setOffline(false);
    this.claim.render();
    this.claim.setStatus('已连接 · 选一个身份');
    if (state.me) {
      // 重连：有令牌就静默坐回去；没有令牌（老模式）才按身份重认。
      const token = loadToken();
      this.join(token ? { token } : state.me.role);
    } else {
      this.net.send({ t: 'query' });
    }
  }

  onClose() {
    state.connected = false;
    state.offline = true;
    this.chat.setOffline(true);
    this.claim.render();
    // 被顶号时服务端会主动断开这条连接，紧接着就重连 ——
    // 那一瞬间不该闪一条「连不上服务器」的红字。
    if (this.kickedAt && Date.now() - this.kickedAt < 8000) {
      this.claim.setStatus('正在重新连接…', false);
      return;
    }
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
      case 'kicked': this.onKicked(m); break;
      case 'code_ok': this.onCodeOk(m); break;
      // 别人换了素材。不用等下一轮轮询，也不用刷新页面。
      case 'sprites': this.onSpritesPush(); break;
      default: break; // pong 等
    }
  }

  onState(m) {
    state.players = m.players || {};
    this.auth = !!m.auth;
    this.claim.setAuth(this.auth);
    this.claim.setLocked(m.locked || []);
    this.syncAccountLinks();
    this.chat.updateOnline();

    // 刷新后自动坐回上次身份。
    // 启用口令：有令牌就用令牌静默登录（口令决定身份，不需要记住 role）；
    // 没启用口令：沿用原来的「上次坐哪儿」。
    if (!state.me && !state.pendingRole && !this.autoTried) {
      this.autoTried = true;
      const token = loadToken();
      if (token) {
        this.join({ token });
        return;
      }
      if (!this.auth) {
        const last = state.lastRole;
        if (last) {
          if (!m.players[last]) {
            this.claim.setStatus(`正在回到「${roleName(last)}」…`);
            this.join(last);
            return;
          }
          savePref('petclub.role', '');
          state.lastRole = '';
          this.claim.show('上次的身份已经被别人认领了，重新选一个吧');
          this.claim.setStatus('已连接 · 选一个身份');
        }
      }
    }

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
    state.lastRole = m.role;
    savePref('petclub.role', m.role);
    if (typeof m.token === 'string' && m.token) saveToken(m.token);
    if (this.lastJoin && this.lastJoin.code) {
      saveCode(m.role, this.lastJoin.code);
    }
    this.joinedAt = Date.now();
    this.kickedAt = 0;
    this.syncAccountLinks();

    if (fresh) {
      this.chat.resetForClaim();
      this.pets.clear();
    }

    this.claim.hide();
    this.chat.showMain();
    // 自定义动作按身份过滤，认领前 state.me 是空的、会被整条滤掉，
    // 所以坐下来之后要重排一次互动条。
    this.chat.renderQuick();
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
    const reason = m.reason || '';
    state.pendingRole = null;
    if (m.auth !== undefined) {
      this.auth = !!m.auth;
      this.claim.setAuth(this.auth);
    }
    if (m.players && typeof m.players === 'object') state.players = m.players;

    if (reason === 'role_online') {
      // 本地状态慢了一拍（点进入前对方刚好上线）：退回认领页。
      // 此时 players 里这个身份已在线，界面自动变成「强制进入」。
      state.lastRole = '';
      savePref('petclub.role', '');
      if (state.me) {
        state.me = null;
        this.pets.clear();
        this.chat.resetForClaim();
      }
      this.chat.hideMain();
      this.claim.show('');
      this.claim.setStatus('');
      const role = m.role || (this.lastJoin && this.lastJoin.role);
      this.claim.preselect(role, loadCode(role));
      return;
    }

    if (reason === 'need_auth' || reason === 'bad_code' || reason === 'too_many') {
      if (reason === 'need_auth') clearToken();
      state.lastRole = '';
      savePref('petclub.role', '');
      if (state.me) {
        state.me = null;
        this.pets.clear();
        this.chat.resetForClaim();
      }
      this.chat.hideMain();
      this.claim.show(m.msg || '认领失败，重新输一次口令吧');
      this.claim.setStatus(m.msg || '', true);
      this.claim.render();
      return;
    }

    // 老模式（没启用口令）的占用 / 不合法：和原来一样
    state.lastRole = '';
    savePref('petclub.role', '');
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

  /** 被新设备顶下线：回到选择身份，选好那个人、填好上次的口令。 */
  onKicked(m) {
    this.autoTried = true;
    this.kickedAt = Date.now();
    const role = state.me && state.me.role;
    const code = loadCode(role);
    this.chat.setNote('');
    this.releaseLocal('');
    if (role) this.claim.preselect(role, code);
  }

  onCodeOk(m) {
    if (typeof m.token === 'string' && m.token) saveToken(m.token);
    this.chat.setNote('口令已更新；其他设备需要重新登录');
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

  join(what) {
    const payload = { t: 'join', client: 'web' };
    if (typeof what === 'string') {
      payload.role = what;
    } else if (what && typeof what === 'object') {
      // 新流程：role + code 一起发，服务端只拿口令和选中的身份比。
      if (what.token) payload.token = what.token;
      if (what.role) payload.role = what.role;
      if (what.code) payload.code = what.code;
      if (!payload.token && !payload.role && !payload.code) return;
      if (what.force) payload.force = true;
    } else {
      return;
    }
    state.pendingRole = payload;
    this.lastJoin = payload;
    this.claim.setStatus('正在进入…');
    if (!this.net.send(payload)) {
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
    state.lastRole = '';
    savePref('petclub.role', '');
    this.pets.clear();
    this.chat.resetForClaim();
    this.claim.resetSelection();
    this.showClaim(note);
    this.chat.updateOnline();
  }

  leave() {
    if (this.quitting) return;
    this.net.send({ t: 'leave' });
    clearToken();                 // 主动换身份：别让本机令牌自动坐回去
    closeMenu();
    this.releaseLocal('已释放身份，重新选一个吧');
  }

  quit() {
    if (this.quitting) return;
    this.quitting = true;
    state.lastRole = '';
    savePref('petclub.role', '');
    clearToken();
    this.net.send({ t: 'leave' });
    this.net.stop();
    closeMenu();
    this.pets.clear();
    this.chat.hideMain();
    this.claim.resetSelection();
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
    if (m.kind === 'game') {
      this.onGameEvent(m);
      return;
    }
    if (m.kind === 'fx') {
      this.onFxEvent(m);
      return;
    }
    const spec = getAction(m.kind);
    const an = typeof m.an === 'string' ? m.an : '';
    // 自定义动作靠**名字**配对：本地没有这条 kind（对方刚删、我还没同步到）
    // 时，只要事件带了名字，我照样能演自己那份同名动作。
    const isCustom = !!(spec && spec.custom) || (!spec && !!an);
    const actor = m.from;
    if (!roleName(actor) || (!spec && !isCustom)) return;
    const label = an || (spec && spec.label) || '';

    const target = typeof m.to === 'string' ? m.to : null;
    const key = target ? actor : CONV_ALL;
    const text = (m.text || '').trim();
    const mt = m.mt || 'text';
    const tip = actionTip(m.kind, roleName(actor), !!target,
      target ? roleName(target) : null)
      || (label ? `${roleName(actor)} 发起了「${label}」` : '');

    if (!state.remoteOn && m.kind !== 'say') {
      // 关了同步远程互动：人不动，但聊天记录里仍留一条
      this.chat.store(key, { kind: 'tip', text: tip, ts: nowSec() });
      return;
    }

    if (isCustom) {
      // 名字是共享语义，图是各人一份：谁有同名动作谁就演自己那份。
      this.performCustom(actor, spec, label, target);
      this.chat.store(key, { kind: 'tip', text: tip, ts: nowSec() });
      return;
    }

    if (m.kind === 'say') {
      if (this.pets.has(actor)) {
        this.pets.play(actor, spec.pose, spec.dur);
        if (mt === 'text' || mt === 'emoji') this.pets.say(actor, text);
        else this.pets.say(actor, mediaLine(mt, actor));
      }
      const entry = { kind: 'msg', who: actor, mt, ts: nowSec() };
      if (m.re && typeof m.re === 'object' && m.re.text) entry.re = m.re;
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

    if (this.pets.has(actor)) {
      this.pets.play(actor, what(spec), spec.dur);
      this.pets.say(actor, text || sayLine(m.kind, actor));
    }
    this.chat.store(key, {
      kind: 'tip',
      text: actionTip(m.kind, roleName(actor), !!target,
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

  /** 某个身份自己的同名动作；没有就是 null。
   *
   * 自定义动作的名字是**共享语义**，图却是各人一份：「我做比耶、对方有比耶」
   * 时，对方演他自己的那张图，不是我的。比较用 normName，和上传去重同一套规则。
   */
  customLike(role, label) {
    const want = normName(label);
    if (!role || !want) return null;
    const acts = (state.meta && state.meta.actions) || {};
    for (const key of (state.meta && state.meta.order) || []) {
      const spec = acts[key];
      if (!spec || !spec.custom || spec.role !== role) continue;
      if (normName(spec.label) === want) return { ...spec, key };
    }
    return null;
  }

  /** 自定义动作：各演各的那一份（和桌面端 App._perform_custom 同一套规则）。
   *
   * 发起人演被点的那条 `spec`；其他人按名字找自己那份，没有就站着不动。
   * 对全员：所有有同名动作的人都跟上；点名：只有被点的那位跟。
   */
  performCustom(actor, spec, label, target = null) {
    // 动作属于发起人才由他演；右键别人的动作时 spec 是对方的，发起人没有
    // 那张图，不能硬套（会把自己的姿势顶成 idle）。
    if (spec && this.pets.has(actor) && (!spec.role || spec.role === actor)) {
      this.pets.play(actor, what(spec), spec.dur);
    }
    let peers;
    if (target) peers = target === actor ? [] : [target];
    else peers = [...this.pets.pets.keys()].filter((r) => r !== actor);
    for (const r of peers) {
      const mine = this.customLike(r, label);
      if (mine) this.pets.play(r, what(mine), mine.dur);
    }
  }

  onGameEvent(m) {
    const actor = m.from;
    if (!roleName(actor)) return;
    const parts = (m.text || '').split(':');
    if (parts[0] !== 'dice' || parts.length !== 2) return;
    const target = typeof m.to === 'string' ? m.to : null;
    const key = target ? actor : CONV_ALL;
    if (state.remoteOn) {
      this.pets.play(actor, 'jump', 1.0);
      this.pets.say(actor, parts[1]);
    }
    this.chat.store(key, {
      kind: 'game',
      who: actor,
      text: `${roleName(actor)} 掷出了 ${parts[1]}`,
      ts: nowSec(),
    });
  }

  onFxEvent(m) {
    const actor = m.from;
    if (!roleName(actor)) return;
    const target = typeof m.to === 'string' ? m.to : null;
    const key = target ? actor : CONV_ALL;
    if (state.remoteOn) showFireworks();
    this.chat.store(key, {
      kind: 'fx',
      who: actor,
      text: `${roleName(actor)} 放了烟花`,
      ts: nowSec(),
    });
  }

  playGame(key, to) {
    const me = state.me ? state.me.role : null;
    if (!me || key !== 'dice') return;
    const why = this.guardSay();
    if (why) {
      this.chat.note(why);
      return;
    }
    if (to && !state.players[to]) {
      this.chat.note(`「${roleName(to)}」不在线`);
      return;
    }
    const conv = to || CONV_ALL;
    const n = 1 + Math.floor(Math.random() * 6);
    const msg = { t: 'event', kind: 'game', text: `dice:${n}` };
    if (to) msg.to = to;
    this.net.send(msg);
    this.pets.play(me, 'jump', 1.0);
    this.pets.say(me, String(n));
    this.chat.store(conv, {
      kind: 'game', who: me, text: `你 掷出了 ${n}`, ts: nowSec(),
    });
  }

  playFireworks(to) {
    const me = state.me ? state.me.role : null;
    if (!me) return;
    const why = this.guardSay();
    if (why) {
      this.chat.note(why);
      return;
    }
    if (to && !state.players[to]) {
      this.chat.note(`「${roleName(to)}」不在线`);
      return;
    }
    const msg = { t: 'event', kind: 'fx', text: 'fireworks' };
    if (to) msg.to = to;
    this.net.send(msg);
    showFireworks();
    this.chat.store(to || CONV_ALL, {
      kind: 'fx', who: me, text: '你 放了烟花', ts: nowSec(),
    });
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
    // 自定义动作除了 kind（各人自己的图号）还要带上名字：
    // 收信端靠它找自己那份同名动作（见 performCustom）。
    if (spec.custom) msg.an = spec.label || '';
    if (to) msg.to = to;
    this.net.send(msg);

    // 自己这边也要演：动作 + 镜像 + 台词，和桌面版 do_action 一致
    if (spec.custom) {
      this.performCustom(me, spec, spec.label || '', to || null);
    } else {
      this.pets.play(me, what(spec), spec.dur);
      if (spec.gather) {
        this.pets.gather(me, to ? [to] : null);
      } else if (spec.mirror) {
        const peers = to
          ? [to]
          : [...this.pets.pets.keys()].filter((r) => r !== me);
        for (const r of peers) this.pets.play(r, spec.mirror, spec.dur);
      }
    }

    const line = sayLine(key, me);
    if (line) this.pets.say(me, line);

    this.chat.store(to || CONV_ALL, {
      kind: 'tip',
      text: actionTip(key, '你', !!to, to ? roleName(to) : null),
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
    if (!msg.re) delete msg.re;
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

  sendChat(text, re = null) {
    const why = this.guardSay();
    if (why) return why;
    const me = state.me.role;
    const key = state.currentKey;
    const to = key === CONV_ALL ? null : key;

    this.emitSay(to, { mt: 'text', text, re });
    this.playSay();
    if (this.pets.get(me)) this.pets.say(me, text);

    const entry = { kind: 'msg', who: me, mt: 'text', text, ts: nowSec() };
    if (re && re.text) entry.re = re;
    this.chat.store(key, entry);
    return '';
  }

  async sendMedia(kind, payload, re = null) {
    const why = this.guardSay();
    if (why) return why;
    const me = state.me.role;
    const key = state.currentKey;
    const to = key === CONV_ALL ? null : key;

    if (kind === 'emoji') {
      const ch = String(payload || '');
      if (!ch) return '这个表情发不出去';
      this.emitSay(to, { mt: 'emoji', text: ch, re });
      this.playSay();
      this.sayMedia('emoji', me);
      const entry = {
        kind: 'msg', who: me, mt: 'emoji', char: ch, text: ch, ts: nowSec(),
      };
      if (re && re.text) entry.re = re;
      this.chat.store(key, entry);
      return '';
    }

    if (kind === 'sticker') {
      const b64 = await urlToBase64(payload);
      if (!b64) return '这个表情包读取失败';
      this.emitSay(to, { mt: 'sticker', img: b64, re });
      this.playSay();
      this.sayMedia('sticker', me);
      const entry = {
        kind: 'msg', who: me, mt: 'sticker', img: b64, text: '[表情包]', ts: nowSec(),
      };
      if (re && re.text) entry.re = re;
      this.chat.store(key, entry);
      return '';
    }

    if (kind === 'file') {
      const file = payload;
      if (!file) return '';
      if (file.type && file.type.startsWith('image/')) {
        const b64 = await compressImage(file);
        if (!b64) return '这张图片处理失败';
        this.emitSay(to, { mt: 'img', img: b64, re });
        this.playSay();
        this.sayMedia('img', me);
        const entry = {
          kind: 'msg', who: me, mt: 'img', img: b64, text: '[图片]', ts: nowSec(),
        };
        if (re && re.text) entry.re = re;
        this.chat.store(key, entry);
        return '';
      }
      if (file.size > 2 * 1024 * 1024) return '文件太大了（上限 2MB）';
      const b64 = await fileToBase64(file);
      this.emitSay(to, {
        mt: 'file', img: b64, name: file.name, size: file.size, re,
      });
      this.playSay();
      this.sayMedia('file', me);
      const entry = {
        kind: 'msg',
        who: me,
        mt: 'file',
        img: b64,
        name: file.name,
        size: file.size,
        text: `[文件] ${file.name}`,
        ts: nowSec(),
      };
      if (re && re.text) entry.re = re;
      this.chat.store(key, entry);
      return '';
    }
    return '';
  }

  // ---------------- 角色交互 ----------------

  // 点谁谁有反应，但**只在本机**：不发 event、不进聊天记录。
  // 别人屏幕上不会看到自己被动了一下 —— 那是菜单里「摸摸头」那条指令的事。
  onPetClick(role, zone = 0.35) {
    const me = state.me ? state.me.role : null;
    if (!me || !role) return;
    const mine = role === me;
    const pet = this.pets.get(role);
    if (!pet || pet.phase === 'hold') return;

    // 点谁都在他自己头上说那一句 —— 和点自己完全一样，不加旁白。
    // 这一下只在本机发生、不进聊天记录，所以说什么不影响别人。
    const say = (line) => {
      if (line) this.pets.say(role, line);
    };

    // 「该起来动动了」是给自己的提醒，不去戳别人的角色
    if (mine && this.sitDue) {
      this.sitDue = false;
      pet.act('jump', 1.0);
      this.pets.say(role, '起来动动');
      return;
    }

    if (pet.poseName === 'sleep') {
      pet.act('jump', 1.0);
      say(sayLine('wake', role));
      return;
    }

    // 点哪儿反应不一样：头顶摸头、肚子吓一跳、脚边轻拍
    let part = 'head';
    let key = 'pet';
    if (zone >= 0.72) {
      part = 'feet';
      key = 'pat';
    } else if (zone >= 0.38) {
      part = 'belly';
      key = 'jump';
    }

    // 同一部位连点三下：复用「被戳」的回应，不用再写一套台词
    const now = performance.now() / 1000;
    const hist = (this.taps.get(role) || []).filter((t) => now - t.ts < 4);
    hist.push({ part, ts: now });
    this.taps.set(role, hist.slice(-3));
    if (hist.length >= 3 && hist.slice(-3).every((t) => t.part === part)) {
      this.taps.set(role, []);
      pet.act('jump', 1.3);
      say(replyLine(key, role));
      return;
    }

    const pose = { head: 'wave', belly: 'jump', feet: 'knock' }[part];
    pet.act(pose, part === 'belly' ? 1.0 : 1.2);
    say(sayLine(key, role));
  }

  startToy(role) {
    this.pets.startToy(role);
    this.chat.setNote('逗一逗：它会跟着鼠标跑 8 秒');
  }

  setReminder(kind, on, minutes = null) {
    if (kind !== 'water' && kind !== 'sit') return;
    const clamped = minutes === null ? null
      : Math.max(5, Math.min(600, Number(minutes) || 45));
    if (kind === 'water') {
      if (clamped !== null) this.waterMin = clamped;
      this.waterOn = !!on;
      clearTimeout(this.reminderTimer);
      this.reminderTimer = null;
      if (this.waterOn) {
        this.reminderTimer = setTimeout(
          () => this.fireWater(), this.waterMin * 60 * 1000);
      }
      this.chat.setNote(this.waterOn
        ? `喝水提醒已开启：每 ${this.waterMin} 分钟`
        : '喝水提醒已关闭');
      return;
    }
    if (clamped !== null) this.sitMin = clamped;
    this.sitOn = !!on;
    this.sitDue = false;
    clearTimeout(this.sitTimer);
    this.sitTimer = null;
    if (this.sitOn) {
      this.sitTimer = setTimeout(() => this.fireSit(), this.sitMin * 60 * 1000);
    }
    this.chat.setNote(this.sitOn
      ? `久坐提醒已开启：每 ${this.sitMin} 分钟`
      : '久坐提醒已关闭');
  }

  fireWater() {
    const me = state.me ? state.me.role : null;
    const pet = me ? this.pets.get(me) : null;
    if (!me || !pet) return;
    const text = '该喝水啦，润润嗓子';
    const go = () => {
      pet.act('drink', 2.8);
      this.pets.say(me, text);
    };
    const target = window.innerWidth / 2;
    if (Math.abs(pet.x - target) < 2) go();
    else pet.startCrawl(target, pet.y, go);
    if (this.waterOn) {
      this.reminderTimer = setTimeout(
        () => this.fireWater(), this.waterMin * 60 * 1000);
    }
  }

  fireSit() {
    const me = state.me ? state.me.role : null;
    if (!me || !this.pets.get(me)) return;
    this.sitDue = true;
    this.pets.say(me, '坐挺久了，起来动动？');
    setTimeout(() => { this.sitDue = false; }, 120 * 1000);
    if (this.sitOn) {
      this.sitTimer = setTimeout(() => this.fireSit(), this.sitMin * 60 * 1000);
    }
  }

  onPetMenu(role, x, y) {
    showMenu(x, y, role, {
      onAction: (key, to) => this.doAction(key, to),
      onLeave: () => this.leave(),
      onQuit: () => this.quit(),
      onToy: () => this.startToy(role),
      onFx: (to) => this.playFireworks(to),
    });
  }

  uploadSprite(role) {
    openUpload(role, (manifest) => this.afterUpload(manifest));
  }

  afterUpload(manifest) {
    applySprites(manifest);
    this.onSpritesChanged();
    // 广播一句「素材变了」：别人那边收到就立刻换图，不用等下一轮轮询、
    // 也不用刷新页面。断线时发不出去也不要紧，重连后各端会自己拉清单。
    this.net.send({ t: 'sprites' });
    this.chat.setNote('动作已加好，大家都能看到');
  }

  // 头像菜单里删掉 / 还原：和上传走同一条刷新链，只是提示语不一样。
  // 失败时菜单已经关了，用聊天顶上那行 note 报错 —— 静默失败最难受。
  async removeSpriteChange(role, key, act) {
    try {
      const manifest = await resetPose(role, key);
      this.chat.closeMeMenu();
      applySprites(manifest);
      this.onSpritesChanged();
      this.net.send({ t: 'sprites' });
      this.chat.setNote(act === 'del' ? '动作已删掉' : '已还原成出厂那张图');
    } catch (e) {
      this.chat.closeMeMenu();
      this.chat.setNote(e.message || '操作失败，稍后再试');
    }
  }

  // 服务端说「有人动了素材」。它只发通知，不带清单（清单里有谁、rev 多少
  // 得自己拉），所以这里先比一遍：没变就什么都不做，别白白重建一圈。
  async onSpritesPush() {
    const m = await refreshSprites();
    if (!m) return;
    this.onSpritesChanged();
  }

  // 清单变了：动作表可能多了一项，图也可能换了一批。
  // 只清角色缓存，不重开聊天 —— init() 里有事件绑定，不能重复调用。
  async onSpritesChanged() {
    try {
      await loadMeta();
    } catch (_) {
      return;
    }
    const now = spriteManifest().poses || {};
    const nowAnims = spriteManifest().anims || {};
    const touched = state.roles.filter((role) => {
      const a = this.posesSeen[role] || {};
      const b = now[role] || {};
      const ka = Object.keys(a);
      return ka.length !== Object.keys(b).length
        || ka.some((k) => a[k] !== b[k])
        // 只改帧率时图片 rev 不变，必须把 anims 也比一遍，否则缓存里的
        // loadRole 还拿着旧 fps，播放速度不会变。
        || JSON.stringify(this.animsSeen[role] || {})
           !== JSON.stringify(nowAnims[role] || {});
    });
    this.posesSeen = JSON.parse(JSON.stringify(now));
    this.animsSeen = JSON.parse(JSON.stringify(nowAnims));

    // 只有图真的变了的角色才重建。别人传自己的图，我这边其他人不该
    // 跟着闪一下；动作表（互动条 / 右键菜单）每次都重画，加了动作要能点。
    if (!touched.length) {
      this.chat.renderQuick();
      return;
    }
    for (const role of touched) clearRole(role);
    this.pets.rebuild(touched);
    this.chat.renderQuick();
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
