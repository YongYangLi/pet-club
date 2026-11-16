// 进入网页先选人物：六张立绘卡片，已被认领的现场变灰。
// 启用了口令时，选中「设了口令的身份」后，下方才出现他的口令框。
import { state, roleName, escapeHtml } from './state.js';
import { loadImage, cropAlpha } from './sprites.js';
import { spriteUrl } from './asset.js';

export class ClaimUI {
  constructor({ onJoin, codeFor }) {
    this.onJoin = onJoin;
    // 本机记住的「每个身份上次用的口令」：选中谁就填谁那条。
    this.codeFor = codeFor || (() => '');
    this.selected = null;
    this.auth = false;
    this.locked = new Set();
    this.cards = new Map();
    this.root = document.getElementById('claim-screen');
    this.grid = document.getElementById('role-grid');
    this.note = document.getElementById('claim-note');
    this.ok = document.getElementById('claim-ok');
    this.status = document.getElementById('claim-status');
    this.forceHint = document.getElementById('claim-force-hint');
    this.title = document.getElementById('claim-title');
    this.sub = document.getElementById('claim-sub');
    this.authBox = document.getElementById('claim-auth');
    this.authLabel = document.getElementById('claim-auth-label');
    this.codeInput = document.getElementById('claim-code');

    this.ok.addEventListener('click', () => {
      if (!state.connected || !this.selected) return;
      const force = this.isForce();
      if (this.needCode()) {
        const code = this.codeInput.value.trim();
        if (!code) return;
        this.setStatus('正在进入…');
        this.onJoin({ role: this.selected, code, force });
        return;
      }
      this.setStatus('正在进入…');
      this.onJoin({ role: this.selected, force });
    });
    this.codeInput.addEventListener('input', () => this.render());
    this.codeInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        this.ok.click();
      }
    });
  }

  /** 选中的身份设了口令吗？只有这种才需要输入框。 */
  needCode() {
    return !!(this.auth && this.selected && this.locked.has(this.selected));
  }

  /** 选中的身份已经被别人认领：按钮直接变成「强制进入」。 */
  isForce() {
    return !!(this.auth && this.selected && this.isTaken(this.selected));
  }

  /** 启用了口令：卡片照常显示，选中后再决定要不要弹口令框。 */
  setAuth(on) {
    this.auth = !!on;
    this.grid.classList.toggle('auth', this.auth);
    if (this.title) this.title.textContent = '挑一个身份坐下';
    if (this.sub) {
      this.sub.textContent = this.auth
        ? '选中后输入属于他的认领口令；已登录过的设备会自动进入'
        : '同一时间每个身份只能有一个人';
    }
    this.render();
  }

  /** 服务端告诉我们的：哪些身份设了口令。 */
  setLocked(roles) {
    this.locked = new Set(Array.isArray(roles) ? roles : []);
    if (this.selected && !this.codeInput.value) {
      this.codeInput.value = this.codeFor(this.selected) || '';
    }
    this.render();
  }

  init() {
    this.grid.innerHTML = '';
    this.cards.clear();
    for (const role of state.roles) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'role-card';
      card.dataset.role = role;
      card.innerHTML = `
        <div class="role-portrait"><span class="role-loading"></span></div>
        <div class="role-name">${escapeHtml(roleName(role))}</div>
        <div class="role-tag">可选</div>`;
      card.addEventListener('click', () => this.pick(role));
      this.grid.appendChild(card);
      this.cards.set(role, card);
      this.loadPreview(role, card);
    }
    this.render();
  }

  async loadPreview(role, card) {
    try {
      const img = await loadImage(spriteUrl(role, 'wave'));
      const portrait = card.querySelector('.role-portrait');
      const im = document.createElement('img');
      im.src = cropAlpha(img, 118, 138);
      im.alt = roleName(role);
      portrait.innerHTML = '';
      portrait.appendChild(im);
    } catch (_) {
      // 素材缺失时留一个空卡片，不影响认领
    }
  }

  isTaken(role) {
    return !!state.players[role];
  }

  pick(role) {
    if (!state.connected) return;
    // 没启用口令时，被占的身份点了也没用；启用了口令则允许选中 ——
    // 选中后按钮会直接变成「强制进入」。
    if (this.isTaken(role) && !this.auth) return;
    if (role !== this.selected) {
      // 切身份就换成本机记着的那条口令；没记过就清空，避免拿上一个人的
      // 口令去撞下一个人。
      this.selected = role;
      this.codeInput.value = this.codeFor(role) || '';
      this.setStatus('');
    }
    this.render();
  }

  render() {
    if (!this.auth && this.selected && this.isTaken(this.selected)) {
      this.selected = null;
    }
    for (const role of state.roles) {
      const card = this.cards.get(role);
      if (!card) continue;
      const taken = this.isTaken(role);
      card.classList.toggle('taken', taken);
      card.classList.toggle('selected', role === this.selected);
      card.disabled = !state.connected;

      const tag = card.querySelector('.role-tag');
      if (taken) {
        tag.textContent = '已被认领';
        tag.className = 'role-tag taken';
      } else if (role === this.selected) {
        tag.textContent = '就是你';
        tag.className = 'role-tag selected';
      } else {
        tag.textContent = '可选';
        tag.className = 'role-tag';
      }
    }

    const need = this.needCode();
    this.authBox.classList.toggle('hidden', !need);
    if (need && this.authLabel) {
      this.authLabel.textContent = `「${roleName(this.selected)}」的认领口令`;
    }

    // 选中的人已经被别人认领：按钮直接就是「强制进入」，旁边红字说明。
    const force = this.isForce();
    this.ok.textContent = force ? '强制进入' : '进入';
    if (force) {
      const who = state.players[this.selected] || {};
      const where = who.client === 'web' ? '网页版' : '桌面版';
      this.forceHint.textContent =
        `当前用户「${roleName(this.selected)}」正在${where}登录，`
        + '强制进入可以让旧设备下线';
      this.forceHint.classList.remove('hidden');
    } else {
      this.forceHint.classList.add('hidden');
    }

    this.ok.disabled = need
      ? (!this.codeInput.value.trim() || !state.connected)
      : (!this.selected || !state.connected);
  }

  show(note = '') {
    this.root.classList.remove('hidden');
    state.claimVisible = true;
    this.note.textContent = note || '';
    this.render();
  }

  hide() {
    this.root.classList.add('hidden');
    state.claimVisible = false;
  }

  setStatus(text, isError = false) {
    this.status.textContent = text || '';
    this.status.classList.toggle('error', !!isError);
  }

  setNote(text) {
    this.note.textContent = text || '';
  }

  resetSelection() {
    this.selected = null;
    this.codeInput.value = '';
    this.render();
  }

  /** 被顶下线后回到这里：选中原来那个人，并把上次的口令填好。 */
  preselect(role, code = '') {
    if (!role || !state.roles.includes(role)) return;
    this.selected = role;
    if (code) this.codeInput.value = code;
    this.render();
  }
}
