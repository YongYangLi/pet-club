// 上传自定义姿势：选图 → 浏览器内归一化 → 传到服务器 → 所有人可见。
//
// 为什么归一化必须在浏览器里做：
//   角色窗口是按「这个角色所有姿势的最大宽高」开的（见 sprites.js loadRole），
//   直接存一张 3000×3000 的原图会把窗口撑爆，其他姿势一起被缩成小不点。
//   所以上传前要：去白底 → 裁透明边 → 等比缩进该角色现有的包围盒。
//
// 服务端只做校验不做图像处理：服务器上没装 Pillow，也不该为一次上传吃 CPU。

import { state } from './state.js';
import { loadRole } from './sprites.js';
import { factoryUrl, spriteRev, spriteUrl } from './asset.js';
import { LOOSE_POSES, loosePose, normName } from './actions.js';

const CAP = 600;      // 分析用工作尺寸：够精细，又不是几百万像素
const TOL = 34;       // 去背容差（RGB 每通道）

let box = null;       // { role, b64, w, h, onDone, file, mode }
let root = null;
let preview = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function ensureDom() {
  if (root) return root;
  root = el('div', 'upload-mask hidden');
  root.innerHTML = `
    <div class="upload-card" role="dialog" aria-modal="true">
      <div class="upload-head">
        <b id="up-title">加个动作</b>
        <button type="button" class="upload-x" data-cmd="close" aria-label="关闭">×</button>
      </div>
      <div class="upload-body">
        <div class="upload-pickrow">
          <label class="upload-pick">
            <input id="up-file" type="file" accept="image/*" hidden>
            <span>选一张图</span>
          </label>
          <label class="upload-key"><input id="up-key" type="checkbox" checked><span>去掉白色背景</span></label>
        </div>
        <div class="upload-stage"><canvas id="up-preview"></canvas></div>
        <div class="upload-fields">
          <div class="upload-row upload-pose-row">
            <label>动作</label>
            <div class="pose-pick" id="up-poses"></div>
          </div>
          <div class="upload-row" id="up-new-row">
            <label for="up-label">名字</label>
            <input id="up-label" maxlength="10" placeholder="比如 跳舞、比耶">
          </div>
          <div class="upload-row" id="up-dur-row">
            <label for="up-dur">时长</label>
            <input id="up-dur" type="number" min="0.6" max="8" step="0.2" value="2">
            <span class="upload-unit">秒</span>
          </div>
        </div>
        <p class="upload-note" id="up-note">图会自动裁边、缩放到和这个角色一样大。</p>
      </div>
      <div class="upload-foot">
        <span class="upload-msg" id="up-msg"></span>
        <button type="button" class="btn btn-ghost hidden" data-cmd="reset" id="up-reset">恢复默认图</button>
        <button type="button" class="btn" data-cmd="close">取消</button>
        <button type="button" class="btn btn-primary" data-cmd="send" id="up-send" disabled>上传</button>
      </div>
    </div>`;
  document.body.appendChild(root);
  preview = root.querySelector('#up-preview');

  root.addEventListener('click', (e) => {
    const cmd = e.target.getAttribute && e.target.getAttribute('data-cmd');
    if (cmd === 'close') closeUpload();
    else if (cmd === 'send') submit();
    else if (cmd === 'reset') resetAction();
    else if (e.target === root) closeUpload();
  });
  root.querySelector('#up-file').addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) prepare(file);
    e.target.value = '';
  });
  root.querySelector('#up-key').addEventListener('change', () => {
    if (box && box.file) prepare(box.file);
  });
  root.querySelector('#up-poses').addEventListener('click', (e) => {
    const tile = e.target.closest && e.target.closest('.pose-tile');
    if (!tile || !box) return;
    box.mode = tile.dataset.key || '';
    root.querySelectorAll('.pose-tile').forEach((t) => {
      t.classList.toggle('on', t === tile);
    });
    syncMode();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && root && !root.classList.contains('hidden')) closeUpload();
  });
  return root;
}

export function openUpload(role, onDone) {
  ensureDom();
  box = { role, b64: '', w: 0, h: 0, onDone, file: null, mode: '' };
  const name = (document.querySelector('#up-title'));
  if (name) name.textContent = `给「${roleName(role)}」加个动作`;

  renderPosePick(role);
  syncMode();
  root.querySelector('#up-label').value = '';
  root.querySelector('#up-dur').value = '2';
  root.querySelector('#up-send').disabled = true;
  root.querySelector('#up-reset').disabled = false;
  root.querySelector('#up-msg').textContent = '';
  note('图会自动裁边、缩放到和这个角色一样大。');
  preview.getContext('2d').clearRect(0, 0, preview.width, preview.height);
  preview.width = 0;
  preview.height = 0;
  root.classList.remove('hidden');
}

// 「换哪个动作」的缩略图格子。
//
// 内置动作一律显示**出厂原图**（factoryUrl 不看覆盖图）：用户要认的是这个
// 动作本来长什么样；只给一句「换掉「打招呼」的图」，六个动作里挑一个，谁也
// 不知道打招呼是挥手还是点头。已经换过的打「已换」，进去还能再换或还原。
function poseTile(key, name, src, badge, isNew = false) {
  const b = el('button', 'pose-tile');
  b.type = 'button';
  b.dataset.key = key;
  if (isNew) b.classList.add('is-new');

  const thumb = el('span', 'pose-thumb');
  if (isNew) {
    thumb.textContent = '＋';
  } else {
    const img = document.createElement('img');
    img.src = src;
    img.alt = '';
    img.draggable = false;
    img.loading = 'lazy';
    thumb.appendChild(img);
  }
  b.append(thumb, el('span', 'pose-tile-name', name));
  if (badge) {
    b.appendChild(el('i', 'pose-badge', badge));
    b.classList.add('is-changed');
  }
  return b;
}

function renderPosePick(role) {
  const host = root.querySelector('#up-poses');
  host.innerHTML = '';
  host.appendChild(poseTile('', '新建动作', '', '', true));

  // 站立：不是动作，是角色的默认姿态，但同样可以换
  for (const loose of LOOSE_POSES) {
    host.appendChild(poseTile(loose.key, loose.label,
      factoryUrl(role, loose.pose), spriteRev(role, loose.pose) ? '已换' : ''));
  }

  const meta = state.meta || {};
  const acts = meta.actions || {};
  const builtin = [];
  const mine = [];
  for (const key of (meta.order || [])) {
    const spec = acts[key];
    if (!spec || !spec.pose) continue;        // 起飞没有姿势图
    if (!spec.custom) builtin.push(key);
    else if (spec.role === role) mine.push(key);   // 别人的自定义动作不在这儿换
  }
  for (const key of builtin) {
    const spec = acts[key];
    host.appendChild(poseTile(key, spec.label, factoryUrl(role, spec.pose),
      spriteRev(role, spec.pose) ? '已换' : ''));
  }
  for (const key of mine) {
    const spec = acts[key];
    host.appendChild(poseTile(key, spec.label, spriteUrl(role, spec.pose), '自定义'));
  }
}

function specOf(key) {
  return (state.meta && state.meta.actions && state.meta.actions[key]) || null;
}

// 这个选择项对应哪张图：动作取 spec.pose，站立这类取姿势名本身。
function poseOf(key) {
  const spec = specOf(key);
  if (spec && spec.pose) return spec.pose;
  const loose = loosePose(key);
  return loose ? loose.pose : '';
}

function syncMode() {
  if (!root || !box) return;
  const key = box.mode;
  const isNew = !key;
  root.querySelector('#up-new-row').classList.toggle('hidden', !isNew);
  root.querySelector('#up-dur-row').classList.toggle('hidden', !isNew);

  const spec = isNew ? null : specOf(key);
  const pose = isNew ? '' : poseOf(key);
  const changed = !!(pose && spriteRev(box.role, pose));
  const rst = root.querySelector('#up-reset');
  // 没被换过的图没什么可还原的，按钮就别摆出来
  rst.classList.toggle('hidden', isNew || !changed);
  rst.textContent = (spec && spec.custom) ? '删掉这个动作' : '恢复默认图';

  root.querySelector('#up-send').textContent = isNew ? '上传' : '换这张';
}

export function closeUpload() {
  if (root) root.classList.add('hidden');
  box = null;
}

function roleName(role) {
  const meta = state.meta || {};
  return (meta.role_names && meta.role_names[role]) || role;
}

function note(msg, bad = false) {
  const n = root.querySelector('#up-note') || root.querySelector('#up-msg');
  n.textContent = msg;
  n.classList.toggle('bad', bad);
}

function msg(text, bad = false) {
  const n = root.querySelector('#up-msg');
  n.textContent = text;
  n.classList.toggle('bad', bad);
}

// ---------------- 图像处理 ----------------

function readImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('这张图读不出来')); };
    img.src = url;
  });
}

function drawCapped(img) {
  const sw = img.naturalWidth || img.width;
  const sh = img.naturalHeight || img.height;
  const k = Math.min(1, CAP / Math.max(sw, sh));
  const w = Math.max(1, Math.round(sw * k));
  const h = Math.max(1, Math.round(sh * k));
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(img, 0, 0, w, h);
  return { c, ctx, w, h };
}

// 从四边往里灌，只清和四角同色、且连通到边缘的像素。
// 人物内部的白色（眼睛高光、白衣服）不会被误伤 —— 它们连不到边缘。
function keyOut(data, w, h, tol) {
  let clear = 0;
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] < 8) clear++;
  }
  if (clear > (w * h) / 20) return;      // 已经是抠好的透明图，别再动

  let r = 0; let g = 0; let b = 0;
  const corners = [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]];
  for (const [x, y] of corners) {
    const i = (y * w + x) * 4;
    r += data[i]; g += data[i + 1]; b += data[i + 2];
  }
  r /= corners.length; g /= corners.length; b /= corners.length;

  const seen = new Uint8Array(w * h);
  const stack = [];
  const push = (p) => { if (!seen[p]) { seen[p] = 1; stack.push(p); } };
  for (let x = 0; x < w; x++) { push(x); push((h - 1) * w + x); }
  for (let y = 0; y < h; y++) { push(y * w); push(y * w + w - 1); }

  while (stack.length) {
    const p = stack.pop();
    const i = p * 4;
    if (Math.abs(data[i] - r) > tol
      || Math.abs(data[i + 1] - g) > tol
      || Math.abs(data[i + 2] - b) > tol) continue;
    data[i + 3] = 0;
    const x = p % w;
    const y = (p / w) | 0;
    if (x > 0) push(p - 1);
    if (x < w - 1) push(p + 1);
    if (y > 0) push(p - w);
    if (y < h - 1) push(p + w);
  }
}

function alphaBox(data, w, h) {
  let minX = w; let minY = h; let maxX = -1; let maxY = -1;
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let x = 0; x < w; x++) {
      if (data[(row + x) * 4 + 3] > 8) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < minX || maxY < minY) return null;
  return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
}

async function prepare(file) {
  if (!box) return;
  box.file = file;
  box.b64 = '';
  root.querySelector('#up-send').disabled = true;
  msg('处理中…');

  let img;
  try {
    img = await readImage(file);
  } catch (e) {
    msg(e.message || '这张图读不出来', true);
    return;
  }

  // 上限取「这个角色现有的包围盒」：新图绝不撑大窗口，
  // 也就不会让其他姿势跟着缩小。
  let maxW = 200;
  let maxH = 240;
  try {
    const sp = await loadRole(box.role);
    maxW = Math.max(60, Math.min(200, sp.w));
    maxH = Math.max(60, Math.min(240, sp.h));
  } catch (_) {
    // 角色还没加载出来就用上限兜底
  }

  const { c, ctx, w, h } = drawCapped(img);
  const frame = ctx.getImageData(0, 0, w, h);
  if (root.querySelector('#up-key').checked) keyOut(frame.data, w, h, TOL);
  ctx.putImageData(frame, 0, 0);

  const bb = alphaBox(frame.data, w, h);
  if (!bb) {
    msg('整张图都是透明的，换一张吧', true);
    return;
  }

  const k = Math.min(maxW / bb.w, maxH / bb.h, 1);
  const ow = Math.max(1, Math.round(bb.w * k));
  const oh = Math.max(1, Math.round(bb.h * k));
  const out = document.createElement('canvas');
  out.width = ow;
  out.height = oh;
  const octx = out.getContext('2d');
  octx.imageSmoothingQuality = 'high';
  octx.drawImage(c, bb.x, bb.y, bb.w, bb.h, 0, 0, ow, oh);

  box.b64 = out.toDataURL('image/png').split(',')[1];
  box.w = ow;
  box.h = oh;

  const scale = Math.min(3, 220 / Math.max(ow, oh));
  preview.width = ow;
  preview.height = oh;
  preview.style.width = `${Math.round(ow * scale)}px`;
  preview.style.height = `${Math.round(oh * scale)}px`;
  preview.getContext('2d').drawImage(out, 0, 0);

  root.querySelector('#up-send').disabled = false;
  msg('');
  note(`已裁成 ${ow}×${oh}，和其他姿势一样贴地。`);
}

async function submit() {
  if (!box || !box.b64) {
    msg('先选一张图', true);
    return;
  }
  const mode = box.mode;
  const payload = { role: box.role, data: box.b64 };
  if (mode) {
    payload.action = mode;
  } else {
    const label = root.querySelector('#up-label').value.trim();
    if (!label) {
      msg('给这个动作起个名字', true);
      return;
    }
    // 名字是「跟着做」的配对键：自己重名就不知道该演哪个，占内置名会有
    // 两个意思。服务端也会拒（唯一真源），这里先拦一道，省得等一个来回。
    const mine = state.me ? state.me.role : box.role;
    const want = normName(label);
    const acts = (state.meta && state.meta.actions) || {};
    const keys = Object.keys(acts);
    if (keys.some((k) => acts[k].custom && acts[k].role === mine
      && normName(acts[k].label) === want)) {
      msg(`你已经有「${label}」了，换个名字`, true);
      return;
    }
    if (keys.some((k) => !acts[k].custom && normName(acts[k].label) === want)) {
      msg(`「${label}」是内置动作的名字，换一个吧`, true);
      return;
    }
    payload.label = label;
    payload.dur = Number(root.querySelector('#up-dur').value) || 2;
  }

  const send = root.querySelector('#up-send');
  send.disabled = true;
  msg('上传中…');
  try {
    const res = await fetch('/api/sprites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      msg(body.error || `上传失败（${res.status}）`, true);
      send.disabled = false;
      return;
    }
    const done = box.onDone;
    closeUpload();
    if (done) done(body.manifest);
  } catch (e) {
    msg('传不上去，检查一下网络', true);
    send.disabled = false;
  }
}

// 撤销：自定义动作整个删掉，内置动作只删覆盖图（回到出厂那张）。
// 不做这一步的话，一张传错的图就再也收不回去了。
//
// 抽成可导出的函数，是因为头像菜单里也能删/还原 —— 那条路径不该为了
// 一个 POST 去把整个上传弹层打开。
export async function resetPose(role, action) {
  const res = await fetch('/api/sprites/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, action }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) {
    throw new Error(body.error || `操作失败（${res.status}）`);
  }
  return body.manifest;
}

async function resetAction() {
  if (!box || !box.mode) return;
  const rst = root.querySelector('#up-reset');
  rst.disabled = true;
  msg('处理中…');
  try {
    const manifest = await resetPose(box.role, box.mode);
    const done = box.onDone;
    closeUpload();
    if (done) done(manifest);
  } catch (e) {
    msg(e.message || '连不上服务器', true);
    rst.disabled = false;
  }
}
