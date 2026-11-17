// 素材清单：进页面拉一次，之后每分钟再拉一次，变了就通知。
//
// 为什么不复用 /api/version：那是全局 build，一变就整页刷新（还会重连）。
// 用户传一张图只应该让那张图换掉，不该把所有人的页面掀一遍。
// 所以素材走自己的清单和自己的 rev，和 build 完全解耦。

import { setSpriteManifest } from './asset.js';

const POLL_MS = 60000;

let signature = '';
let timer = null;

// 清单签名：动作定义 + 每个槽位的 rev。任何一个变了都算变。
function sig(m) {
  const poses = (m && m.poses) || {};
  const acts = (m && m.actions) || {};
  const anims = (m && m.anims) || {};
  const parts = Object.keys(acts).sort().map((k) => `${k}=${acts[k].label || ''}`);
  for (const role of Object.keys(poses).sort()) {
    for (const pose of Object.keys(poses[role]).sort()) {
      parts.push(`${role}/${pose}@${poses[role][pose]}`);
    }
  }
  for (const role of Object.keys(anims).sort()) {
    for (const pose of Object.keys(anims[role]).sort()) {
      parts.push(`${role}/${pose}#${anims[role][pose].frames || 1}`
        + `@${anims[role][pose].fps || 8}`);
    }
  }
  return parts.join('|');
}

async function pull() {
  try {
    const res = await fetch('/api/sprites', { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    // 拿不到就保持现状：本地缓存 / 出厂图还能用，不该因此报错
    return null;
  }
}

// 进页面先调它，且必须在第一次 loadRole 之前 —— 否则第一屏画的是出厂图。
export async function loadSprites() {
  const m = await pull();
  if (!m) return null;
  setSpriteManifest(m);
  signature = sig(m);
  return m;
}

export function applySprites(m) {
  setSpriteManifest(m);
  signature = sig(m);
}

// 服务端主动说「有人换了素材」。返回清单表示真的变了，返回 null 表示
// 只是虚惊一场（重复通知 / 别人传的是另一个身份，我这边用不上）——
// 让调用方不用自己比对，也就不用每次都被迫重建一遍角色。
export async function refreshSprites() {
  const m = await pull();
  if (!m) return null;
  const next = sig(m);
  if (next === signature) return null;
  applySprites(m);
  return m;
}

export function startSpritePolling(onChange) {
  const tick = async () => {
    if (document.hidden) return;
    const m = await pull();
    if (!m) return;
    const next = sig(m);
    if (next === signature) return;
    applySprites(m);
    onChange(m);
  };
  clearInterval(timer);
  timer = setInterval(tick, POLL_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) tick();
  });
}
