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

export function bubbleSec() {
  return state.meta?.bubble_sec ?? 2.6;
}

export function pick(pool) {
  if (typeof pool === 'string') return pool;
  if (!Array.isArray(pool) || pool.length === 0) return '';
  return pool[Math.floor(Math.random() * pool.length)];
}
