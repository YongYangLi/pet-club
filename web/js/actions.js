// 动作、台词、互动痕迹。数据来自 /api/meta，源头仍然是 petclub/actions.py。
import { state } from './state.js';

export async function loadMeta() {
  const res = await fetch('/api/meta', { cache: 'no-store' });
  if (!res.ok) throw new Error('meta ' + res.status);
  const meta = await res.json();
  state.meta = meta;
  state.roles = meta.roles || [];
  state.roleNames = meta.role_names || {};
  return meta;
}

export function getAction(key) {
  const spec = state.meta && state.meta.actions ? state.meta.actions[key] : null;
  return spec ? { ...spec, key } : null;
}

/** 动作名的比较键：去首尾空白、内部空白归一、大小写不敏感。
 *
 * 名字是**跨身份共享**的那个东西 —— 我做「比耶」、对方也有「比耶」时，
 * 对方跟着做的是他自己那张图。归一化规则必须和桌面端
 * `petclub/custom.py:norm_label` 完全一致：上传去重、跟着做配对都靠它，
 * 两端算出来的键不一样，就会出现「明明同名却不跟」。
 */
export function normName(label) {
  return String(label || '').trim().replace(/\s+/g, ' ').toLowerCase();
}

export function what(spec) {
  return spec && spec.launch ? 'launch' : spec && spec.pose;
}

export function sayLine(key, role) {
  const pool = state.meta?.voice?.[role]?.[`s.${key}`];
  return pick(pool);
}

export function replyLine(key, role) {
  const pool = state.meta?.voice?.[role]?.[`s.${key}.me`];
  return pick(pool);
}

export function mediaLine(mt, role) {
  const pool = state.meta?.media_line?.[mt]?.[role];
  return pick(pool);
}

export function tipText(kind, actor, one = false, target = null) {
  const pat = state.meta?.tip?.[kind];
  if (!pat) return '';
  const a = actor || '';
  if (a === '你') {
    return '你 ' + (one ? pat.to_one.replace('{who}', target || '') : pat.to_all);
  }
  return `${a} ${one ? pat.from_one : pat.from_all}`;
}

/** 互动提示文案。TIP 表里只有内置指令，自定义动作用动作名拼一句 ——
 *  否则传了图、动了，聊天记录里什么都不会留（桌面端 _log_tip 同一套规则）。 */
export function actionTip(kind, actor, one = false, target = null) {
  const base = tipText(kind, actor, one, target);
  if (base) return base;
  const label = state.meta?.actions?.[kind]?.label;
  if (!label) return '';
  const where = one && target ? `对${target}` : '';
  return `${actor || ''} ${where}发起了「${label}」`;
}

/** 不对应任何动作、但可以直接换图的姿势。
 *  站立是角色的默认姿态，不进动作表（它不是能发起的指令），
 *  可用户想换的正是「我站着的样子」—— 所以单独列一份。 */
export const LOOSE_POSES = [
  { key: 'idle', label: '站立', pose: 'idle' },
];

export function loosePose(key) {
  return LOOSE_POSES.find((p) => p.key === key) || null;
}

export function bubbleSec() {
  return state.meta?.bubble_sec ?? 2.6;
}

export function pick(pool) {
  if (typeof pool === 'string') return pool;
  if (!Array.isArray(pool) || pool.length === 0) return '';
  return pool[Math.floor(Math.random() * pool.length)];
}
