// 进入网页先选人物：六张立绘卡片，已被认领的现场变灰。
import { state, roleName, escapeHtml } from './state.js';
import { loadImage, cropAlpha } from './sprites.js';

export class ClaimUI {
  constructor({ onJoin }) {
    this.onJoin = onJoin;
    this.selected = null;
    this.cards = new Map();
    this.root = document.getElementById('claim-screen');
    this.grid = document.getElementById('role-grid');
    this.note = document.getElementById('claim-note');
    this.ok = document.getElementById('claim-ok');
    this.status = document.getElementById('claim-status');

    this.ok.addEventListener('click', () => {
      if (!this.selected || this.isTaken(this.selected) || !state.connected) return;
      this.setStatus('正在进入…');
      this.onJoin(this.selected);
    });
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
      const img = await loadImage(`/assets/roles/${role}/wave.png`);
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
    if (this.isTaken(role) || !state.connected) return;
    this.selected = role;
    this.render();
  }

  render() {
    if (this.selected && this.isTaken(this.selected)) this.selected = null;
    for (const role of state.roles) {
      const card = this.cards.get(role);
      if (!card) continue;
      const taken = this.isTaken(role);
      card.classList.toggle('taken', taken);
      card.classList.toggle('selected', role === this.selected && !taken);
      card.disabled = taken || !state.connected;

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
    this.ok.disabled = !this.selected || this.isTaken(this.selected) || !state.connected;
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
    this.render();
  }
}
