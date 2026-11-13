// 全屏聊天：左侧会话栏 + 右侧消息流 + 输入卡片 + 快捷互动。
// 结构和桌面版 chat.py 一致；宽度是全屏的，所以快捷互动在宽屏上是一行。
import {
  state, CONV_ALL, convKeys, nameOf, roleName, getLog, appendEntry,
  clearUnread, unreadTotal, onlineCount, isOnline, formatTime,
  escapeHtml,
} from './state.js';
import { avatarFor } from './sprites.js';
import { assetUrl } from './asset.js';
import { saveHistory } from './history.js';

// 和 petclub/media.py 保持同一份表情字符 / 表情包姿势。
const EMOJI_CHARS = ['😀', '😄', '😅', '😂', '😊', '😉', '😍', '😘',
  '😜', '😳', '😭', '😡', '😱', '😴', '🙄', '👍', '👏', '🙏'];
const STICKER_POSES = ['idle', 'wave', 'drink', 'heart', 'jump', 'sleep', 'knock', 'drag'];

export class ChatUI {
  constructor({ onSend, onAction, onLeave, onQuit, onRemote, onMedia, onPetSize, onGame, onFx, onToy, onReminder, reminderState }) {
    this.onSend = onSend;
    this.onAction = onAction;
    this.onLeave = onLeave;
    this.onQuit = onQuit;
    this.onRemote = onRemote;
    this.onMedia = onMedia;
    this.onPetSize = onPetSize;
    this.onGame = onGame;
    this.onFx = onFx;
    this.onToy = onToy;
    this.onReminder = onReminder;
    this.reminderState = reminderState || (() => ({ on: false, min: 45 }));

    this.root = document.getElementById('main-screen');
    this.list = document.getElementById('conv-list');
    this.messagesEl = document.getElementById('messages');
    this.input = document.getElementById('composer-input');
    this.sendBtn = document.getElementById('send-btn');
    this.quick = document.getElementById('quick-actions');
    this.favicon = document.getElementById('favicon');
    this.convTitle = document.getElementById('conv-title');
    this.convSub = document.getElementById('conv-sub');
    this.convState = document.getElementById('conv-state');
    this.onlinePill = document.getElementById('online-pill');
    this.targetLabel = document.getElementById('target-label');
    this.noteEl = document.getElementById('chat-note');
    this.searchInput = document.getElementById('conv-search');
    this.meBar = document.getElementById('me-bar');
    this.meAvatar = document.getElementById('me-avatar');
    this.meName = document.getElementById('me-name');
    this.meState = document.getElementById('me-state');
    this.offlineBanner = document.getElementById('offline-banner');
    this.emojiBtn = document.getElementById('emoji-btn');
    this.fileBtn = document.getElementById('file-btn');
    this.mentionBtn = document.getElementById('mention-btn');
    this.diceBtn = document.getElementById('dice-btn');
    this.quoteStrip = document.getElementById('quote-strip');
    this.mediaPicker = document.getElementById('media-picker');
    this.fileInput = document.getElementById('file-input');
    this.attachStrip = document.getElementById('attach-strip');
    this.pickerPreview = document.getElementById('picker-preview');
    this.imageViewer = document.getElementById('image-viewer');
    this.imageViewerImg = document.getElementById('image-viewer-img');

    this.attachments = [];
    this.search = '';
    this.quote = null;
    this._mentionRe = null;
    this.avatars = new Map();
    this.meMenu = null;
    this.mentionMenu = null;
    this.toyMenu = null;
    this.noteTimer = null;
    this.forceBottom = true;
    this.pickerTab = 'emoji';
  }

  init() {
    // 快捷互动：和桌面聊天窗同一份 CHAT_ORDER（不含「传个话…」）
    this.quick.innerHTML = '';
    for (const key of state.meta.chat_order) {
      const spec = state.meta.actions[key];
      if (!spec) continue;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = spec.label;
      b.title = spec.hint || '';
      b.addEventListener('click', () => this.onAction(key, this.currentTarget()));
      this.quick.appendChild(b);
    }
    const toy = document.createElement('button');
    toy.type = 'button';
    toy.className = 'chip';
    toy.textContent = '逗一逗';
    toy.title = '让它跟着鼠标跑 8 秒';
    toy.addEventListener('click', () => this.triggerToy(toy));
    this.quick.appendChild(toy);

    const fx = document.createElement('button');
    fx.type = 'button';
    fx.className = 'chip';
    fx.textContent = '烟花';
    fx.title = '放一场烟花';
    fx.addEventListener('click', () => {
      if (this.onFx) this.onFx(this.currentTarget());
    });
    this.quick.appendChild(fx);

    this.sendBtn.addEventListener('click', () => this.submit());
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        this.submit();
      }
    });
    this.input.addEventListener('input', (e) => {
      this.resizeInput();
      // 手输 @ 也弹快捷选人，和桌面版一致
      if (e.data === '@' || e.data === '＠') {
        this.toggleMentionMenu();
      } else if (e.data && this.mentionMenu) {
        this.closeMentionMenu();
      }
    });

    this.searchInput.addEventListener('input', () => {
      this.search = this.searchInput.value.trim().toLowerCase();
      this.renderConversations();
      this.renderMessages();
    });
    this.mentionBtn.addEventListener('click', () => this.toggleMentionMenu());
    this.diceBtn.addEventListener('click', () => {
      if (this.onGame) this.onGame('dice', this.currentTarget());
    });

    this.meBar.addEventListener('click', () => this.toggleMeMenu());
    document.addEventListener('pointerdown', (e) => {
      if (this.meMenu) {
        if (!this.meMenu.contains(e.target) && !this.meBar.contains(e.target)) {
          this.closeMeMenu();
        }
      }
      if (!this.mediaPicker.classList.contains('hidden')
          && !this.mediaPicker.contains(e.target)
          && !this.emojiBtn.contains(e.target)) {
        this.closePicker();
      }
      if (this.mentionMenu && !this.mentionMenu.contains(e.target)
          && !this.mentionBtn.contains(e.target)) {
        this.closeMentionMenu();
      }
      if (this.toyMenu && !this.toyMenu.contains(e.target)) {
        this.closeToyMenu();
      }
    }, true);
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      this.closePicker();
      this.closeImageViewer();
      this.closeMeMenu();
      this.closeMentionMenu();
      this.closeToyMenu();
    });

    this.emojiBtn.addEventListener('click', () => this.togglePicker());
    this.fileBtn.addEventListener('click', () => this.fileInput.click());
    this.fileInput.addEventListener('change', () => {
      const files = Array.from(this.fileInput.files || []);
      this.fileInput.value = '';
      if (files.length) this.addFiles(files);
    });
    // 和桌面版一样：在聊天页里 Ctrl+V 粘贴截图/图片，先落成待发附件。
    document.addEventListener('paste', (e) => this.handlePaste(e));
    this.imageViewer.addEventListener('click', () => this.closeImageViewer());
    this.resizeInput();
    this.renderAll();
  }

  // ---------------- 待发附件（粘贴 / 选文件） ----------------

  handlePaste(e) {
    if (state.claimVisible || !state.me) return;
    const cd = e.clipboardData;
    if (!cd) return;
    const files = [];
    if (cd.items) {
      for (const item of cd.items) {
        if (item.kind !== 'file') continue;
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (!files.length && cd.files) {
      for (const f of cd.files) files.push(f);
    }
    if (!files.length) return;      // 纯文字：让浏览器按正常粘贴处理
    e.preventDefault();
    this.addFiles(files);
  }

  addFiles(files) {
    const room = 3 - this.attachments.length;
    if (room <= 0) {
      this.note('一次最多带 3 个附件');
      return;
    }
    const take = files.slice(0, room);
    for (const file of take) {
      const isImage = !!(file.type && file.type.startsWith('image/'));
      const url = isImage ? URL.createObjectURL(file) : '';
      const name = file.name || (isImage ? '粘贴的图片.png' : '文件');
      this.attachments.push({ file, url, isImage, name });
    }
    if (files.length > room) this.note('一次最多带 3 个附件');
    this.renderAttachments();
    this.focusInput();
  }

  removeAttachment(index) {
    const [item] = this.attachments.splice(index, 1);
    if (item && item.url) URL.revokeObjectURL(item.url);
    this.renderAttachments();
  }

  clearAttachments() {
    for (const item of this.attachments) {
      if (item.url) URL.revokeObjectURL(item.url);
    }
    this.attachments = [];
    this.renderAttachments();
  }

  renderAttachments() {
    if (!this.attachStrip) return;
    this.attachStrip.innerHTML = '';
    this.attachStrip.classList.toggle('hidden', !this.attachments.length);
    this.attachments.forEach((item, index) => {
      const chip = document.createElement('div');
      chip.className = 'attach-chip';
      if (item.url) {
        const img = document.createElement('img');
        img.src = item.url;
        img.alt = item.name;
        chip.appendChild(img);
      } else {
        const tag = document.createElement('span');
        tag.className = 'attach-file';
        tag.textContent = 'FILE';
        chip.appendChild(tag);
      }
      const name = document.createElement('span');
      name.className = 'attach-name';
      name.textContent = item.name;
      name.title = item.name;
      chip.appendChild(name);
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'attach-remove';
      rm.textContent = '×';
      rm.title = '移除';
      rm.addEventListener('click', () => this.removeAttachment(index));
      chip.appendChild(rm);
      this.attachStrip.appendChild(chip);
    });
  }

  // ---------------- 表情 / 表情包 / 文件 ----------------

  closePicker() {
    this.mediaPicker.classList.add('hidden');
    this.mediaPicker.innerHTML = '';
    this.hidePickerPreview();
  }

  togglePicker() {
    if (!this.mediaPicker.classList.contains('hidden')) {
      this.closePicker();
      return;
    }
    this.renderPicker();
    this.mediaPicker.classList.remove('hidden');
  }

  renderPicker() {
    const tabs = [['emoji', '表情'], ['mine', '我的'], ['others', '他人']]
      .map(([key, label]) => (
        `<button type="button" class="picker-tab${this.pickerTab === key ? ' active' : ''}" data-tab="${key}">${label}</button>`
      )).join('');

    let cells = '';
    if (this.pickerTab === 'emoji') {
      cells = EMOJI_CHARS.map((ch) => (
        `<button type="button" class="picker-cell emoji" data-emoji="${escapeHtml(ch)}">${escapeHtml(ch)}</button>`
      )).join('');
    } else {
      const me = state.me ? state.me.role : null;
      let roles;
      if (this.pickerTab === 'mine') {
        roles = me ? [me] : [];
      } else {
        roles = state.roles.filter((r) => r !== me);
      }
      cells = roles.flatMap((role) => STICKER_POSES.map((pose) => {
        const url = `/assets/stickers/${role}/${pose}.png`;
        const label = `${roleName(role)} · ${pose}`;
        // 只给显示的 src 挂 build 号；data-sticker 要原样发给服务端，不能带查询串
        return `<button type="button" class="picker-cell" data-sticker="${escapeHtml(url)}" title="${escapeHtml(label)}"><img src="${escapeHtml(assetUrl(url))}" alt="${escapeHtml(label)}" loading="lazy"></button>`;
      })).join('');
    }

    this.mediaPicker.innerHTML = `
      <div class="picker-tabs">${tabs}<span class="picker-hint">点一下直接发送</span></div>
      <div class="picker-grid">${cells || '<div class="picker-hint">没有可发送的表情包</div>'}</div>`;

    this.mediaPicker.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.pickerTab = btn.dataset.tab;
        this.renderPicker();
      });
    });
    this.mediaPicker.querySelectorAll('[data-emoji]').forEach((btn) => {
      btn.addEventListener('click', () => this.pickEmoji(btn.dataset.emoji));
      btn.addEventListener('mouseenter', () => {
        this.showPickerPreview(btn, 'emoji', btn.dataset.emoji);
      });
      btn.addEventListener('mouseleave', () => this.hidePickerPreview());
    });
    this.mediaPicker.querySelectorAll('[data-sticker]').forEach((btn) => {
      btn.addEventListener('click', () => this.pickSticker(btn.dataset.sticker));
      btn.addEventListener('mouseenter', () => {
        this.showPickerPreview(btn, 'sticker', btn.dataset.sticker);
      });
      btn.addEventListener('mouseleave', () => this.hidePickerPreview());
    });
  }

  showPickerPreview(anchor, kind, value) {
    if (!this.pickerPreview) return;
    this.pickerPreview.innerHTML = '';
    this.pickerPreview.className = 'picker-preview'
      + (kind === 'emoji' ? ' emoji' : '');
    if (kind === 'emoji') {
      this.pickerPreview.textContent = value;
    } else {
      const img = document.createElement('img');
      img.src = value;
      img.alt = '表情包预览';
      this.pickerPreview.appendChild(img);
    }
    this.pickerPreview.classList.remove('hidden');
    const r = anchor.getBoundingClientRect();
    const br = this.pickerPreview.getBoundingClientRect();
    let left = Math.min(Math.max(8, r.left), window.innerWidth - br.width - 8);
    let top = r.top - br.height - 8;
    if (top < 8) top = r.bottom + 8;
    if (top + br.height > window.innerHeight - 8) {
      top = Math.max(8, window.innerHeight - br.height - 8);
    }
    this.pickerPreview.style.left = `${left}px`;
    this.pickerPreview.style.top = `${top}px`;
  }

  hidePickerPreview() {
    if (!this.pickerPreview) return;
    this.pickerPreview.classList.add('hidden');
    this.pickerPreview.innerHTML = '';
  }

  pickEmoji(ch) {
    if (!this.onMedia) return;
    this.closePicker();
    this.onMedia('emoji', ch).then((why) => {
      if (why) this.note(why);
    }).catch((e) => this.note(String(e.message || e)));
  }

  pickSticker(url) {
    if (!this.onMedia) return;
    this.closePicker();
    this.onMedia('sticker', url).then((why) => {
      if (why) this.note(why);
    }).catch((e) => this.note(String(e.message || e)));
  }

  openImageViewer(src) {
    this.imageViewerImg.src = src;
    this.imageViewer.classList.remove('hidden');
  }

  closeImageViewer() {
    this.imageViewer.classList.add('hidden');
    this.imageViewerImg.removeAttribute('src');
  }

  currentTarget() {
    return state.currentKey === CONV_ALL ? null : state.currentKey;
  }

  triggerToy(btn) {
    const to = this.currentTarget();
    if (to) {
      if (!isOnline(to)) {
        this.note(`「${roleName(to)}」不在线，逗不了`);
        return;
      }
      if (this.onToy) this.onToy(to);
      return;
    }
    this.showToyMenu(btn);
  }

  showToyMenu(btn) {
    this.closeToyMenu();
    const roles = state.roles.filter(
      (r) => r !== (state.me ? state.me.role : null) && isOnline(r),
    );
    if (!roles.length) {
      this.note('现在没有别人在线，逗不了');
      return;
    }
    const menu = document.createElement('div');
    menu.className = 'mention-menu';
    for (const r of roles) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'mention-item';
      b.textContent = `逗 ${roleName(r)}`;
      b.addEventListener('click', () => {
        this.closeToyMenu();
        if (this.onToy) this.onToy(r);
      });
      menu.appendChild(b);
    }
    const rect = btn.getBoundingClientRect();
    menu.style.left = `${Math.max(8, rect.left)}px`;
    menu.style.bottom = `${window.innerHeight - rect.top + 6}px`;
    document.body.appendChild(menu);
    this.toyMenu = menu;
  }

  closeToyMenu() {
    if (!this.toyMenu) return;
    this.toyMenu.remove();
    this.toyMenu = null;
  }

  showMain() {
    this.root.classList.remove('hidden');
    this.renderAll();
  }

  hideMain() {
    this.closeMeMenu();
    this.closePicker();
    this.closeImageViewer();
    this.closeMentionMenu();
    this.closeToyMenu();
    this.clearAttachments();
    this.root.classList.add('hidden');
  }

  resetForClaim() {
    this.closeMeMenu();
    this.closePicker();
    this.closeImageViewer();
    this.clearAttachments();
    this.clearQuote();
    this.closeMentionMenu();
    this.closeToyMenu();
    this.search = '';
    this.searchInput.value = '';
    this.input.value = '';
    this.resizeInput();
    this.note('');
    // 保留已加载的本地历史；只清当前会话和未读
    state.unread = {};
    state.currentKey = CONV_ALL;
    this.renderAll();
  }

  // ---------------- 会话栏 ----------------

  renderAll() {
    if (!convKeys().includes(state.currentKey)) state.currentKey = CONV_ALL;
    this.renderConversations();
    this.renderMessages();
    this.updateHeader();
    this.updateMe();
    this.updateTitle();
  }

  renderConversations() {
    this.list.innerHTML = '';
    const q = this.search;
    const keys = convKeys();
    let shown = 0;

    for (const key of keys) {
      const log = getLog(key);
      const name = nameOf(key);
      const last = log[log.length - 1];
      if (q && !name.toLowerCase().includes(q)
        && !log.some((e) => (e.text || '').toLowerCase().includes(q))) {
        continue;
      }
      shown++;

      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'conv-row' + (key === state.currentKey ? ' selected' : '');

      const av = document.createElement('span');
      av.className = 'avatar' + (key === CONV_ALL ? ' group' : '');
      if (key === CONV_ALL) {
        av.textContent = '群';
      } else {
        av.textContent = (roleName(key) || '?').slice(0, 1);
        this.fillAvatar(av, key);
      }
      row.appendChild(av);

      const main = document.createElement('span');
      main.className = 'conv-main';
      const line = document.createElement('span');
      line.className = 'conv-line';
      const nm = document.createElement('span');
      nm.className = 'conv-name';
      nm.textContent = name;
      const tm = document.createElement('span');
      tm.className = 'conv-time';
      tm.textContent = last ? formatTime(last.ts) : '';
      line.append(nm, tm);

      const pv = document.createElement('span');
      pv.className = 'conv-preview';
      pv.textContent = previewOf(key, last);
      main.append(line, pv);
      row.appendChild(main);

      const n = state.unread[key] || 0;
      if (n) {
        const badge = document.createElement('span');
        badge.className = 'unread-badge';
        badge.textContent = n > 99 ? '99+' : String(n);
        row.appendChild(badge);
      }

      row.addEventListener('click', () => this.switch(key));
      this.list.appendChild(row);
    }

    if (!shown) {
      const empty = document.createElement('div');
      empty.className = 'conv-empty';
      empty.textContent = q ? '没有匹配的会话' : '还没有可聊的人';
      this.list.appendChild(empty);
    }
  }

  fillAvatar(el, role) {
    if (!role) return;
    const cached = this.avatars.get(role);
    if (cached) {
      el.innerHTML = '';
      const img = document.createElement('img');
      img.src = cached;
      img.alt = roleName(role);
      el.appendChild(img);
      return;
    }
    avatarFor(role, 64).then((url) => {
      if (!url) return;
      this.avatars.set(role, url);
      if (!el.isConnected) return;
      el.innerHTML = '';
      const img = document.createElement('img');
      img.src = url;
      img.alt = roleName(role);
      el.appendChild(img);
    }).catch(() => {});
  }

  switch(key, { focus = true } = {}) {
    state.currentKey = key;
    clearUnread(key);
    this.clearQuote();
    this.forceBottom = true;
    this.renderConversations();
    this.renderMessages();
    this.updateHeader();
    this.updateTitle();
    if (focus) this.focusInput();
  }

  // ---------------- 消息区 ----------------

  renderMessages() {
    const key = state.currentKey;
    let log = getLog(key);
    const nearBottom = this.isNearBottom();
    this.messagesEl.innerHTML = '';

    const q = this.search;
    if (q) {
      log = log.filter((e) => (e.text || '').toLowerCase().includes(q));
      const hint = document.createElement('div');
      hint.className = 'search-hint';
      hint.textContent = `搜索「${this.searchInput.value.trim()}」· 当前会话 ${log.length} 条匹配`;
      this.messagesEl.appendChild(hint);
    }

    if (!log.length) {
      const empty = document.createElement('div');
      empty.className = 'messages-empty';
      empty.textContent = q ? '当前会话没有匹配的消息'
        : key === CONV_ALL
          ? '还没有人说话，先打个招呼吧'
          : `和 ${roleName(key)} 还没有聊天记录`;
      this.messagesEl.appendChild(empty);
      return;
    }

    log.forEach((entry, i) => {
      const prev = i > 0 ? log[i - 1] : null;
      if (!prev || (entry.ts || 0) - (prev.ts || 0) > 300) {
        const tr = document.createElement('div');
        tr.className = 'time-row';
        tr.textContent = formatTime(entry.ts);
        this.messagesEl.appendChild(tr);
      }

      if (entry.kind === 'tip') {
        const tip = document.createElement('div');
        tip.className = 'tip-row';
        tip.textContent = entry.text || '';
        this.messagesEl.appendChild(tip);
        return;
      }
      if (entry.kind === 'game' || entry.kind === 'fx') {
        const row = document.createElement('div');
        row.className = 'game-row';
        const pill = document.createElement('span');
        pill.textContent = entry.text || '';
        row.appendChild(pill);
        this.messagesEl.appendChild(row);
        return;
      }

      // 新记录写角色 ID：换身份后，旧身份发的消息不再算「我」
      const mine = entry.who === (state.me ? state.me.role : null)
        || entry.who === 'me';
      const row = document.createElement('div');
      row.className = 'msg-row' + (mine ? ' mine' : '');
      row.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        this.setQuote(entry);
      });

      const av = document.createElement('span');
      av.className = 'avatar msg-avatar';
      const avRole = mine ? (state.me ? state.me.role : null) : entry.who;
      if (avRole) {
        av.textContent = (roleName(avRole) || '?').slice(0, 1);
        this.fillAvatar(av, avRole);
      } else {
        av.textContent = '?';
      }
      row.appendChild(av);

      const body = document.createElement('div');
      body.className = 'msg-body';
      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      const who = document.createElement('span');
      who.textContent = mine ? '我' : roleName(entry.who);
      const time = document.createElement('span');
      time.textContent = formatTime(entry.ts);
      meta.append(who, time);

      body.append(meta);
      if (entry.re && entry.re.text) body.appendChild(this.quoteBlock(entry.re));
      body.append(this.mediaContent(entry));
      row.appendChild(body);
      this.messagesEl.appendChild(row);
    });

    if (this.forceBottom || nearBottom) {
      this.forceBottom = false;
      requestAnimationFrame(() => this.scrollToBottom());
    }
  }

  quoteBlock(re) {
    const box = document.createElement('div');
    box.className = 'quote-block';
    const who = re.who === (state.me ? state.me.role : null) ? '我' : roleName(re.who);
    box.textContent = `${who}：${re.text || ''}`;
    return box;
  }

  mediaContent(entry) {
    const mt = entry.mt || 'text';
    if (mt === 'emoji' && (entry.char || entry.text)) {
      const bubble = document.createElement('div');
      bubble.className = 'bubble emoji';
      bubble.textContent = entry.char || entry.text;
      return bubble;
    }
    if (mt === 'img' || mt === 'sticker') {
      if (entry.img) {
        const src = imageDataURL(entry.img, mt === 'sticker');
        const img = document.createElement('img');
        img.className = mt === 'sticker' ? 'chat-sticker' : 'chat-img';
        img.src = src;
        img.alt = entry.text || (mt === 'sticker' ? '表情包' : '图片');
        img.loading = 'lazy';
        img.addEventListener('click', () => this.openImageViewer(src));
        return img;
      }
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = entry.text || (mt === 'sticker' ? '[表情包]' : '[图片]');
      return bubble;
    }
    if (mt === 'file') {
      const card = document.createElement('div');
      card.className = 'file-card';
      const name = document.createElement('div');
      name.className = 'file-name';
      name.textContent = entry.name || '文件';
      const size = document.createElement('div');
      size.className = 'file-size';
      size.textContent = `${humanSize(entry.size || 0)} · 点击下载`;
      card.append(name, size);
      card.addEventListener('click', () => downloadFile(entry));
      return card;
    }
    return this.textBubble(entry.text || '');
  }

  textBubble(text) {
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    const re = this.mentionRegex();
    if (!re) {
      bubble.textContent = text;
      return bubble;
    }
    let last = 0;
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      if (m.index > last) {
        bubble.appendChild(document.createTextNode(text.slice(last, m.index)));
      }
      const s = document.createElement('span');
      s.className = 'mention';
      s.textContent = m[0];
      bubble.appendChild(s);
      last = m.index + m[0].length;
    }
    if (last < text.length) {
      bubble.appendChild(document.createTextNode(text.slice(last)));
    }
    return bubble;
  }

  mentionRegex() {
    if (this._mentionRe === null && state.roleNames) {
      const names = Object.values(state.roleNames);
      if (names.length) {
        const parts = names.concat(['所有人'])
          .sort((a, b) => b.length - a.length)
          .map(escapeRegExp);
        this._mentionRe = new RegExp(`@(?:${parts.join('|')})`, 'g');
      }
    }
    return this._mentionRe || null;
  }

  isNearBottom() {
    const el = this.messagesEl;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 90;
  }

  scrollToBottom() {
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  store(key, entry) {
    appendEntry(key, entry);
    const watching = state.me && state.currentKey === key && !this.root.classList.contains('hidden');
    const me = state.me ? state.me.role : null;
    if (entry.kind === 'msg' && entry.who !== me
      && entry.who !== 'me' && !watching) {
      state.unread[key] = (state.unread[key] || 0) + 1;
    } else if ((entry.kind === 'game' || entry.kind === 'fx')
      && entry.who !== me && !watching) {
      state.unread[key] = (state.unread[key] || 0) + 1;
    }
    saveHistory(key, entry);
    this.renderConversations();
    this.updateTitle();
    this.updateHeader();
    if (state.currentKey === key) this.renderMessages();
  }

  // ---------------- 引用 / @ ----------------

  setQuote(entry) {
    const text = (entry.text || '').trim();
    if (!text) return;
    let who = entry.who || '';
    // 老记录里「我」写成 'me'，服务端只认角色 ID，这里先归一化
    if (!who || who === 'me') who = state.me ? state.me.role : '';
    this.quote = {
      who,
      text: text.slice(0, 120),
      mt: entry.mt || 'text',
    };
    this.renderQuoteStrip();
    this.focusInput();
  }

  renderQuoteStrip() {
    const el = this.quoteStrip;
    if (!el) return;
    el.innerHTML = '';
    if (!this.quote) {
      el.classList.add('hidden');
      return;
    }
    const me = state.me ? state.me.role : null;
    const who = this.quote.who === me ? '我' : roleName(this.quote.who);
    const text = document.createElement('span');
    text.className = 'quote-text';
    text.textContent = `引用 ${who}：${this.quote.text}`;
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'quote-x';
    x.textContent = '×';
    x.title = '取消引用';
    x.addEventListener('click', () => this.clearQuote());
    el.append(text, x);
    el.classList.remove('hidden');
  }

  clearQuote() {
    this.quote = null;
    this.renderQuoteStrip();
  }

  toggleMentionMenu() {
    if (this.mentionMenu) {
      this.closeMentionMenu();
      return;
    }
    const menu = document.createElement('div');
    menu.className = 'mention-menu';
    const me = state.me ? state.me.role : null;
    for (const r of state.roles) {
      if (r === me) continue;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'mention-item';
      b.textContent = `@${roleName(r)}`;
      b.disabled = !isOnline(r);
      b.addEventListener('click', () => {
        this.insertMention(roleName(r));
        this.closeMentionMenu();
      });
      menu.appendChild(b);
    }
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'mention-item';
    all.textContent = '@所有人';
    all.addEventListener('click', () => {
      this.insertMention('所有人');
      this.closeMentionMenu();
    });
    menu.appendChild(all);

    const rect = this.mentionBtn.getBoundingClientRect();
    menu.style.left = `${Math.max(8, rect.left)}px`;
    menu.style.bottom = `${window.innerHeight - rect.top + 6}px`;
    document.body.appendChild(menu);
    this.mentionMenu = menu;
  }

  closeMentionMenu() {
    if (!this.mentionMenu) return;
    this.mentionMenu.remove();
    this.mentionMenu = null;
  }

  insertMention(name) {
    const el = this.input;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    const before = el.value.slice(0, start);
    let insert;
    if (/[@＠]$/.test(before)) {
      // 手输的 @ 已经在框里了，只补名字
      insert = `${name} `;
    } else if (before && !/[\s\n]$/.test(before)) {
      insert = ` @${name} `;
    } else {
      insert = `@${name} `;
    }
    el.value = before + insert + el.value.slice(end);
    const pos = before.length + insert.length;
    try { el.setSelectionRange(pos, pos); } catch (_) {}
    this.focusInput();
    this.resizeInput();
  }

  // ---------------- 顶部 / 输入区 ----------------

  updateHeader() {
    const key = state.currentKey;
    const name = nameOf(key);
    this.convTitle.textContent = name;
    const count = onlineCount();
    const total = state.roles.length || 6;
    this.onlinePill.querySelector('span:last-child').textContent = `${count}/${total} 在线`;
    this.onlinePill.querySelector('.dot').classList.toggle('off', count === 0 || !state.connected);

    if (key === CONV_ALL) {
      this.convSub.textContent = '所有人都在这里';
      this.convState.querySelector('span:last-child').textContent = `${count}/${total} 在线`;
      this.convState.querySelector('.dot').classList.toggle('off', count === 0 || !state.connected);
      this.targetLabel.textContent = '发送到 全体群聊';
      this.sendBtn.disabled = !state.connected;
    } else {
      const live = isOnline(key);
      const info = state.players[key];
      const kind = info && info.client === 'web' ? '网页版'
        : info && info.client === 'desktop' ? '桌面版' : '';
      const stateText = live ? `在线${kind ? ' · ' + kind : ''}` : '不在线';
      this.convSub.textContent = `单聊 · ${stateText}`;
      this.convState.querySelector('span:last-child').textContent = stateText;
      this.convState.querySelector('.dot').classList.toggle('off', !live || !state.connected);
      this.targetLabel.textContent = `发送给 ${name}${live ? '' : '（不在线）'}`;
      this.sendBtn.disabled = !state.connected || !live;
    }
  }

  updateMe() {
    const me = state.me ? state.me.role : null;
    this.meName.textContent = me ? roleName(me) : '';
    this.meState.textContent = state.connected ? '在线' : '离线';
    if (me) {
      this.meAvatar.textContent = (roleName(me) || '?').slice(0, 1);
      this.fillAvatar(this.meAvatar, me);
    } else {
      this.meAvatar.textContent = '';
    }
  }

  updateOnline() {
    this.updateHeader();
    this.updateMe();
  }

  updateTitle() {
    const n = unreadTotal();
    document.title = 'pet-club';
    this.updateFavicon(n);
  }

  updateFavicon(n) {
    if (!this.favicon) return;
    if (!n) {
      this.favicon.href = '/favicon.ico';
      return;
    }
    const c = document.createElement('canvas');
    c.width = 64;
    c.height = 64;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#e5484d';
    ctx.beginPath();
    ctx.arc(32, 32, 30, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 38px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(n > 9 ? '9+' : String(n), 32, 35);
    this.favicon.href = c.toDataURL('image/png');
  }

  focusInput() {
    setTimeout(() => {
      try {
        this.input.focus({ preventScroll: true });
        const len = this.input.value.length;
        this.input.setSelectionRange(len, len);
      } catch (_) {}
    }, 0);
  }

  resizeInput() {
    this.input.style.height = 'auto';
    this.input.style.height = `${Math.min(160, Math.max(64, this.input.scrollHeight))}px`;
  }

  async submit() {
    const text = this.input.value.trim();
    if (!text && !this.attachments.length) return;

    const quote = this.quote;
    let quoteUsed = false;

    if (text) {
      const why = this.onSend(text, quote);
      if (why) {
        this.note(why);
        return;
      }
      quoteUsed = true;
      this.input.value = '';
      this.resizeInput();
    }

    if (this.attachments.length && this.onMedia) {
      const items = this.attachments.slice();
      for (let i = 0; i < items.length; i++) {
        let err = '';
        try {
          err = await this.onMedia('file', items[i].file,
            (!text && i === 0) ? quote : null);
        } catch (e) {
          err = String(e.message || e);
        }
        if (err) {
          // 已经发出去的那几张把缩略图 URL 收掉，失败的和后面的留在输入框
          for (let j = 0; j < i; j++) {
            if (items[j].url) URL.revokeObjectURL(items[j].url);
          }
          this.attachments = items.slice(i);
          this.renderAttachments();
          if (quoteUsed) this.clearQuote();
          this.note(err);
          return;
        }
        if (!text && i === 0) quoteUsed = true;
      }
      this.clearAttachments();
    }

    if (quoteUsed) this.clearQuote();
    this.note('');
    this.focusInput();
  }

  note(text) {
    clearTimeout(this.noteTimer);
    this.noteEl.textContent = text || '';
    if (text) {
      this.noteTimer = setTimeout(() => {
        this.noteEl.textContent = '';
      }, 4200);
    }
  }

  setOffline(offline) {
    this.offlineBanner.classList.toggle('hidden', !offline);
    this.updateHeader();
    this.updateMe();
  }

  setNote(text) {
    this.note(text);
  }

  // ---------------- 「我是谁」菜单 ----------------

  closeMeMenu() {
    if (this.meMenu) {
      this.meMenu.remove();
      this.meMenu = null;
    }
  }

  toggleMeMenu() {
    if (this.meMenu) {
      this.closeMeMenu();
      return;
    }
    const me = state.me ? state.me.role : null;
    if (!me) return;

    const sizeNames = { small: '小', default: '默认', large: '大' };
    const sizeRows = ['small', 'default', 'large'].map((level) => (
      `<button type="button" class="ctx-item" data-size="${level}">
        <span>${sizeNames[level]}</span><span>${state.petSize === level ? '✓' : ''}</span>
      </button>`
    )).join('');

    const rem = this.reminderState();
    const remRow = (kind, label) => {
      const st = rem[kind] || { on: false, min: 45 };
      return `<div class="rem-row${st.on ? ' on' : ''}" data-kind="${kind}" title="单击启用 / 停用">
        <span class="rem-name">${label}</span>
        <span class="rem-min">${st.min} 分钟</span>
        <button type="button" class="rem-edit" data-kind="${kind}" title="编辑时间">✎</button>
        <span class="rem-check">✓</span>
      </div>`;
    };

    const menu = document.createElement('div');
    menu.className = 'ctx-menu me-menu';
    menu.innerHTML = `
      <div class="ctx-head">${roleName(me)} · 当前身份</div>
      <div class="ctx-sub">
        <button type="button" class="ctx-item ctx-parent">
          <span>桌宠大小</span><span class="ctx-arrow">›</span>
        </button>
        <div class="ctx-submenu">${sizeRows}</div>
      </div>
      <div class="ctx-sep"></div>
      <button type="button" class="ctx-item" data-cmd="remote">
        <span>同步远程互动</span><span>${state.remoteOn ? '✓' : ''}</span>
      </button>
      <div class="ctx-sep"></div>
      ${remRow('water', '喝水提醒')}
      ${remRow('sit', '久坐提醒')}
      <div class="ctx-sep"></div>
      <button type="button" class="ctx-item" data-cmd="leave"><span>换一个身份</span></button>
      <button type="button" class="ctx-item ctx-danger" data-cmd="quit"><span>退出</span></button>`;
    document.body.appendChild(menu);
    this.meMenu = menu;

    const rect = this.meBar.getBoundingClientRect();
    const mrect = menu.getBoundingClientRect();
    const left = Math.max(8, rect.left);
    menu.style.left = `${left}px`;
    menu.style.top = `${Math.max(8, rect.top - mrect.height - 8)}px`;
    // 二级悬浮框默认贴在右侧；快出屏幕时翻到左边
    if (left + mrect.width + 140 > window.innerWidth - 8) {
      menu.querySelectorAll('.ctx-submenu').forEach((sub) => {
        sub.classList.add('left');
      });
    }

    menu.querySelectorAll('.ctx-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        // 「桌宠大小」只是二级悬浮框的入口，不关菜单
        if (btn.classList.contains('ctx-parent')) {
          btn.parentElement.classList.toggle('open');
          return;
        }
        const cmd = btn.dataset.cmd;
        const size = btn.dataset.size;
        this.closeMeMenu();
        if (size) {
          if (this.onPetSize) this.onPetSize(size);
        } else if (cmd === 'remote') {
          this.onRemote(!state.remoteOn);
        } else if (cmd === 'leave') {
          this.onLeave();
        } else if (cmd === 'quit') {
          this.onQuit();
        }
      });
    });

    menu.querySelectorAll('.rem-row').forEach((row) => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('.rem-edit')) return;
        const kind = row.dataset.kind;
        const st = this.reminderState()[kind];
        if (this.onReminder) this.onReminder(kind, !st.on, st.min);
        const now = this.reminderState()[kind];
        row.classList.toggle('on', now.on);
      });
    });

    menu.querySelectorAll('.rem-edit').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const kind = btn.dataset.kind;
        const st = this.reminderState()[kind];
        const what = kind === 'water' ? '喝水' : '起来动动';
        const value = window.prompt(`每隔多少分钟提醒${what}？`, st.min);
        if (value === null || value === '') return;
        const mins = Math.max(5, Math.min(600, Number(value) || st.min));
        if (this.onReminder) this.onReminder(kind, st.on, mins);
        const row = btn.closest('.rem-row');
        const now = this.reminderState()[kind];
        row.querySelector('.rem-min').textContent = `${now.min} 分钟`;
        row.classList.toggle('on', now.on);
      });
    });
  }
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function previewOf(key, entry) {
  if (!entry) return '还没有消息';
  if (entry.kind === 'tip' || entry.kind === 'game' || entry.kind === 'fx') return entry.text || '';
  const text = entry.text || '';
  const me = state.me ? state.me.role : null;
  if (entry.who === me || entry.who === 'me' || key !== CONV_ALL) return text;
  return `${roleName(entry.who)}：${text}`;
}

function imageDataURL(b64, sticker) {
  if (!b64) return '';
  if (b64.startsWith('data:')) return b64;
  if (sticker || b64.startsWith('iVBORw0KGgo')) {
    return `data:image/png;base64,${b64}`;
  }
  if (b64.startsWith('/9j/')) return `data:image/jpeg;base64,${b64}`;
  if (b64.startsWith('R0lGOD')) return `data:image/gif;base64,${b64}`;
  if (b64.startsWith('UklGR')) return `data:image/webp;base64,${b64}`;
  return `data:image/png;base64,${b64}`;
}

function humanSize(n) {
  let v = Number(n) || 0;
  for (const unit of ['B', 'KB', 'MB', 'GB']) {
    if (v < 1024 || unit === 'GB') {
      return unit === 'B' ? `${Math.round(v)} ${unit}` : `${v.toFixed(1)} ${unit}`;
    }
    v /= 1024;
  }
  return '';
}

function downloadFile(entry) {
  if (!entry.img) return;
  try {
    const raw = atob(entry.img);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const blob = new Blob([bytes], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = entry.name || 'file';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (_) {
    // base64 坏了就什么也不做
  }
}
