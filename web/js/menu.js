// 角色右键菜单。和桌面版一致：右键自己 = 对全员，右键别人 = 只对他。
import { state, roleName, escapeHtml } from './state.js';

let root = null;
let open = false;

export function initMenu() {
  root = document.getElementById('ctx-menu');
  document.addEventListener('pointerdown', (e) => {
    if (open && !root.contains(e.target)) closeMenu();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });
  window.addEventListener('blur', closeMenu);
  window.addEventListener('resize', closeMenu);
}

export function closeMenu() {
  if (!root) return;
  root.classList.add('hidden');
  root.innerHTML = '';
  open = false;
}

export function showMenu(x, y, role, handlers) {
  if (!root || !state.meta) return;
  closeMenu();

  const me = state.me ? state.me.role : null;
  const isSelf = role === me;
  const parts = [];

  parts.push(
    `<div class="ctx-head">${isSelf
      ? `${escapeHtml(roleName(me))}（我）→ 全员`
      : `${escapeHtml(roleName(role))} → 只对他`}</div>`,
  );

  for (const key of state.meta.order) {
    const spec = state.meta.actions[key];
    if (!spec) continue;
    parts.push(
      `<button type="button" class="ctx-item" data-key="${escapeHtml(key)}" title="${escapeHtml(spec.hint || '')}"><span>${escapeHtml(spec.label)}</span></button>`,
    );
  }

  if (isSelf) {
    parts.push('<div class="ctx-sep"></div>');
    parts.push('<button type="button" class="ctx-item" data-cmd="leave"><span>换一个身份</span></button>');
    parts.push('<button type="button" class="ctx-item ctx-danger" data-cmd="quit"><span>退出</span></button>');
  }

  root.innerHTML = parts.join('');
  root.classList.remove('hidden');
  open = true;

  // 菜单出现在鼠标旁边；靠边时换另一侧。桌面版还会避开角色，
  // 网页里按「点哪儿出哪儿、不盖住鼠标」处理就够了。
  const margin = 8;
  const rect = root.getBoundingClientRect();
  let left = x + 14;
  let top = y + 8;
  if (left + rect.width > window.innerWidth - margin) left = x - rect.width - 14;
  if (left < margin) left = margin;
  if (top + rect.height > window.innerHeight - margin) top = window.innerHeight - rect.height - margin;
  if (top < margin) top = margin;
  root.style.left = `${left}px`;
  root.style.top = `${top}px`;

  root.querySelectorAll('.ctx-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.key;
      const cmd = btn.dataset.cmd;
      closeMenu();
      if (key) {
        handlers.onAction(key, isSelf ? null : role);
      } else if (cmd === 'leave') {
        handlers.onLeave();
      } else if (cmd === 'quit') {
        handlers.onQuit();
      }
    });
  });
}
