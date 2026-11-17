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
import { factoryUrl, remoteAnim, spriteRev, spriteUrl } from './asset.js';
import { moveOffset } from './motion.js';
import { LOOSE_POSES, loosePose, normName } from './actions.js';

const CAP = 600;      // 分析用工作尺寸：够精细，又不是几百万像素
const TOL = 34;       // 去背容差（RGB 每通道）
const MAX_FRAMES = 16;  // 和服务端 custom.MAX_FRAMES 对齐

let box = null;       // { role, frames, previews, b64, w, h, onDone, files, mode }
let root = null;
let preview = null;
let previewTimer = null;
let previewIndex = 0;

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
            <input id="up-file" type="file" accept="image/*" multiple hidden>
            <span>选图（可多选）</span>
          </label>
          <label class="upload-key"><input id="up-key" type="checkbox" checked><span>去掉白色背景</span></label>
          <input id="up-add" type="file" accept="image/*" multiple hidden>
        </div>
        <div class="upload-stage"><canvas id="up-preview"></canvas></div>
        <div id="up-frames" class="upload-frames hidden"></div>
        <div class="upload-play-row">
          <label class="upload-auto"><input id="up-auto" type="checkbox" checked><span>自动播放</span></label>
          <label for="up-fps" class="upload-fps-lab">播放速度</label>
          <input id="up-fps" type="range" min="2" max="24" step="0.1" value="8">
          <span id="up-fps-val" class="upload-fps-val">8.0</span>
        </div>
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
        <div class="upload-move" id="up-move-box">
          <div class="upload-move-row">
            <label for="up-move-kind">移动</label>
            <select id="up-move-kind">
              <option value="">静止</option>
              <option value="line">横穿（飞出去再回来）</option>
              <option value="arc">弧线（腾空飞越）</option>
              <option value="orbit">环绕（绕一圈）</option>
              <option value="hover">原地悬停</option>
            </select>
          </div>
          <div class="upload-move-row" id="up-move-nums">
            <label for="up-move-h">高度</label>
            <input id="up-move-h" type="range" min="5" max="80" step="1" value="35">
            <span id="up-move-h-val" class="upload-move-val">35%</span>
            <label for="up-move-d">跨度</label>
            <input id="up-move-d" type="range" min="20" max="100" step="1" value="80">
            <span id="up-move-d-val" class="upload-move-val">80%</span>
          </div>
        </div>
        <div class="upload-alt">
          <p class="upload-note" id="up-note">图会自动裁边、缩放到和这个角色一样大。</p>
          <button type="button" class="btn btn-ghost hidden" data-cmd="reset" id="up-reset">恢复默认图</button>
        </div>
      </div>
      <div class="upload-foot">
        <span class="upload-msg" id="up-msg"></span>
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
    const files = e.target.files ? Array.from(e.target.files) : [];
    if (files.length) prepare(files);
    e.target.value = '';
  });
  root.querySelector('#up-add').addEventListener('change', (e) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    if (files.length) appendFrames(files);
    e.target.value = '';
  });
  root.querySelector('#up-key').addEventListener('change', () => {
    if (box && box.files && box.files.length) prepare(box.files);
  });
  root.querySelector('#up-auto').addEventListener('change', (e) => {
    if (!box) return;
    box.auto = !!e.target.checked;
    startPreview();
  });
  root.querySelector('#up-fps').addEventListener('input', (e) => {
    if (!box) return;
    box.fpsTouched = true;
    box.fps = Number(e.target.value) || 8;
    root.querySelector('#up-fps-val').textContent = box.fps.toFixed(1);
    startPreview();
  });
  root.querySelector('#up-move-kind').addEventListener('change', () => syncMove(true));
  root.querySelector('#up-move-h').addEventListener('input', () => syncMove(false));
  root.querySelector('#up-move-d').addEventListener('input', () => syncMove(false));
  root.querySelector('#up-poses').addEventListener('click', (e) => {
    const tile = e.target.closest && e.target.closest('.pose-tile');
    if (!tile) return;
    pickMode(tile.dataset.key || '');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && root && !root.classList.contains('hidden')) closeUpload();
  });
  return root;
}

// 选中一个动作格子（新建 / 某个已有动作）。菜单里的「换一张…」也走这里：
// 打开弹层时直接把目标动作选好，用户只需要选图。
function pickMode(key) {
  if (!box) return;
  if (key !== box.mode) box.fpsTouched = false;
  box.mode = key;
  root.querySelectorAll('.pose-tile').forEach((t) => {
    t.classList.toggle('on', (t.dataset.key || '') === key);
  });
  syncMode();
  if (!box.userPicked) loadExisting(box.mode);
}

export function openUpload(role, onDone, presetKey = '') {
  ensureDom();
  stopPreview();
  box = {
    role, frames: [], previews: [], b64: '', w: 0, h: 0, onDone,
    files: [], mode: '', fps: 8, auto: true, userPicked: false,
    fpsTouched: false, loadSeq: 0, selected: 0, move: null, previewT: 0,
  };
  root.querySelector('#up-auto').checked = true;
  root.querySelector('#up-fps').value = '8';
  root.querySelector('#up-fps-val').textContent = '8.0';
  setMoveControls(null);
  const strip = root.querySelector('#up-frames');
  if (strip) {
    strip.innerHTML = '';
    strip.classList.add('hidden');
  }
  const name = (document.querySelector('#up-title'));
  if (name) {
    name.textContent = presetKey
      ? `给「${roleName(role)}」换一张`
      : `给「${roleName(role)}」加个动作`;
  }

  renderPosePick(role);
  syncMode();
  root.querySelector('#up-label').value = '';
  root.querySelector('#up-dur').value = '2';
  root.querySelector('#up-send').disabled = true;
  root.querySelector('#up-reset').disabled = false;
  root.querySelector('#up-msg').textContent = '';
  note(root.querySelector('#up-key').checked
    ? '可以多选；多张会按设定帧率循环。图会自动裁边、缩放到和这个角色一样大。'
    : '可以多选；多张会按设定帧率循环。图会自动缩放到和这个角色一样大。');
  preview.getContext('2d').clearRect(0, 0, preview.width, preview.height);
  preview.width = 0;
  preview.height = 0;
  root.classList.remove('hidden');
  // 从菜单的「换一张…」进来：直接把目标动作选好（会异步预载它的帧和帧率）。
  if (presetKey) pickMode(presetKey);
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

  const spec = isNew ? null : specOf(key);
  const custom = isNew || !!(spec && spec.custom);
  // 自定义动作可以改时长；内置姿势的时长是语义的一部分，不给改。
  root.querySelector('#up-dur-row').classList.toggle('hidden', !custom);
  if (isNew) {
    root.querySelector('#up-dur').value = '2';
  } else if (spec && spec.custom) {
    root.querySelector('#up-dur').value = String(spec.dur || 2);
  }
  const pose = isNew ? '' : poseOf(key);
  const changed = !!(pose && spriteRev(box.role, pose));
  const rst = root.querySelector('#up-reset');
  // 没被换过的图没什么可还原的，按钮就别摆出来
  rst.classList.toggle('hidden', isNew || !changed);
  rst.textContent = (spec && spec.custom) ? '删掉这个动作' : '恢复默认图';

  root.querySelector('#up-send').textContent = isNew ? '上传' : '换这张';
  // 移动轨迹只给自定义动作：内置姿势的时长和语义是写死的。
  root.querySelector('#up-move-box').classList.toggle('hidden', !custom);
}

export function closeUpload() {
  stopPreview();
  if (root) root.classList.add('hidden');
  box = null;
}

function stopPreview() {
  clearInterval(previewTimer);
  previewTimer = null;
  previewIndex = 0;
  if (preview) preview.style.transform = '';
}

// ---------------- 移动轨迹 ----------------
// 轨迹只属于自定义动作。数值是比例，服务端只做白名单校验。

function readMove() {
  if (!root) return null;
  const kind = root.querySelector('#up-move-kind').value;
  if (!kind) return null;
  return {
    kind,
    dir: 'lr',
    height: Number(root.querySelector('#up-move-h').value) / 100,
    distance: Number(root.querySelector('#up-move-d').value) / 100,
  };
}

function syncMove(restart) {
  if (!box) return;
  box.move = readMove();
  const kind = box.move ? box.move.kind : '';
  root.querySelector('#up-move-h').disabled = !kind;
  root.querySelector('#up-move-d').disabled = !kind || kind === 'hover';
  root.querySelector('#up-move-h-val').textContent
    = `${root.querySelector('#up-move-h').value}%`;
  root.querySelector('#up-move-d-val').textContent
    = `${root.querySelector('#up-move-d').value}%`;
  if (restart) startPreview();
}

function setMoveControls(mv) {
  if (!root) return;
  const kind = (mv && mv.kind) || '';
  const h = Math.round(((mv && mv.height) || 0.35) * 100);
  const d = Math.round(((mv && mv.distance) || 0.8) * 100);
  root.querySelector('#up-move-kind').value = kind;
  root.querySelector('#up-move-h').value = String(Math.min(80, Math.max(5, h)));
  root.querySelector('#up-move-d').value = String(Math.min(100, Math.max(20, d)));
  syncMove(true);
}

function drawPreviewIndex(index) {
  if (!box || !box.previews || !box.previews.length) return;
  const c = box.previews[index % box.previews.length];
  const scale = Math.min(3, 160 / Math.max(c.width, c.height));
  preview.width = c.width;
  preview.height = c.height;
  preview.style.width = `${Math.round(c.width * scale)}px`;
  preview.style.height = `${Math.round(c.height * scale)}px`;
  preview.getContext('2d').drawImage(c, 0, 0);
}

// 多帧时在画布下方排一排缩略帧：一眼能看出选了几张、分别是哪几张。
// 只剩一张也照常显示（用户要能确认留下的是哪张），最右边固定一个「＋」
// 用来继续加图；缩略图可点选、可拖动换顺序。
let dragIndex = null;

function renderFrameStrip() {
  const strip = root.querySelector('#up-frames');
  if (!strip) return;
  strip.innerHTML = '';
  if (!box || !box.frames.length) {
    strip.classList.add('hidden');
    return;
  }
  box.frames.forEach((b64, i) => {
    const cell = document.createElement('span');
    cell.className = 'upload-frame' + (i === box.selected ? ' sel' : '');
    cell.draggable = true;
    cell.title = '单击预览，拖动排序';
    cell.addEventListener('click', () => selectFrame(i));
    cell.addEventListener('dragstart', (e) => {
      dragIndex = i;
      cell.classList.add('drag');
      try {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(i));
      } catch (_) { /* 老浏览器不给设置就算了 */ }
    });
    cell.addEventListener('dragend', () => {
      dragIndex = null;
      cell.classList.remove('drag');
    });
    cell.addEventListener('dragover', (e) => {
      if (dragIndex === null) return;
      e.preventDefault();
      cell.classList.add('over');
    });
    cell.addEventListener('dragleave', () => cell.classList.remove('over'));
    cell.addEventListener('drop', (e) => {
      e.preventDefault();
      cell.classList.remove('over');
      const from = dragIndex;
      dragIndex = null;
      if (from !== null) moveFrame(from, i);
    });
    const im = document.createElement('img');
    im.src = `data:image/png;base64,${b64}`;
    im.alt = `第 ${i + 1} 帧`;
    im.draggable = false;
    const no = document.createElement('i');
    no.textContent = String(i + 1);
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'upload-frame-x';
    x.textContent = '×';
    x.title = `删掉第 ${i + 1} 帧`;
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      removeFrame(i);
    });
    cell.append(im, no, x);
    strip.appendChild(cell);
  });
  // 加图：一个虚线框里挂个加号，和缩略图同一排、在最右侧。
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'upload-frame-add';
  add.title = '再加几张图';
  add.setAttribute('aria-label', '再加几张图');
  add.textContent = '＋';
  add.addEventListener('click', () => root.querySelector('#up-add').click());
  strip.appendChild(add);
  strip.classList.remove('hidden');
}

function selectFrame(index) {
  if (!box || index < 0 || index >= box.frames.length) return;
  box.selected = index;
  const cells = root.querySelectorAll('.upload-frame');
  cells.forEach((c, i) => c.classList.toggle('sel', i === index));
  // 自动播放时由定时器轮播；关掉后画布就停在这张上。
  if (!box.auto) drawPreviewIndex(index);
}

function moveFrame(from, to) {
  if (!box || from === to || from < 0 || to < 0
    || from >= box.frames.length || to >= box.frames.length) return;
  box.userPicked = true;
  const [f] = box.frames.splice(from, 1);
  box.frames.splice(to, 0, f);
  const [p] = box.previews.splice(from, 1);
  box.previews.splice(to, 0, p);
  box.b64 = box.frames[0];
  box.selected = to;
  renderFrameStrip();
  startPreview();
}

function removeFrame(index) {
  if (!box || index < 0 || index >= box.frames.length) return;
  box.userPicked = true;
  box.frames.splice(index, 1);
  box.previews.splice(index, 1);
  box.b64 = box.frames[0] || '';
  if (box.previews.length) {
    box.w = box.previews[0].width;
    box.h = box.previews[0].height;
  }
  if (box.selected > index) box.selected -= 1;
  box.selected = Math.max(0, Math.min(box.selected, box.frames.length - 1));
  renderFrameStrip();
  startPreview();
  root.querySelector('#up-send').disabled = !box.frames.length;
  if (!box.frames.length) {
    preview.width = 0;
    preview.height = 0;
    msg('至少留一帧', true);
  } else {
    msg('');
    note(`还剩 ${box.frames.length} 帧。`);
  }
}

function startPreview() {
  stopPreview();
  if (!box || !box.previews.length) return;
  const n = box.previews.length;
  box.selected = Math.max(0, Math.min(box.selected || 0, n - 1));
  previewIndex = box.selected;
  box.previewT = 0;
  drawPreviewIndex(previewIndex);
  if (!box.auto) return;
  // 只有一帧但带轨迹时也要跑：静态姿势也能飞。
  if (n < 2 && !(box.move && box.move.kind)) return;
  const ms = Math.max(40, 1000 / (box.fps || 8));
  previewTimer = setInterval(() => tickPreview(ms), ms);
}

function tickPreview(ms) {
  if (!box || !box.previews.length) return;
  if (box.previews.length > 1) {
    previewIndex = (previewIndex + 1) % box.previews.length;
    drawPreviewIndex(previewIndex);
  }
  const mv = box.move;
  if (!mv || !mv.kind) {
    preview.style.transform = '';
    return;
  }
  // 预览台就是一块缩小的屏幕：轨迹跑 2 秒一轮，结束正好回到原点。
  box.previewT += ms / 1000;
  const t = (box.previewT / 2) % 1;
  const stage = root.querySelector('.upload-stage');
  const w = Math.max(40, (stage.clientWidth || 300) - 16);
  const h = Math.max(40, (stage.clientHeight || 120) - 16);
  const [dx, dy] = moveOffset(mv, t, w, h, w / 2, w / 2, h * 0.55, h * 0.45);
  preview.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
}

function imageCanvas(img) {
  const c = document.createElement('canvas');
  c.width = img.naturalWidth || img.width;
  c.height = img.naturalHeight || img.height;
  c.getContext('2d').drawImage(img, 0, 0);
  return c;
}

function resetFrames() {
  if (!box) return;
  box.frames = [];
  box.previews = [];
  box.b64 = '';
  box.w = 0;
  box.h = 0;
  box.selected = 0;
  setMoveControls(null);
  stopPreview();
  renderFrameStrip();
  preview.width = 0;
  preview.height = 0;
  root.querySelector('#up-send').disabled = true;
}

// 编辑已有动作：把当前动画直接装进预览，用户可以删帧、改帧率、再换图。
async function loadExisting(mode) {
  if (!box) return;
  if (!mode) {
    if (!box.userPicked) resetFrames();
    return;
  }
  const spec = ((state.meta && state.meta.actions) || {})[mode];
  if (!spec || !spec.custom) {
    if (!box.userPicked) resetFrames();
    return;
  }
  if (box.userPicked) return;
  const seq = ++box.loadSeq;
  try {
    const sp = await loadRole(box.role);
    // 等图这段时间用户可能已经拖了滑条 / 换了图 / 点了别的动作：
    // 迟到的结果不能再把播放速度改回服务端那份旧值。
    if (!box || seq !== box.loadSeq || box.mode !== mode) return;
    if (!box.userPicked) {
      let imgs = (sp.frames && sp.frames[mode]) || [];
      if (!imgs.length && sp.poses[mode]) imgs = [sp.poses[mode]];
      if (!imgs.length) return;
      box.previews = imgs.map(imageCanvas);
      box.frames = box.previews.map((c) => c.toDataURL('image/png').split(',')[1]);
      box.b64 = box.frames[0];
      box.w = box.previews[0].width;
      box.h = box.previews[0].height;
      box.selected = 0;
    }
    if (!box.fpsTouched) {
      // 清单里的 anims 是上传成功后立刻写进来的那一份，最不容易读到旧值；
      // loadRole 的缓存和 /api/meta 都可能比它晚一拍。
      const anim = remoteAnim(box.role, poseOf(mode));
      box.fps = Number((anim && anim.fps)
        || (sp.anims && sp.anims[mode] && sp.anims[mode].fps)
        || spec.fps || 8);
      root.querySelector('#up-fps').value = String(box.fps);
      root.querySelector('#up-fps-val').textContent = box.fps.toFixed(1);
    }
    setMoveControls(spec.move);
    renderFrameStrip();
    startPreview();
    root.querySelector('#up-send').disabled = !box.frames.length;
    msg('');
    note(`当前动作：${box.frames.length} 帧 · ${box.fps.toFixed(1)}fps。可以删帧或重新选图。`);
  } catch (_) {
    // 本地还没缓存这个动作的帧：保持原样，用户仍可重新选图
  }
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

// 「＋」继续加图：输出和现有帧同一个画布尺寸、脚底对齐，服务端的
// 「所有帧同尺寸」校验能过，播放时人也不会上下跳。
async function appendFrames(files) {
  if (!box) return;
  const room = MAX_FRAMES - box.frames.length;
  if (room <= 0) {
    msg(`最多 ${MAX_FRAMES} 帧`, true);
    return;
  }
  const list = Array.from(files || []).slice(0, room);
  if (!list.length) return;
  if (!box.frames.length || !box.w || !box.h) {
    prepare(list);
    return;
  }
  box.userPicked = true;
  msg('处理中…');
  const w = box.w;
  const h = box.h;
  const keyout = root.querySelector('#up-key').checked;
  const added = [];
  for (let i = 0; i < list.length; i += 1) {
    let img;
    try {
      img = await readImage(list[i]);
    } catch (e) {
      msg(list.length > 1 ? `第 ${i + 1} 张图读不出来` : (e.message || '这张图读不出来'), true);
      continue;
    }
    const { c, ctx, w: iw, h: ih } = drawCapped(img);
    const frame = ctx.getImageData(0, 0, iw, ih);
    if (keyout) keyOut(frame.data, iw, ih, TOL);
    ctx.putImageData(frame, 0, 0);
    const bb = alphaBox(frame.data, iw, ih);
    if (!bb) continue;
    const k = Math.min(w / bb.w, h / bb.h, 1);
    const dw = Math.max(1, Math.round(bb.w * k));
    const dh = Math.max(1, Math.round(bb.h * k));
    const out = document.createElement('canvas');
    out.width = w;
    out.height = h;
    const octx = out.getContext('2d');
    octx.imageSmoothingQuality = 'high';
    octx.drawImage(c, bb.x, bb.y, bb.w, bb.h,
      Math.round((w - dw) / 2), h - dh, dw, dh);
    box.previews.push(out);
    box.frames.push(out.toDataURL('image/png').split(',')[1]);
    added.push(box.frames.length - 1);
  }
  if (!added.length) {
    msg('新加的图都是透明的，换几张吧', true);
    return;
  }
  box.selected = added[0];
  renderFrameStrip();
  startPreview();
  root.querySelector('#up-send').disabled = false;
  msg('');
  note(`已加到 ${box.frames.length} 帧。`);
}

async function prepare(files) {
  if (!box) return;
  const list = Array.from(files || []).slice(0, MAX_FRAMES);
  if (!list.length) return;
  box.files = list;
  box.frames = [];
  box.previews = [];
  box.b64 = '';
  box.userPicked = true;
  stopPreview();
  renderFrameStrip();
  preview.width = 0;
  preview.height = 0;
  root.querySelector('#up-send').disabled = true;
  msg('处理中…');

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

  // 每帧先各自去背，再算所有帧的透明边界并集：同一个裁剪框 + 同一个缩放，
  // 输出画布才会完全一致，播放时人不会一帧一跳。
  const items = [];
  for (let i = 0; i < list.length; i += 1) {
    let img;
    try {
      img = await readImage(list[i]);
    } catch (e) {
      msg(list.length > 1 ? `第 ${i + 1} 张图读不出来` : (e.message || '这张图读不出来'), true);
      return;
    }
    const { c, ctx, w, h } = drawCapped(img);
    const frame = ctx.getImageData(0, 0, w, h);
    if (root.querySelector('#up-key').checked) keyOut(frame.data, w, h, TOL);
    ctx.putImageData(frame, 0, 0);
    const bb = alphaBox(frame.data, w, h);
    if (!bb) {
      msg(list.length > 1 ? `第 ${i + 1} 帧整张都是透明的` : '整张图都是透明的，换一张吧', true);
      return;
    }
    items.push({ c, bb });
  }

  const u = { x: Infinity, y: Infinity, x2: -1, y2: -1 };
  for (const it of items) {
    u.x = Math.min(u.x, it.bb.x);
    u.y = Math.min(u.y, it.bb.y);
    u.x2 = Math.max(u.x2, it.bb.x + it.bb.w);
    u.y2 = Math.max(u.y2, it.bb.y + it.bb.h);
  }
  const bw = Math.max(1, u.x2 - u.x);
  const bh = Math.max(1, u.y2 - u.y);
  const k = Math.min(maxW / bw, maxH / bh, 1);
  const ow = Math.max(1, Math.round(bw * k));
  const oh = Math.max(1, Math.round(bh * k));

  const outs = [];
  const frames = [];
  for (const it of items) {
    const out = document.createElement('canvas');
    out.width = ow;
    out.height = oh;
    const octx = out.getContext('2d');
    octx.imageSmoothingQuality = 'high';
    octx.drawImage(it.c, u.x, u.y, bw, bh, 0, 0, ow, oh);
    outs.push(out);
    frames.push(out.toDataURL('image/png').split(',')[1]);
  }

  box.frames = frames;
  box.previews = outs;
  box.b64 = frames[0];
  box.w = ow;
  box.h = oh;
  box.selected = 0;

  renderFrameStrip();
  startPreview();

  root.querySelector('#up-send').disabled = false;
  msg('');
  note(frames.length > 1
    ? `已裁成 ${ow}×${oh}，共 ${frames.length} 帧。`
    : `已裁成 ${ow}×${oh}，和其他姿势一样贴地。`);
}

async function submit() {
  if (!box || !box.frames || !box.frames.length) {
    msg('先选一张图', true);
    return;
  }
  const mode = box.mode;
  const acts = (state.meta && state.meta.actions) || {};
  if (mode && box.frames.length > 1) {
    const spec = acts[mode];
    if (!(spec && spec.custom)) {
      msg('内置姿势只能放一张图', true);
      return;
    }
  }
  // 以滑条当前显示值提交：用户拖动后 box.fps 和界面必须一致，
  // 不能因为某次异步回填把速度改回旧值。
  const shownFps = Number(root.querySelector('#up-fps').value);
  const payload = {
    role: box.role,
    data: box.frames[0],
    frames: box.frames,
    fps: shownFps > 0 ? shownFps : box.fps,
  };
  // 时长和轨迹只属于自定义动作；内置姿势不带这些字段，服务端不会动它的配置。
  if (!mode || (acts[mode] && acts[mode].custom)) {
    payload.dur = Number(root.querySelector('#up-dur').value) || 2;
    payload.move = box.move || null;
  }
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
