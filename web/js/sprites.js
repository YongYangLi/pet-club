// 素材加载 + 和 sprites.py 对齐的窗口尺寸计算。
// 浏览器和 Qt 的差别只在「画到哪儿」：这里用同一个最大包围盒、
// 同一套底部居中 + crawl 两帧质心修正。

import { assetUrl } from './asset.js';

const POSES = ['idle', 'wave', 'jump', 'sleep', 'fall', 'drag',
  'drink', 'heart', 'knock', 'crawl_1', 'crawl_2'];
const CRAWL_FRAMES = ['crawl_1', 'crawl_2'];

const imageCache = new Map();
const roleCache = new Map();
const cropCache = new Map();

export function loadImage(src) {
  if (imageCache.has(src)) return imageCache.get(src);
  const p = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => {
      imageCache.delete(src);
      reject(new Error('load failed: ' + src));
    };
    img.src = src;
  });
  imageCache.set(src, p);
  return p;
}

export async function loadRole(role) {
  if (roleCache.has(role)) return roleCache.get(role);
  const p = (async () => {
    const poses = {};
    await Promise.all(POSES.map(async (pose) => {
      try {
        poses[pose] = await loadImage(assetUrl(`/assets/roles/${role}/${pose}.png`));
      } catch (_) {
        // 素材缺一张不影响整个角色，和 sprites.load_all 的退化一致
      }
    }));
    if (!poses.idle) throw new Error('missing idle: ' + role);

    const all = Object.values(poses);
    const w = Math.max(...all.map((im) => im.naturalWidth));
    const h = Math.max(...all.map((im) => im.naturalHeight));
    const bodyW = poses.idle.naturalWidth;

    // crawl_1 / crawl_2 的包围盒宽度不同，按 alpha 质心对齐，
    // 否则换帧时人会横着闪一下（和 sprites.py 同一个做法）。
    const dx = {};
    const cs = CRAWL_FRAMES.filter((n) => poses[n]).map((n) => [n, centroidX(poses[n])]);
    if (cs.length === 2) {
      const avg = (cs[0][1] + cs[1][1]) / 2;
      for (const [n, c] of cs) dx[n] = Math.round(avg - c);
    }

    return { role, poses, w, h, bodyW, dx };
  })();
  roleCache.set(role, p);
  try {
    return await p;
  } catch (e) {
    roleCache.delete(role);
    throw e;
  }
}

function centroidX(img) {
  const c = document.createElement('canvas');
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  let data;
  try {
    data = ctx.getImageData(0, 0, c.width, c.height).data;
  } catch (_) {
    return c.width / 2;
  }
  let tot = 0;
  let acc = 0;
  for (let y = 0; y < c.height; y += 2) {
    for (let x = 0; x < c.width; x += 2) {
      const a = data[(y * c.width + x) * 4 + 3];
      if (a) {
        tot += a;
        acc += a * x;
      }
    }
  }
  return tot ? acc / tot : c.width / 2;
}

// 认领卡片和头像用：裁掉透明边，按最大框等比缩放，返回 dataURL。
export function cropAlpha(img, maxW, maxH) {
  const key = `${img.src}|${maxW}|${maxH}`;
  if (cropCache.has(key)) return cropCache.get(key);

  const c = document.createElement('canvas');
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  let data;
  try {
    data = ctx.getImageData(0, 0, c.width, c.height).data;
  } catch (_) {
    return img.src;
  }

  let minX = c.width;
  let minY = c.height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < c.height; y++) {
    for (let x = 0; x < c.width; x++) {
      if (data[(y * c.width + x) * 4 + 3] > 8) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < minX || maxY < minY) return img.src;

  const bw = maxX - minX + 1;
  const bh = maxY - minY + 1;
  const scale = Math.min(maxW / bw, maxH / bh, 1);
  const out = document.createElement('canvas');
  out.width = Math.max(1, Math.round(bw * scale));
  out.height = Math.max(1, Math.round(bh * scale));
  const octx = out.getContext('2d');
  octx.imageSmoothingQuality = 'high';
  octx.drawImage(img, minX, minY, bw, bh, 0, 0, out.width, out.height);
  let url;
  try {
    url = out.toDataURL('image/png');
  } catch (_) {
    url = img.src;
  }
  cropCache.set(key, url);
  return url;
}

export async function avatarFor(role, size = 64) {
  try {
    const img = await loadImage(assetUrl(`/assets/roles/${role}/wave.png`));
    return cropAlpha(img, size, size);
  } catch (_) {
    try {
      const img = await loadImage(assetUrl(`/assets/roles/${role}/idle.png`));
      return cropAlpha(img, size, size);
    } catch (_) {
      return '';
    }
  }
}
