// 自定义动作的移动轨迹（和 petclub/motion.py 一一对应）。
//
// 只算「相对起点的偏移」：轨迹存的是比例，播放时按当前窗口算像素。
// 改公式必须两边一起改，否则同一个动作在桌面和网页上飞得不一样。

function smooth(u) {
  u = Math.min(1, Math.max(0, u));
  return u * u * (3 - 2 * u);
}

/**
 * @param {object} move   {kind, dir, height, distance}
 * @param {number} t      0..1
 * @param {number} width  屏幕宽（像素）
 * @param {number} height 屏幕高（像素）
 * @param {number} roomLeft  起点左边还剩多少空间
 * @param {number} roomRight 起点右边还剩多少空间
 * @param {number} rise      起点到屏幕顶部还剩多少空间
 * @param {number} drop      起点到屏幕底部（或地面）还剩多少空间
 * @returns {[number, number]} [dx, dy]
 */
export function moveOffset(move, t, width, height, roomLeft, roomRight, rise,
  drop = 0) {
  if (!move || !move.kind) return [0, 0];
  const kind = move.kind;
  if (!['line', 'arc', 'orbit', 'hover'].includes(kind)) return [0, 0];
  t = Math.min(1, Math.max(0, Number(t) || 0));

  const hp = Number(move.height);
  const dp = Number(move.distance);
  const heightPct = Number.isFinite(hp) ? hp : 0.35;
  const distPct = Number.isFinite(dp) ? dp : 0.8;
  const riseRoom = Math.max(0, Number(rise) || 0);
  const fallRoom = Math.max(0, Number(drop) || 0);
  // 默认往上飞；顶部放不下就掉头向下，幅度仍按屏幕高度算。
  const wanted = Math.max(0, heightPct) * Math.max(0, height);
  let up;
  let amp;
  if (riseRoom >= wanted) {
    up = true;
    amp = wanted;
  } else if (fallRoom >= wanted) {
    up = false;
    amp = wanted;
  } else if (riseRoom >= fallRoom) {
    up = true;
    amp = riseRoom;
  } else {
    up = false;
    amp = fallRoom;
  }
  const vdir = up ? -1 : 1;

  if (kind === 'hover') {
    return [0, vdir * amp * 0.5 * (1 - Math.cos(2 * Math.PI * t))];
  }

  const leftRoom = Math.max(0, Number(roomLeft) || 0);
  const rightRoom = Math.max(0, Number(roomRight) || 0);
  // 幅度固定成「跨度比例的一半屏宽」：站中间还是贴边，飞出去的距离一样。
  const ampX = Math.max(0, distPct) * Math.max(0, width) / 2;
  const preferRight = move.dir !== 'rl';
  let room = preferRight ? rightRoom : leftRoom;
  let sign = preferRight ? 1 : -1;
  const other = preferRight ? leftRoom : rightRoom;
  if (room < ampX && other > room) {
    // 首选方向放不下整段，掉头往空间大的那边飞。
    room = other;
    sign = -sign;
  }
  const reach = Math.min(ampX, room);

  if (kind === 'orbit') {
    // 贴边时整圈会有一半出屏：左右半径分开，空间小的一侧压扁。
    const rxR = Math.min(ampX, rightRoom);
    const rxL = Math.min(ampX, leftRoom);
    const ry = amp / 2;
    const ang = 2 * Math.PI * t * sign;
    const s = Math.sin(ang);
    return [(s > 0 ? rxR : rxL) * s, vdir * (ry - ry * Math.cos(ang))];
  }

  const out = Math.sin(Math.PI * t);
  const lift = kind === 'line'
    ? smooth(Math.min(t / 0.18, (1 - t) / 0.18))
    : out;
  return [sign * reach * out, vdir * amp * lift];
}
