// 进入网页先选人物：六张立绘卡片，已被认领的现场变灰。
// 启用了口令时，选中「设了口令的身份」后，下方才出现他的口令框。
import { state, roleName, escapeHtml } from './state.js';
import { loadImage, cropAlpha } from './sprites.js';
import { spriteUrl } from './asset.js';

export class ClaimUI {
  constructor({ onJoin }) {
    this.onJoin = onJoin;
    this.selected = null;
    this.auth = false;
    this.locked = new Set();
    this.cards = new Map();
    this.root = document.getElementById('claim-screen');
    this.grid = document.getElementById('role-grid');
    this.note = document.getElementById('claim-note');
    this.ok = document.getElementById('claim-ok');
    this.status = document.getElementById('claim-status');
    this.title = document.getElementById('claim-title');
    this.sub = document.getElementById('claim-sub');
    this.authBox = document.getElementById('claim-auth');
    this.authLabel = document.getElementById('claim-auth-label');
    this.codeInput = document.getElementById('claim-code');

    this.ok.addEventListener('click', () => {
      if (!state.connected || !this.selected) return;
      if (this.needCode()) {
        const code = this.codeInput.value.trim();
        if (!code) return;
        this.setStatus('正在进入…');
        this.onJoin({ role: this.selected, code });
        return;
      }
      this.setStatus('正在进入…');
      this.onJoin(this.selected);
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
    // 进入后服务端会回 role_online，再弹「要不要让对方下线」。
    if (this.isTaken(role) && !this.auth) return;
    this.selected = role;
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
    this.ok.disabled = need
      ? (!this.codeInput.value.trim() || !state.connected)
      : (!this.selected || (!this.auth && this.isTaken(this.selected))
         || !state.connected);
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
}
