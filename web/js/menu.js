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

  const mine = [];
  for (const key of state.meta.order) {
    const spec = state.meta.actions[key];
    if (!spec) continue;
    // 网页里本来就站在聊天窗口里，「传个话…」没有意义
    if (key === 'say') continue;
    // 自定义动作单独成组，放到烟花下面 —— 内置指令是「大家都有」的，
    // 自己加的那些是「我这个人多出来的」，混在一起每次都要从头找。
    if (spec.custom) {
      // 列的一律是**我自己的**：动作是我做的，图也只在我这儿；右键别人
      // 只是把目标收窄成「只对他」。换成列对方的动作，这里点了也没有能
      // 贴在我身上的图（那是对方的形象）。
      if (spec.role === me) mine.push({ key, spec });
      continue;
    }
    parts.push(
      `<button type="button" class="ctx-item" data-key="${escapeHtml(key)}" title="${escapeHtml(spec.hint || '')}"><span>${escapeHtml(spec.label)}</span></button>`,
    );
  }
  parts.push('<button type="button" class="ctx-item" data-cmd="toy"><span>逗一逗</span></button>');
  parts.push('<button type="button" class="ctx-item" data-cmd="fx"><span>烟花</span></button>');
  // 上下各一条普通分隔线，把「我自己加的」包成一组，和桌面端一致。
  // 下面那条由「换一个身份」前面那条兼任（自己菜单）；别人的菜单里这一组
  // 是最后一段，末尾不再补线 —— 和 QMenu 不绘制末尾分隔符的观感对齐。
  if (mine.length) {
    parts.push('<div class="ctx-sep"></div>');
    for (const { key, spec } of mine) {
      parts.push(
        `<button type="button" class="ctx-item" data-key="${escapeHtml(key)}" title="${escapeHtml(spec.hint || '')}"><span>${escapeHtml(spec.label)}</span></button>`,
      );
    }
  }
  // 「加个动作…」不在这儿：给角色加动作管的是「我是谁」这一层的东西，
  // 统一放在左下角头像菜单的「我的动作」里，和删除 / 还原在一块。

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
      } else if (cmd === 'toy') {
        if (handlers.onToy) handlers.onToy(role);
      } else if (cmd === 'fx') {
        if (handlers.onFx) handlers.onFx(isSelf ? null : role);
      } else if (cmd === 'leave') {
        handlers.onLeave();
      } else if (cmd === 'quit') {
        handlers.onQuit();
      }
    });
  });
}
