// 全局状态。和桌面版 App 里的字段一一对应，只少掉 Qt 那一层。
export const CONV_ALL = 'all';

export const state = {
  meta: null,
  roles: [],
  roleNames: {},
  players: {},
  me: null,
  pendingRole: null,
  connected: false,
  offline: true,
  logs: {},
  unread: {},
  currentKey: CONV_ALL,
  remoteOn: readBool('petclub.remote', true),
  petSize: readChoice('petclub.petSize', ['small', 'default', 'large'], 'default'),
  claimVisible: true,
};

function readBool(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === '1';
  } catch (_) {
    return fallback;
  }
}

function readChoice(key, choices, fallback) {
  try {
    const v = localStorage.getItem(key);
    return choices.includes(v) ? v : fallback;
  } catch (_) {
    return fallback;
  }
}

export function savePref(key, value) {
  try { localStorage.setItem(key, String(value)); } catch (_) {}
}

export function roleName(role) {
  return state.roleNames[role] || role || '';
}

export function nameOf(key) {
  return key === CONV_ALL ? '全体群聊' : roleName(key);
}

export function convKeys() {
  const me = state.me ? state.me.role : null;
  return [CONV_ALL, ...state.roles.filter((r) => r !== me)];
}

export function getLog(key) {
  if (!state.logs[key]) state.logs[key] = [];
  return state.logs[key];
}

export function appendEntry(key, entry) {
  const log = getLog(key);
  log.push(entry);
  if (log.length > 200) log.splice(0, log.length - 200);
}

export function unreadTotal() {
  return Object.values(state.unread).reduce((n, v) => n + (v || 0), 0);
}

export function clearUnread(key) {
  if (state.unread[key]) delete state.unread[key];
}

export function resetChat() {
  state.logs = {};
  state.unread = {};
  state.currentKey = CONV_ALL;
}

export function onlineCount() {
  return Object.values(state.players).filter(Boolean).length;
}

export function isOnline(role) {
  return !!(role && state.players[role]);
}

export function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function formatTime(ts) {
  const d = new Date((ts || 0) * 1000);
  if (!Number.isFinite(d.getTime())) return '';
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return `${hh}:${mm}`;
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${mo}-${dd}`;
}

export function nowSec() {
  return Date.now() / 1000;
}
