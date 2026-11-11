# -*- coding: utf-8 -*-
"""聊天面板。

三块：
  左边会话栏（搜索、头像、名字+时间、预览、未读红点）
  右边消息流（我的绿气泡靠右、别人的白气泡靠左、系统提示居中灰字）
  底下输入区（表情 / 图片 / 文件 + 输入框 + 发送）

数据仍然不在这个类里：`App.chat_log` 是唯一一份，窗口只是它的视图；
未读也是 `App.unread`（共享字典），切会话时清零，托盘闪的就是它。
"""
import io
import os
import time

from PIL import Image
from PySide6.QtCore import (QBuffer, QIODevice, QPoint, QRectF, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from . import host, media
from .config import ROLE_NAME, ROLES, assets_dir
from .theme import (ACCENT, BORDER, FG, FIELD, LINE, MUTED, SUB, WINDOW,
                   CardWindow, avatar, font)
from .windows import to_qimage

CONV_ALL = 'all'

WIDTH = 728
LIST_W = 212
MSG_H = 392
ROW_H = 64
PASTE_MAX = 3               # 一次最多发几个：多了就是刷屏，对面也来不及看
GAP_TIME = 300.0
BUBBLE_MAX = 300

CANVAS_BG = '#f2f2f2'
BUBBLE_ME = '#95ec69'
BUBBLE_ME_FG = '#111111'
BUBBLE_OTH = '#ffffff'
SIDE_BG = '#f7f7f7'
SIDE_SEL = '#e6e6e6'
SIDE_HOVER = '#efefef'

AVATAR_COLORS = ('#5b5bf0', '#0f9d76', '#e08c2a', '#d6455d', '#2b8fd6')


def human_size(n):
    """12345 → '12.1 KB'。文件卡片上那一行小字。"""
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return ''


def _qimage(pil):
    return to_qimage(pil)


def _pil_from_clipboard(img):
    """QImage → PIL.Image（走 PNG 编码，慢一点点但不会有像素格式的坑）。"""
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, 'PNG')
    return Image.open(io.BytesIO(bytes(buf.data()))).convert('RGBA')


class ConvRow(QWidget):
    """会话栏的一行。选中底色、未读红点、头像、两行文字。"""

    clicked = Signal(str)

    def __init__(self, key, name, me, parent=None):
        super().__init__(parent)
        self.key = key
        self.name = name
        self.selected = False
        self.unread = 0
        self.pix = None if key == CONV_ALL else avatar(key, 38)
        self.preview = ''
        self.stamp = ''
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)

    def set_data(self, preview, stamp, unread, selected):
        self.preview = preview or ''
        self.stamp = stamp or ''
        self.unread = int(unread or 0)
        self.selected = bool(selected)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
            e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        bg = SIDE_SEL if self.selected else SIDE_BG
        p.fillRect(self.rect(), QColor(bg))

        # 头像
        ax, ay, s = 11, (ROW_H - 40) // 2, 40
        if self.pix is not None:
            p.drawPixmap(ax, ay, self.pix)
        else:
            idx = ROLES.index(self.key) if self.key in ROLES else 0
            color = (ACCENT if self.key == CONV_ALL
                     else AVATAR_COLORS[idx % len(AVATAR_COLORS)])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(ax, ay, s, s)
            p.setPen(QColor('#ffffff'))
            p.setFont(font(12, True))
            p.drawText(QRectF(ax, ay, s, s), Qt.AlignCenter,
                       '群' if self.key == CONV_ALL else self.name[:1])

        x = ax + s + 9
        right = self.width() - 10
        p.setPen(QColor(MUTED))
        p.setFont(font(8))
        tw = p.fontMetrics().horizontalAdvance(self.stamp)
        p.drawText(QRectF(right - tw, 11, tw, 14), Qt.AlignRight | Qt.AlignVCenter,
                   self.stamp)

        p.setPen(QColor(FG))
        p.setFont(font(10, True))
        name_w = max(30, right - tw - 6 - x)
        name = p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(name_w))
        p.drawText(QRectF(x, 9, name_w, 18), Qt.AlignLeft | Qt.AlignVCenter, name)

        # 红点：先算它的宽度，预览文字要给它让位
        badge_w = 0
        if self.unread:
            txt = str(self.unread) if self.unread < 10 else '9+'
            p.setFont(font(8, True))
            bw = max(18, p.fontMetrics().horizontalAdvance(txt) + 10)
            badge_w = bw + 6
            bx = right - bw
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#f0445a'))
            p.drawRoundedRect(QRectF(bx, 34, bw, 16), 8, 8)
            p.setPen(QColor('#ffffff'))
            p.drawText(QRectF(bx, 34, bw, 16), Qt.AlignCenter, txt)

        p.setPen(QColor(MUTED))
        p.setFont(font(9))
        pw = max(20, right - badge_w - x)
        text = p.fontMetrics().elidedText(self.preview, Qt.ElideRight, int(pw))
        p.drawText(QRectF(x, 33, pw, 18), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()


class ChatWindow(CardWindow):
    """一个窗口，五个会话。"""

    def __init__(self, parent=None, on_send=None, on_close=None, me=None,
                 is_online=None, log=None, on_media=None, unread=None):
        super().__init__(parent, WIDTH, 'pet-club', stretch=False)
        self.on_send = on_send
        self.on_media = on_media
        self.on_close = on_close
        self.me = me
        self.is_online = is_online or (lambda r: False)
        self.log = log if log is not None else {}
        self.unread = unread if unread is not None else {}
        self.key = CONV_ALL
        self.query = ''
        self._rows = {}
        self._pop = None
        self._pop_prev = None       # 刚关掉的弹层，别在点击栈里析构
        self._tabs = {}
        self._pick_grid = None
        self._pick_box = None
        self._page = 'emoji'
        self._top_on = True

        self.setFixedHeight(MSG_H + 172 + 28)
        self._build()
        self.refresh_list()
        self.switch(CONV_ALL)

    # ---------------- 构建 ----------------

    def _build(self):
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        left = QWidget()
        left.setFixedWidth(LIST_W)
        left.setStyleSheet('background: %s; border-radius: 10px;' % SIDE_BG)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 8, 0, 0)
        lv.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索')
        self.search.setFont(font(9))
        self.search.setFixedHeight(28)
        self.search.setStyleSheet(
            'QLineEdit { background:#ffffff; border:1px solid %s; border-radius:8px;'
            ' padding:3px 9px; }' % LINE)
        self.search.textChanged.connect(self._on_search)
        lv.addWidget(self.search)
        lv.addSpacing(2)

        self.list_box = QVBoxLayout()
        self.list_box.setContentsMargins(0, 0, 0, 0)
        self.list_box.setSpacing(0)
        lv.addLayout(self.list_box)
        lv.addStretch(1)
        body.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.title = QLabel('')
        self.title.setFont(font(12, True))
        head.addWidget(self.title)
        self.state_lb = QLabel('')
        self.state_lb.setFont(font(8))
        self.state_lb.setStyleSheet('color: %s;' % MUTED)
        head.addWidget(self.state_lb)
        head.addStretch(1)
        rv.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedHeight(MSG_H)
        self.scroll.setStyleSheet(
            'QScrollArea { background: %s; border-radius: 10px; border: none; }'
            % CANVAS_BG)
        self.msg_host = QWidget()
        self.msg_host.setStyleSheet('background: %s;' % CANVAS_BG)
        self.msg_box = QVBoxLayout(self.msg_host)
        self.msg_box.setContentsMargins(14, 12, 14, 12)
        self.msg_box.setSpacing(8)
        self.msg_box.addStretch(1)
        self.scroll.setWidget(self.msg_host)
        rv.addWidget(self.scroll)

        self.note = QLabel('')
        self.note.setFont(font(8))
        self.note.setStyleSheet('color: %s;' % SUB)
        self.note.setFixedHeight(18)
        rv.addWidget(self.note)

        inbox = QFrame()
        inbox.setStyleSheet(
            'QFrame { background: %s; border: 1px solid %s; border-radius: 10px; }'
            % (WINDOW, LINE))
        iv = QVBoxLayout(inbox)
        iv.setContentsMargins(10, 8, 8, 6)
        iv.setSpacing(4)

        self.field = QLineEdit()
        self.field.setPlaceholderText('说点什么…（Enter 发送，Ctrl+V 贴图）')
        self.field.setFont(font(10))
        self.field.setStyleSheet(
            'QLineEdit { background: transparent; border: none; padding: 2px; }')
        self.field.returnPressed.connect(self.send)
        iv.addWidget(self.field)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(2)
        self.icon_btn = {}
        for kind, text, tip in (('emoji', '☺', '表情'),
                                ('image', '▧', '图片'),
                                ('file', '⎘', '文件')):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(26, 26)
            b.setStyleSheet(
                'QToolButton { border: none; border-radius: 6px; color: %s;'
                ' font-size: 13pt; } QToolButton:hover { background: %s; }'
                % (SUB, FIELD))
            b.clicked.connect(lambda _=False, k=kind: self._on_icon(k))
            bar.addWidget(b)
            self.icon_btn[kind] = b
        bar.addStretch(1)
        self.send_btn = QPushButton('发送')
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFixedSize(62, 26)
        self.send_btn.setFont(font(9, True))
        self.send_btn.setStyleSheet(
            'QPushButton { background: %s; color: #fff; border-radius: 7px; }'
            'QPushButton:hover { background: #4a4ade; }' % ACCENT)
        self.send_btn.clicked.connect(self.send)
        bar.addWidget(self.send_btn)
        iv.addLayout(bar)
        rv.addWidget(inbox)

        body.addWidget(right, 1)
        self.content.addLayout(body)
        self.setAcceptDrops(True)

    # ---------------- 数据 ----------------

    def convs(self):
        out = [(CONV_ALL, '全体群聊')]
        for r in ROLES:
            if r != self.me:
                out.append((r, ROLE_NAME.get(r, r)))
        return out

    def visible_convs(self):
        q = (self.query or '').strip().lower()
        if not q:
            return self.convs()
        out = []
        for key, name in self.convs():
            if q in name.lower():
                out.append((key, name))
                continue
            for e in self.entries(key):
                if q in (e.get('text') or '').lower():
                    out.append((key, name))
                    break
        return out

    def name_of(self, key):
        return '全体群聊' if key == CONV_ALL else ROLE_NAME.get(key, key)

    def entries(self, key=None):
        return self.log.get(key if key is not None else self.key) or []

    def unread_of(self, key):
        return int(self.unread.get(key) or 0)

    def last_of(self, key):
        es = self.entries(key)
        return es[-1] if es else None

    def preview_of(self, key):
        e = self.last_of(key)
        if e is None:
            return '还没有消息'
        text = e.get('text') or ''
        if e.get('kind') == 'tip':
            return text
        if e.get('who') == 'me' or key != CONV_ALL:
            return text
        return '%s：%s' % (ROLE_NAME.get(e.get('who'), ''), text)

    def history_text(self, key=None):
        return '\n'.join(e.get('text') or '' for e in self.entries(key))

    def kinds(self, key=None):
        return [e.get('kind') for e in self.entries(key)]

    def types(self, key=None):
        return ['tip' if e.get('kind') == 'tip' else (e.get('mt') or 'text')
                for e in self.entries(key)]

    # ---------------- 会话栏 ----------------

    def _on_search(self, text):
        self.query = text
        self.refresh_list()

    @staticmethod
    def _stamp(ts):
        if not ts:
            return ''
        t = time.localtime(ts)
        now = time.localtime()
        if (t.tm_year, t.tm_yday) == (now.tm_year, now.tm_yday):
            return time.strftime('%H:%M', t)
        return time.strftime('%m/%d', t)

    def refresh_list(self):
        self._rows = {}
        while self.list_box.count():
            item = self.list_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
        for key, name in self.visible_convs():
            row = ConvRow(key, name, self.me, self)
            row.clicked.connect(self.switch)
            self.list_box.addWidget(row)
            self._rows[key] = row
        self._sync_rows()

    def _sync_rows(self):
        for key, row in self._rows.items():
            last = self.last_of(key) or {}
            row.set_data(self.preview_of(key), self._stamp(last.get('ts')),
                         self.unread_of(key), key == self.key)

    # ---------------- 切会话 ----------------

    def switch(self, key):
        if key not in [k for k, _ in self.convs()]:
            key = CONV_ALL
        self.key = key
        self.unread[key] = 0
        self.title.setText(self.name_of(key))
        self._update_state()
        self.set_note('')
        self.refresh_list()
        self.render()
        self.focus_input()

    def refresh_state(self):
        self._update_state()
        self._sync_rows()

    def _update_state(self):
        if self.key == CONV_ALL:
            online = sum(1 for r in ROLES if r == self.me or self.is_online(r))
            text = '%d/%d 在线' % (online, len(ROLES))
        else:
            text = '在线' if self.is_online(self.key) else '空闲'
        self.state_lb.setText(text)

    def on_new(self, key):
        self.refresh_list()
        if key == self.key:
            self.render()

    # ---------------- 消息渲染 ----------------

    def _clear_msgs(self):
        while self.msg_box.count():
            item = self.msg_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
        self.msg_box.addStretch(1)

    def render(self, scroll=True):
        self._clear_msgs()
        last_ts = None
        entries = self.entries()
        for e in entries:
            ts = e.get('ts')
            if last_ts is not None and ts and ts - last_ts > GAP_TIME:
                self.msg_box.insertWidget(self.msg_box.count() - 1,
                                          self._time_row(ts))
            last_ts = ts or last_ts
            self.msg_box.insertWidget(self.msg_box.count() - 1, self._entry_row(e))
        if scroll:
            QTimer.singleShot(0, self._scroll_end)

    def _scroll_end(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _time_row(self, ts):
        lb = QLabel(time.strftime('%m月%d日 %H:%M', time.localtime(ts)))
        lb.setAlignment(Qt.AlignCenter)
        lb.setFont(font(8))
        lb.setStyleSheet('color: #9aa0ac;')
        return lb

    def _entry_row(self, e):
        if e.get('kind') == 'tip':
            lb = QLabel(e.get('text') or '')
            lb.setAlignment(Qt.AlignCenter)
            lb.setWordWrap(True)
            lb.setFont(font(8))
            lb.setStyleSheet('color: #9aa0ac;')
            return lb

        mine = e.get('who') == 'me'
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        if not mine:
            av = QLabel()
            if e.get('who'):
                pix = avatar(e['who'], 30)
                if pix is not None:
                    av.setPixmap(pix)
            av.setFixedSize(30, 30)
            h.addWidget(av, 0, Qt.AlignTop)
        else:
            h.addStretch(1)

        content = self._content(e, mine)
        if content is not None:
            h.addWidget(content, 0, Qt.AlignTop)
        if not mine:
            h.addStretch(1)
        return row

    def _content(self, e, mine):
        mt = e.get('mt') or 'text'
        if mt in ('img', 'sticker'):
            pix = self._img_pix(e)
            if pix is None:
                lb = QLabel(e.get('text') or '')
                lb.setFont(font(10))
                return lb
            lb = QLabel()
            lb.setPixmap(pix)
            lb.setCursor(Qt.PointingHandCursor)
            if mt == 'img':
                lb.setStyleSheet('background: #ffffff; padding: 4px; border-radius: 8px;')
            lb.mousePressEvent = lambda _e, p=pix: self._show_big(p)
            return lb

        if mt == 'file':
            return self._file_card(e)

        text = e.get('text') or ''
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setFont(font(10))
        lb.setMaximumWidth(BUBBLE_MAX)
        lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if mine:
            lb.setStyleSheet(
                'background: %s; color: %s; padding: 9px 12px;'
                ' border-radius: 10px;' % (BUBBLE_ME, BUBBLE_ME_FG))
        else:
            lb.setStyleSheet(
                'background: %s; color: %s; padding: 9px 12px;'
                ' border: 1px solid #e6e6e6; border-radius: 10px;'
                % (BUBBLE_OTH, '#111827'))
        return lb

    def _img_pix(self, e):
        """条目里的图 → 缩放后的 QPixmap。表情包不套白框，普通图片按 220 宽缩。"""
        limit = 140 if (e.get('mt') == 'sticker') else 220
        im = media.thumb(e.get('img') or '', e.get('path') or '', limit)
        if im is None:
            return None
        return QPixmap.fromImage(_qimage(im))

    def _file_card(self, e):
        w = QWidget()
        w.setFixedWidth(240)
        w.setStyleSheet(
            'QWidget { background: #ffffff; border: 1px solid #e6e6e6;'
            ' border-radius: 10px; }')
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        name = QLabel(e.get('name') or '文件')
        name.setFont(font(9, True))
        name.setStyleSheet('color: %s; border: none;' % FG)
        name.setToolTip(e.get('path') or '')
        v.addWidget(name)
        size = QLabel(human_size(e.get('size') or 0))
        size.setFont(font(8))
        size.setStyleSheet('color: %s; border: none;' % MUTED)
        v.addWidget(size)
        path = e.get('path') or ''
        w.setCursor(Qt.PointingHandCursor)
        w.mousePressEvent = lambda _e: self._open_file(path)
        return w

    def _open_file(self, path):
        if path and os.path.exists(path):
            try:
                os.startfile(path)           # noqa: S606 —— 打开自己保存的文件
            except (OSError, AttributeError):
                pass

    def _show_big(self, pix):
        dlg = QFrame(None, Qt.Window | Qt.FramelessWindowHint)
        dlg.setStyleSheet('background: #111827;')
        lb = QLabel(dlg)
        lb.setPixmap(pix)
        lb.adjustSize()
        dlg.resize(lb.size())
        dlg.move(QGuiApplication.primaryScreen().availableGeometry().center()
                 - QPoint(dlg.width() // 2, dlg.height() // 2))
        dlg.mousePressEvent = lambda _e: dlg.close()
        dlg.setCursor(Qt.PointingHandCursor)
        dlg.show()

    # ---------------- 提示行 ----------------

    def set_note(self, text):
        self.note.setText(text or '')

    # ---------------- 发送 ----------------

    def send(self):
        if self.on_send is None:
            return
        text = self.field.text().strip()
        if not text:
            return
        why = self.on_send(text) or ''
        if why:
            self.set_note(why)
            return
        self.field.clear()
        self.set_note('')

    def focus_input(self):
        self.field.setFocus()

    # ---------------- 表情 / 图片 / 文件 ----------------

    def _on_icon(self, kind):
        if self._pop is not None and self._pop.isVisible():
            self._close_pop()
            return
        if kind == 'emoji':
            self._picker_pop()
        elif kind == 'image':
            path, _ = QFileDialog.getOpenFileName(
                self, '发图片', '', '图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)')
            if path:
                self._send_media('img', path)
            return
        elif kind == 'file':
            path, _ = QFileDialog.getOpenFileName(self, '发文件', '')
            if path:
                self._send_media('file', path)
            return
        if self._pop is not None:
            btn = self.icon_btn.get(kind)
            pos = btn.mapToGlobal(QPoint(0, -self._pop.height() - 6)) if btn else QPoint(0, 0)
            self._pop.move(pos)
            self._pop.show()

    def _picker_pop(self):
        """表情 / 表情包拾取面板：三页，同一块格子换内容。"""
        pop = QFrame(None, Qt.Popup)
        self._pop = pop             # 弹层没有 parent，引用必须自己先攥住
        pop.setStyleSheet(
            'QFrame { background: #ffffff; border: 1px solid %s;'
            ' border-radius: 10px; }' % BORDER)
        v = QVBoxLayout(pop)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self._tabs = {}
        for page, label in (('emoji', '表情'), ('mine', '我的'),
                            ('others', '他人')):
            b = QToolButton(pop)
            b.setText(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                'QToolButton { border: none; border-radius: 6px;'
                ' padding: 3px 10px; color: %s; }'
                'QToolButton:checked { background: %s; color: %s; }'
                'QToolButton:hover { background: %s; }'
                % (MUTED, FIELD, FG, FIELD))
            b.clicked.connect(lambda _=False, p=page: self._fill_page(p))
            tabs.addWidget(b)
            self._tabs[page] = b
        tabs.addStretch(1)
        v.addLayout(tabs)

        box = QWidget(pop)
        # 格子固定成 8 列 × 3 行：翻页时高度不跳，鼠标不用重新找位置
        self._pick_grid = QGridLayout(box)
        self._pick_grid.setContentsMargins(0, 0, 0, 0)
        self._pick_grid.setSpacing(2)
        self._pick_box = box
        v.addWidget(box)

        self._fill_page('emoji')
        pop.adjustSize()
        return pop

    def _clear_grid(self):
        while self._pick_grid.count():
            item = self._pick_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)

    def _fill_page(self, page):
        """换一页内容。表情是字符，表情包是素材里的卡片。"""
        if self._pick_grid is None:
            return
        self._clear_grid()
        for key, b in self._tabs.items():
            b.setChecked(key == page)
        self._page = page

        if page == 'emoji':
            for i, ch in enumerate(media.EMOJI_CHARS):
                b = QToolButton(self._pick_box)
                b.setText(ch)
                b.setFixedSize(30, 30)
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet(
                    'QToolButton { border: none; border-radius: 6px;'
                    ' font-size: 15pt; }'
                    'QToolButton:hover { background: %s; }' % FIELD)
                b.clicked.connect(lambda _=False, c=ch: self._pick_emoji(c))
                self._pick_grid.addWidget(b, i // 8, i % 8)
        else:
            cards = self._sticker_cards(page)
            for i, path in enumerate(cards):
                b = QToolButton(self._pick_box)
                b.setFixedSize(44, 44)
                b.setIcon(QIcon(path))
                b.setIconSize(QSize(40, 40))
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet(
                    'QToolButton { border: none; border-radius: 8px; }'
                    'QToolButton:hover { background: %s; }' % FIELD)
                b.setToolTip(self._sticker_tip(path))
                b.clicked.connect(
                    lambda _=False, p=path: self._pick_sticker(p))
                self._pick_grid.addWidget(b, i // 8, i % 8)
        if self._pop is not None:
            self._pop.adjustSize()

    def _sticker_cards(self, page):
        """这一页有哪些卡片：我的 = 自己八张；他人 = 另外四个角色各八张。"""
        if page == 'mine':
            roles = [self.me] if self.me else []
        else:
            roles = [r for r in ROLES if r != self.me]
        out = []
        for r in roles:
            out.extend(media.sticker_paths(assets_dir(), r))
        return out

    @staticmethod
    def _sticker_tip(path):
        name = os.path.splitext(os.path.basename(path))[0]
        return '%s · %s' % (ROLE_NAME.get(os.path.basename(
            os.path.dirname(path)), ''), name)

    def _pick_emoji(self, ch):
        self._close_pop()
        self._send_media('emoji', ch)

    def _pick_sticker(self, path):
        self._close_pop()
        self._send_media('sticker', path)

    def _close_pop(self):
        if self._pop is None:
            return
        pop = self._pop
        pop.close()
        # 这个方法常常是从弹层里某个表情卡片的 clicked 回调进来的，
        # 此刻 Qt 还在 QFrame 自己的派发栈上。引用一丢就会被 shiboken
        # 删掉 C++ 对象 —— 留一拍，并把指向弹层子对象的引用一并清掉，
        # 免得下次翻页碰到已经失效的 QGridLayout。
        self._pop_prev, self._pop = pop, None
        self._pick_grid = None
        self._pick_box = None
        self._tabs = {}

    def _send_media(self, mt, payload):
        if self.on_media is None:
            return
        why = self.on_media(mt, payload) or ''
        self.set_note(why)

    # ---------------- 粘贴 / 拖放 ----------------

    def keyPressEvent(self, e):
        if e.matches(Qt.Key_V) and (e.modifiers() & Qt.ControlModifier):
            if self._paste():
                e.accept()
                return
        super().keyPressEvent(e)

    def _paste(self):
        md = QApplication.clipboard().mimeData()
        if md is None:
            return False
        if md.hasImage():
            pil = _pil_from_clipboard(QApplication.clipboard().image())
            self._send_media('img', pil)
            return True
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self._send_paths(paths)
                return True
        self.field.paste()
        return True

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self._send_paths(paths)
        e.acceptProposedAction()

    def _send_paths(self, paths):
        """一批本地文件：按扩展名分图片/文件，最多发 PASTE_MAX 个。

        上限不是省流量，是别刷屏 —— 拖着二十个文件松手的本意多半是
        「顺手都给你」，但对面会收到二十条消息。
        """
        for p in paths[:PASTE_MAX]:
            ext = os.path.splitext(p)[1].lower()
            mt = 'img' if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp',
                                  '.bmp') else 'file'
            self._send_media(mt, p)
        if len(paths) > PASTE_MAX:
            self.set_note('一次最多发 %d 个，先发这些' % PASTE_MAX)

    # ---------------- 层级 ----------------

    def lift(self):
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def follow(self, fg):
        """只有自己（或自己的弹层）是前台时才置顶，否则让位。

        规则：人物一直置顶、聊天框会让人。切走的时候顺手把表情弹层收掉，
        不然面板退了它还在上面飘着。
        """
        if fg is None:
            return
        pop = self._pop if (self._pop is not None and self._pop.isVisible()) else None
        on = host.is_foreground(fg, self, pop)
        if on == self._top_on:
            return
        self._top_on = on
        host.set_topmost(self, on)
        if not on and self._pop is not None:
            self._pop.close()
            self._pop = None

    def alive(self):
        return True

    def closeEvent(self, e):
        self._close_pop()
        super().closeEvent(e)
