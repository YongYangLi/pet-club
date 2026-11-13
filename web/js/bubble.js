// 角色头顶的气泡。位置由 pets.js 每帧推过来，这里只管显示和消失。
import { bubbleSec } from './actions.js';

export class BubbleLayer {
  constructor() {
    this.root = document.getElementById('bubble-layer');
    this.items = new Map();
  }

  say(role, text) {
    if (!role || !text) return;
    let item = this.items.get(role);
    if (!item) {
      const el = document.createElement('div');
      el.className = 'bubble';
      this.root.appendChild(el);
      item = { el, until: 0 };
      this.items.set(role, item);
    }
    item.el.textContent = text;
    item.el.classList.add('show');
    item.until = performance.now() + bubbleSec() * 1000;
  }

  position(role, x, y) {
    const item = this.items.get(role);
    if (!item) return;
    item.el.style.left = `${x}px`;
    item.el.style.top = `${y}px`;
  }

  update() {
    const now = performance.now();
    for (const item of this.items.values()) {
      if (item.until && now > item.until) {
        item.el.classList.remove('show');
        item.until = 0;
      }
    }
  }

  remove(role) {
    const item = this.items.get(role);
    if (!item) return;
    item.el.remove();
    this.items.delete(role);
  }

  clear() {
    for (const item of this.items.values()) item.el.remove();
    this.items.clear();
  }
}
