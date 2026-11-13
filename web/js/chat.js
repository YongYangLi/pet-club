// 全屏聊天：左侧会话栏 + 右侧消息流 + 输入卡片 + 快捷互动。
// 结构和桌面版 chat.py 一致；宽度是全屏的，所以快捷互动在宽屏上是一行。
import {
  state, CONV_ALL, convKeys, nameOf, roleName, getLog, appendEntry,
  clearUnread, unreadTotal, onlineCount, isOnline, formatTime,
  escapeHtml,
} from './state.js';
import { avatarFor } from './sprites.js';
import { saveHistory } from './history.js';

// 和 petclub/media.py 保持同一份表情字符 / 表情包姿势。
const EMOJI_CHARS = ['😀', '😄', '😅', '😂', '😊', '😉', '😍', '😘',
  '😜', '😳', '😭', '😡', '😱', '😴', '🙄', '👍', '👏', '🙏'];
const STICKER_POSES = ['idle', 'wave', 'drink', 'heart', 'jump', 'sleep', 'knock', 'drag'];

export class ChatUI {
  constructor({ onSend, onAction, onLeave, onQuit, onRemote, onMedia, onPetSize }) {
    this.onSend = onSend;
    this.onAction = onAction;
    this.onLeave = onLeave;
    this.onQuit = onQuit;
    this.onRemote = onRemote;
    this.onMedia = onMedia;
    this.onPetSize = onPetSize;

    this.root = document.getElementById('main-screen');
    this.list = document.getElementById('conv-list');
    this.messagesEl = document.getElementById('messages');
    this.input = document.getElementById('composer-input');
    this.sendBtn = document.getElementById('send-btn');
    this.quick = document.getElementById('quick-actions');
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
    this.mediaPicker = document.getElementById('media-picker');
    this.fileInput = document.getElementById('file-input');
    this.attachStrip = document.getElementById('attach-strip');
    this.pickerPreview = document.getElementById('picker-preview');
    this.imageViewer = document.getElementById('image-viewer');
    this.imageViewerImg = document.getElementById('image-viewer-img');

    this.attachments = [];
    this.search = '';
    this.avatars = new Map();
    this.meMenu = null;
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

    this.sendBtn.addEventListener('click', () => this.submit());
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        this.submit();
      }
    });
    this.input.addEventListener('input', () => this.resizeInput());

    this.searchInput.addEventListener('input', () => {
      this.search = this.searchInput.value.trim().toLowerCase();
      this.renderConversations();
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
    }, true);
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      this.closePicker();
      this.closeImageViewer();
      this.closeMeMenu();
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
        return `<button type="button" class="picker-cell" data-sticker="${escapeHtml(url)}" title="${escapeHtml(label)}"><img src="${escapeHtml(url)}" alt="${escapeHtml(label)}" loading="lazy"></button>`;
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

  showMain() {
    this.root.classList.remove('hidden');
    this.renderAll();
  }

  hideMain() {
    this.closeMeMenu();
    this.closePicker();
    this.closeImageViewer();
    this.clearAttachments();
    this.root.classList.add('hidden');
  }

  resetForClaim() {
    this.closeMeMenu();
    this.closePicker();
    this.closeImageViewer();
    this.clearAttachments();
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
    const log = getLog(key);
    const nearBottom = this.isNearBottom();
    this.messagesEl.innerHTML = '';

    if (!log.length) {
      const empty = document.createElement('div');
      empty.className = 'messages-empty';
      empty.textContent = key === CONV_ALL
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

      // 新记录写角色 ID：换身份后，旧身份发的消息不再算「我」
      const mine = entry.who === (state.me ? state.me.role : null)
        || entry.who === 'me';
      const row = document.createElement('div');
      row.className = 'msg-row' + (mine ? ' mine' : '');

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

      body.append(meta, this.mediaContent(entry));
      row.appendChild(body);
      this.messagesEl.appendChild(row);
    });

    if (this.forceBottom || nearBottom) {
      this.forceBottom = false;
      requestAnimationFrame(() => this.scrollToBottom());
    }
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
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = entry.text || '';
    return bubble;
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
    }
    saveHistory(key, entry);
    this.renderConversations();
    this.updateTitle();
    this.updateHeader();
    if (state.currentKey === key) this.renderMessages();
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
    document.title = (n ? `(${n}) ` : '') + 'pet-club';
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

    if (text) {
      const why = this.onSend(text);
      if (why) {
        this.note(why);
        return;
      }
      this.input.value = '';
      this.resizeInput();
    }

    if (this.attachments.length && this.onMedia) {
      const items = this.attachments.slice();
      for (let i = 0; i < items.length; i++) {
        let err = '';
        try {
          err = await this.onMedia('file', items[i].file);
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
          this.note(err);
          return;
        }
      }
      this.clearAttachments();
    }

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

    const menu = document.createElement('div');
    menu.className = 'ctx-menu me-menu';
    menu.innerHTML = `
      <div class="ctx-head">${roleName(me)} · 当前身份</div>
      <div class="ctx-head">桌宠大小</div>
      ${sizeRows}
      <div class="ctx-sep"></div>
      <button type="button" class="ctx-item" data-cmd="remote">
        <span>同步远程互动</span><span>${state.remoteOn ? '✓' : ''}</span>
      </button>
      <div class="ctx-sep"></div>
      <button type="button" class="ctx-item" data-cmd="leave"><span>换一个身份</span></button>
      <button type="button" class="ctx-item ctx-danger" data-cmd="quit"><span>退出</span></button>`;
    document.body.appendChild(menu);
    this.meMenu = menu;

    const rect = this.meBar.getBoundingClientRect();
    const mrect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, rect.left)}px`;
    menu.style.top = `${Math.max(8, rect.top - mrect.height - 8)}px`;

    menu.querySelectorAll('.ctx-item').forEach((btn) => {
      btn.addEventListener('click', () => {
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
  }
}

function previewOf(key, entry) {
  if (!entry) return '还没有消息';
  if (entry.kind === 'tip') return entry.text || '';
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
