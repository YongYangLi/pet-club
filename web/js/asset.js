// 素材地址：出厂图挂 build 号，用户上传的图挂远程 rev。
//
// 渲染层只想知道「这张图该从哪儿取」，所以分岔收在这里：
//   · 服务端清单里这个角色的这个姿势有覆盖图 → /sprites/{role}/{pose}.png?v={rev}
//   · 没有 → /assets/roles/{role}/{pose}.png?v={build}
//
// 两套版本号是刻意分开的。build 一变，全群页面都要刷新（那是代码更新）；
// 用户传一张图只让 rev 变，URL 变了缓存自然失效，但 build 不动 ——
// 于是"加个动作"是即时的，不会把所有人的页面掀一遍。

const BUILD = (() => {
  const el = document.querySelector('meta[name="petclub-build"]');
  return el ? (el.content || '') : '';
})();

// 服务端 /api/sprites 的清单：{actions, poses: {p1: {idle: 'rev', ...}}, seq}
let manifest = { actions: {}, poses: {} };

export function setSpriteManifest(m) {
  manifest = (m && typeof m === 'object') ? m : { actions: {}, poses: {} };
  if (!manifest.poses) manifest.poses = {};
}

export function spriteManifest() {
  return manifest;
}

export function assetUrl(path) {
  if (!BUILD) return path;
  return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'v=' + encodeURIComponent(BUILD);
}

// 这个角色有哪些姿势被用户替换过（也可能是新动作的姿势）
export function remotePoses(role) {
  const per = manifest.poses && manifest.poses[role];
  return per ? Object.keys(per) : [];
}

export function spriteRev(role, pose) {
  const per = manifest.poses && manifest.poses[role];
  return (per && per[pose]) || '';
}

export function spriteUrl(role, pose) {
  const rev = spriteRev(role, pose);
  if (rev) {
    return `/sprites/${encodeURIComponent(role)}/${encodeURIComponent(pose)}.png?v=${encodeURIComponent(rev)}`;
  }
  return assetUrl(`/assets/roles/${role}/${pose}.png`);
}

// 出厂原图，**永远不看覆盖图**。
// 用在「换掉哪个动作」的选择器上：用户要认的是这个动作本来长什么样，
// 而不是自己上次换上去的那张 —— 后者他自己记得，前者才是判断依据。
export function factoryUrl(role, pose) {
  return assetUrl(`/assets/roles/${role}/${pose}.png`);
}

export function buildId() {
  return BUILD;
}
