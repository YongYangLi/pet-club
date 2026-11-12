# -*- coding: utf-8 -*-
"""聊天面板。

四块：
  左边会话栏（搜索、头像、名字+时间、预览、未读红点）
  右边消息流（我的绿气泡靠右、别人的白气泡靠左、系统提示居中灰字）
  输入区正上方一排悬浮的快捷互动圆角按钮（没有底板，放不下的收进「更多」）
  底下微信式输入区（几行高的文本框；表情 / 文件在左下，发送在右下）

数据仍然不在这个类里：`App.chat_log` 是唯一一份，窗口只是它的视图；
未读也是 `App.unread`（共享字典），切会话时清零，托盘闪的就是它。
"""
import io
import os
import time

from PIL import Image
from PySide6.QtCore import (QBuffer, QIODevice, QPoint, QPointF, QRectF, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QSizePolicy,
                               QTextEdit, QToolButton, QVBoxLayout, QWidget)

from . import actions, host, media
from .config import ROLE_NAME, ROLES, assets_dir
from .theme import (ACCENT, ACCENT_SOFT, BORDER, FG, FIELD, LINE, MUTED, SUB,
                    WINDOW, CardWindow, avatar, font)
from .windows import to_qimage

CONV_ALL = 'all'

WIDTH, HEIGHT = 840, 620
MIN_W, MIN_H = 660, 460
LIST_W = 212
ROW_H = 62
PASTE_MAX = 3               # 一次最多发几个：多了就是刷屏，对面也来不及看
GAP_TIME = 300.0
BUBBLE_MAX = 320
CELL = 56                   # 拾取面板一格；表情和贴纸都按这个放大
COLS = 8
MORE_W = 74                 # 「更多」占位宽度固定，显示/隐藏都不改布局
COMPOSER_H = 78             # 多行输入框：三行半文字，够写一小段话
PREVIEW_BOX = 200           # 悬停预览的逻辑边长
PREVIEW_DPR = 2.0           # 位图按 2x 物理像素生成，高分屏上不糊

CANVAS_BG = '#f5f6f8'
BUBBLE_ME = '#95ec69'
BUBBLE_ME_FG = '#111111'
BUBBLE_OTH = '#ffffff'
SIDE_BG = '#f7f7f7'
SIDE_SEL = '#e9eaf0'
SIDE_HOVER = '#efefef'

AVATAR_COLORS = ('#5b5bf0', '#0f9d76', '#e08c2a', '#d6455d', '#2b8fd6')

# 系统 UI 字体（微软雅黑 / 苹方）不含彩色 emoji 字形，拿它画 😀 会落到
# 一套很小的位图 fallback 上，放大就糊。emoji 一律走各平台自己的彩色字体。
EMOJI_FAMILY = ('Apple Color Emoji' if host.IS_MAC else
                'Segoe UI Emoji' if host.IS_WIN else host.ui_font())


def emoji_font(size):
    f = QFont(EMOJI_FAMILY)
    f.setPointSizeF(float(size))
    return f


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


def _zoom(p, widget, k):
    """hover 时以控件中心为原点放大：只改绘制，不动布局，不会挤到邻居。"""
    if k == 1.0:
        return
    cx, cy = widget.width() / 2.0, widget.height() / 2.0
    p.translate(cx, cy)
    p.scale(k, k)
    p.translate(-cx, -cy)


def _hover_events(cls):
    """给自绘按钮补上 enter/leave 的重绘。"""
    def enter(self, e):
        self._hover = True
        self.update()
        super(cls, self).enterEvent(e)

    def leave(self, e):
        self._hover = False
        self.update()
        super(cls, self).leaveEvent(e)

    cls.enterEvent = enter
    cls.leaveEvent = leave
    return cls


@_hover_events
class ToolIcon(QToolButton):
    """输入区左下角的一颗工具按钮。

    只有两颗：表情、文件（图片和文件合并到同一颗里）。矢量自绘，
    一套线宽一套颜色，hover 时放大并浮出底色，不靠字体里的符号。
    """

    TIPS = {'emoji': '表情 / 表情包', 'file': '图片 / 文件'}

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setFixedSize(32, 32)
        self.setToolTip(self.TIPS.get(kind, ''))
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QToolButton { border: none; background: transparent; }')

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT_SOFT))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)
            _zoom(p, self, 1.14)
        p.setPen(QPen(QColor(ACCENT if self._hover else SUB), 1.7,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)

        if self.kind == 'emoji':
            p.drawEllipse(QPointF(cx, cy), 9, 9)
            p.setBrush(QColor(ACCENT if self._hover else SUB))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx - 3.4, cy - 2.4), 1.2, 1.4)
            p.drawEllipse(QPointF(cx + 3.4, cy - 2.4), 1.2, 1.4)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(ACCENT if self._hover else SUB), 1.5,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(cx - 5, cy - 3, 10, 9), 200 * 16, 140 * 16)
        else:
            # 一张带折角的文件：图片也走这颗按钮，选完类型再进系统文件框
            p.drawLine(QPointF(cx - 7, cy - 9), QPointF(cx + 3, cy - 9))
            p.drawLine(QPointF(cx + 3, cy - 9), QPointF(cx + 8, cy - 4))
            p.drawLine(QPointF(cx + 8, cy - 4), QPointF(cx + 8, cy + 9))
            p.drawLine(QPointF(cx + 8, cy + 9), QPointF(cx - 7, cy + 9))
            p.drawLine(QPointF(cx - 7, cy + 9), QPointF(cx - 7, cy - 9))
            p.drawLine(QPointF(cx + 3, cy - 9), QPointF(cx + 3, cy - 4))
            p.drawLine(QPointF(cx + 3, cy - 4), QPointF(cx + 8, cy - 4))
            p.drawLine(QPointF(cx - 4, cy), QPointF(cx + 4, cy))
            p.drawLine(QPointF(cx - 4, cy + 4), QPointF(cx + 2, cy + 4))
        p.end()


@_hover_events
class ActionChip(QToolButton):
    """快捷互动的一颗。本身就是圆角按钮，背后没有底板。

    文字宽度固定，倒计时画角标，不会让整条抖来抖去。
    """

    def __init__(self, label, cmd, parent=None):
        super().__init__(parent)
        self._hover = False
        self._cd = 0
        self.setText(label)
        self.setFont(font(8))
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QToolButton { border: none; background: transparent; }')
        w = QFontMetrics(self.font()).horizontalAdvance(label) + 20
        self.setFixedSize(w, 26)
        self.clicked.connect(cmd)

    def set_cd(self, sec):
        sec = int(sec or 0)
        if sec != self._cd:
            self._cd = sec
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect())
        if self._hover and self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(r, 8, 8)
            _zoom(p, self, 1.06)
        elif self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT_SOFT))
            p.drawRoundedRect(r, 8, 8)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#f1f2f5'))
            p.drawRoundedRect(r, 8, 8)
        if self._hover and self.isEnabled():
            fg = '#ffffff'
        else:
            fg = ACCENT if self.isEnabled() else MUTED
        p.setPen(QColor(fg))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())
        if self._cd > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#f0445a'))
            p.drawEllipse(QPointF(r.right() - 6, r.top() + 6), 8, 8)
            p.setPen(QColor('#ffffff'))
            f = font(7, True)
            p.setFont(f)
            p.drawText(QRectF(r.right() - 14, r.top() - 2, 16, 16),
                       Qt.AlignCenter, str(self._cd))
        p.end()


class PickerCell(QToolButton):
    """拾取面板的一格：emoji 是大字，贴纸是图片。

    `_pix` 存的是**原图**（贴纸素材 256×256），不是缩到格子大小的缩略图：
    格子里按 hover 状态现场高质量缩放，预览浮层也从这张原图出图。
    以前先压到 48px 再拉到 140px，两层重采样把表情糊成了一团。
    """

    def __init__(self, pix, text, label, cmd, on_hover, on_leave, parent=None):
        super().__init__(parent)
        self._hover = False
        self._pix = pix
        self._text = text
        self._label = label
        self._on_hover = on_hover
        self._on_leave = on_leave
        self.setFixedSize(CELL, CELL)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)
        self.setStyleSheet('QToolButton { border: none; background: transparent; }')
        self.clicked.connect(cmd)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT_SOFT))
            p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        k = 1.25 if self._hover else 1.0
        if self._pix is not None:
            side = int((CELL - 8) * k)
            p.drawPixmap((self.width() - side) // 2,
                         (self.height() - side) // 2, side, side, self._pix)
        else:
            p.setFont(emoji_font(28 * k))
            p.setPen(QColor(FG))
            p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        self._on_hover(self)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        self._on_leave()
        super().leaveEvent(e)


class PreviewPopup(QFrame):
    """悬停时弹出的放大预览。常驻引用，只 show/hide，不排队延迟删除。

    位图按 `PREVIEW_BOX × PREVIEW_DPR` 的物理像素出图，再标上 dpr：
    高分屏 1:1 显示，普通屏是 2x 超采样，边缘都干净。
    """

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        # QLabel 也继承 QFrame，用 objectName 限定，否则子标签会被套上边框，
        # 全局 QSS 的字号还会把大表情压回 10pt。
        self.setObjectName('pv')
        self.setStyleSheet(
            '#pv { background: #ffffff; border: 1px solid %s;'
            ' border-radius: 12px; }' % BORDER)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(4)
        self.big = QLabel()
        self.big.setFixedSize(PREVIEW_BOX, PREVIEW_BOX)
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setStyleSheet('border: none; background: transparent;')
        v.addWidget(self.big)
        self.cap = QLabel()
        self.cap.setAlignment(Qt.AlignCenter)
        self.cap.setFont(font(8))
        self.cap.setStyleSheet('color: %s; border: none; background: transparent;'
                               % SUB)
        v.addWidget(self.cap)
        self.adjustSize()

    def show_for(self, cell, top_left):
        side = int(PREVIEW_BOX * PREVIEW_DPR)
        pm = QPixmap(side, side)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if cell._pix is not None:
            box = side - 20
            fit = cell._pix.scaled(box, box, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            p.drawPixmap((side - fit.width()) // 2,
                         (side - fit.height()) // 2, fit)
        else:
            # 自绘而不是 QLabel.setText：QPainter 的字号不受样式表影响，
            # 彩色 emoji 字体才能真正放大到 192pt。
            p.setFont(emoji_font(96 * PREVIEW_DPR))
            p.setPen(QColor(FG))
            p.drawText(QRectF(0, 0, side, side), Qt.AlignCenter, cell._text)
        p.end()
        pm.setDevicePixelRatio(PREVIEW_DPR)
        self.big.setPixmap(pm)
        self.cap.setText(cell._label)
        self.adjustSize()
        x = top_left.x() + cell.width() // 2 - self.width() // 2
        y = top_left.y() - self.height() - 8
        scr = QGuiApplication.primaryScreen().availableGeometry()
        x = max(scr.left() + 4, min(x, scr.right() - self.width() - 4))
        if y < scr.top() + 4:
            y = top_left.y() + cell.height() + 8
        self.move(x, y)
        self.show()
        self.raise_()


class ConvRow(QWidget):
    """会话栏的一行。选中底色、未读红点、头像、两行文字。"""

    clicked = Signal(str)

    def __init__(self, key, name, me, parent=None):
        super().__init__(parent)
        self.key = key
        self.name = name
        self.selected = False
        self.unread = 0
        self.pix = None if key == CONV_ALL else avatar(key, 40)
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
        p.fillRect(self.rect(), QColor(SIDE_SEL if self.selected else SIDE_BG))
        if self.selected:
            p.fillRect(QRectF(0, 10, 3, ROW_H - 20), QColor(ACCENT))

        ax, ay, s = 12, (ROW_H - 40) // 2, 40
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
            p.setFont(font(11, True))
            p.drawText(QRectF(ax, ay, s, s), Qt.AlignCenter,
                       '群' if self.key == CONV_ALL else self.name[:1])

        x = ax + s + 10
        right = self.width() - 12
        p.setPen(QColor(MUTED))
        p.setFont(font(8))
        tw = p.fontMetrics().horizontalAdvance(self.stamp)
        p.drawText(QRectF(right - tw, 9, tw, 13), Qt.AlignRight | Qt.AlignVCenter,
                   self.stamp)

        p.setPen(QColor(FG))
        p.setFont(font(10, True))
        name_w = max(30, right - tw - 6 - x)
        name = p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(name_w))
        p.drawText(QRectF(x, 8, name_w, 18), Qt.AlignLeft | Qt.AlignVCenter, name)

        badge_w = 0
        if self.unread:
            txt = str(self.unread) if self.unread < 10 else '9+'
            p.setFont(font(7, True))
            bw = max(18, p.fontMetrics().horizontalAdvance(txt) + 10)
            badge_w = bw + 6
            bx = right - bw
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#f0445a'))
            p.drawRoundedRect(QRectF(bx, 35, bw, 16), 8, 8)
            p.setPen(QColor('#ffffff'))
            p.drawText(QRectF(bx, 35, bw, 16), Qt.AlignCenter, txt)

        p.setPen(QColor(MUTED))
        p.setFont(font(8))
        pw = max(20, right - badge_w - x)
        text = p.fontMetrics().elidedText(self.preview, Qt.ElideRight, int(pw))
        p.drawText(QRectF(x, 34, pw, 17), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()


class Composer(QTextEdit):
    """微信式多行输入框：Enter 发送，Shift/Ctrl+Enter 换行。

    QTextEdit 没有 placeholder，空的时候自己在 viewport 上画一行灰字。
    `text()` / `setText()` 是 QLineEdit 兼容接口 —— 探针和历史脚本还按
    老接口塞文字，没必要为了换个控件把它们全改一遍。
    """

    send_requested = Signal()

    def __init__(self, parent, on_paste):
        super().__init__(parent)
        self._on_paste = on_paste
        self._hint = '说点什么…'
        self.setFont(font(10))
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            'QTextEdit { background: transparent; border: none;'
            ' padding: 2px 4px; selection-background-color: %s; }'
            % ACCENT_SOFT)
        self.setFixedHeight(COMPOSER_H)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setAcceptDrops(False)      # 拖文件交给主窗口统一处理

    def text(self):
        return self.toPlainText()

    def setText(self, s):
        self.setPlainText(s or '')

    def keyPressEvent(self, e):
        key, mod = e.key(), e.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if mod & (Qt.ShiftModifier | Qt.AltModifier):
                super().keyPressEvent(e)     # 换行
            else:
                self.send_requested.emit()  # 裸 Enter / Ctrl+Enter 都发送
            e.accept()
            return
        if key == Qt.Key_V and (mod & Qt.ControlModifier):
            if self._on_paste():
                e.accept()
                return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.toPlainText().strip():
            return
        p = QPainter(self.viewport())
        p.setFont(self.font())
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(self.viewport().rect()).adjusted(6, 3, -6, -3),
                   Qt.AlignLeft | Qt.AlignTop, self._hint)
        p.end()

    def dragEnterEvent(self, e):
        # 拖到文本框上的文件也要交给主窗口走「发附件」那条路，
        # 而不是被 QTextEdit 当成一段路径文本插进来。
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e):
        win = self.window()
        if win is not self and hasattr(win, 'dropEvent'):
            win.dropEvent(e)
        else:
            super().dropEvent(e)


class ChatWindow(CardWindow):
    """一个窗口，五个会话。"""

    def __init__(self, parent=None, on_send=None, on_close=None, me=None,
                 is_online=None, log=None, on_media=None, unread=None,
                 on_action=None, action_state=None):
        super().__init__(parent, WIDTH, 'pet-club', stretch=False,
                         win_buttons=True, resizable=True, height=HEIGHT)
        self.setMinimumSize(MIN_W, MIN_H)
        self.on_send = on_send
        self.on_media = on_media
        self.on_close = on_close
        self.on_action = on_action
        self.action_state = action_state
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
        self._preview = None
        self._more_pop = None
        self._more_grid = None
        self._more_btns = {}
        self._chips = {}
        self._more_keys = []
        self._act_avail = -1
        self._reflow_pending = False

        self._build()
        self.refresh_list()
        self.switch(CONV_ALL)
        QTimer.singleShot(0, self._place_actions)

    # ---------------- 构建 ----------------

    def _build(self):
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        left = QWidget()
        left.setFixedWidth(LIST_W)
        left.setStyleSheet('background: %s; border-radius: 12px;' % SIDE_BG)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(9, 9, 9, 9)
        lv.setSpacing(7)

        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索会话或消息')
        self.search.setFont(font(9))
        self.search.setFixedHeight(30)
        self.search.setStyleSheet(
            'QLineEdit { background:#ffffff; border:1px solid %s; border-radius:9px;'
            ' padding:4px 10px; }' % LINE)
        self.search.textChanged.connect(self._on_search)
        lv.addWidget(self.search)

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
        self.state_lb.setStyleSheet(
            'color: %s; background: %s; border-radius: 8px; padding: 2px 8px;'
            % (SUB, FIELD))
        head.addWidget(self.state_lb)
        head.addStretch(1)
        rv.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setStyleSheet(
            'QScrollArea { background: %s; border-radius: 12px; border: none; }'
            % CANVAS_BG)
        self.msg_host = QWidget()
        self.msg_host.setStyleSheet('background: %s;' % CANVAS_BG)
        self.msg_box = QVBoxLayout(self.msg_host)
        # 底部留一条给悬浮快捷按钮，最后一条消息不会被压住
        self.msg_box.setContentsMargins(12, 10, 12, 42)
        self.msg_box.setSpacing(8)
        self.msg_box.addStretch(1)
        self.scroll.setWidget(self.msg_host)
        rv.addWidget(self.scroll, 1)

        # 反馈提示只有出事时才占一行；平时收起来，快捷条就紧贴输入框
        self.note = QLabel('')
        self.note.setFont(font(8))
        self.note.setStyleSheet('color: %s;' % SUB)
        self.note.setFixedHeight(14)
        self.note.hide()
        rv.addWidget(self.note)

        self._build_actions(right)      # 浮层，不进布局：见 _place_actions
        rv.addWidget(self._build_inbox())
        body.addWidget(right, 1)
        self.content.addLayout(body)
        self.setAcceptDrops(True)

    def _build_actions(self, stage):
        """输入框正上方一排圆角按钮，浮在消息画布上。

        容器完全透明：没有白底卡片、没有边框、没有阴影，也没有「快捷互动」
        标题 —— 看上去就是一颗颗按钮浮在灰色画布上。它不进布局，高度不占
        消息区的位置；宽就多摆几颗，窄就把剩下的收进「更多」。
        """
        bar = QWidget(stage)
        bar.setObjectName('actbar')
        bar.setFixedHeight(28)
        bar.setStyleSheet('#actbar { background: transparent; }')

        row = QHBoxLayout(bar)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(5)

        self._chip_host = QWidget(bar)
        self._chip_lay = QHBoxLayout(self._chip_host)
        self._chip_lay.setContentsMargins(0, 0, 0, 0)
        self._chip_lay.setSpacing(5)
        row.addWidget(self._chip_host, 1)

        self._more_pop = QFrame(None, Qt.Popup)
        self._more_pop.setObjectName('more')
        self._more_pop.setStyleSheet(
            '#more { background: #ffffff; border: 1px solid %s;'
            ' border-radius: 12px; }' % BORDER)
        self._more_grid = QGridLayout(self._more_pop)
        self._more_grid.setContentsMargins(10, 10, 10, 10)
        self._more_grid.setSpacing(6)

        for key in actions.ORDER:
            spec = actions.get(key)
            label = spec['label']
            cmd = lambda _=False, k=key: self._on_action(k)
            chip = ActionChip(label, cmd, self._chip_host)
            chip.hide()
            self._chips[key] = chip
            extra = ActionChip(label, cmd, self._more_pop)
            extra.hide()
            self._more_btns[key] = extra

        self._more_slot = QWidget(bar)
        self._more_slot.setFixedWidth(MORE_W)
        ml = QHBoxLayout(self._more_slot)
        ml.setContentsMargins(0, 0, 0, 0)
        self.more_btn = QToolButton(self._more_slot)
        self.more_btn.setText('更多 ▾')
        self.more_btn.setFont(font(8))
        self.more_btn.setFixedHeight(26)
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setStyleSheet(
            'QToolButton { border: none; border-radius: 8px;'
            ' padding: 0 9px; color: %s; background: %s; }'
            'QToolButton:hover { color: #ffffff; background: %s; }'
            % (SUB, ACCENT_SOFT, ACCENT))
        self.more_btn.clicked.connect(self._show_more)
        ml.addWidget(self.more_btn)
        row.addWidget(self._more_slot)

        self.act_bar = bar
        self.refresh_actions()
        return bar

    def _build_inbox(self):
        """微信式的一个白框：上面几行文字，左下工具、右下发送，全在框内。"""
        inbox = QFrame()
        inbox.setObjectName('inbox')
        inbox.setStyleSheet(
            '#inbox { background: %s; border: 1px solid %s; border-radius: 12px; }'
            % (WINDOW, LINE))
        iv = QVBoxLayout(inbox)
        iv.setContentsMargins(10, 8, 10, 8)
        iv.setSpacing(2)

        self.field = Composer(inbox, self._paste)
        self.field.send_requested.connect(self.send)
        iv.addWidget(self.field)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(4)
        self.icon_btn = {}
        for kind in ('emoji', 'file'):
            b = ToolIcon(kind, inbox)
            b.clicked.connect(lambda _=False, k=kind: self._on_icon(k))
            foot.addWidget(b)
            self.icon_btn[kind] = b
        foot.addSpacing(6)

        self.target_lb = QLabel('')
        self.target_lb.setFont(font(8))
        self.target_lb.setStyleSheet('color: %s;' % MUTED)
        foot.addWidget(self.target_lb)
        foot.addStretch(1)

        self.hint = QLabel('Enter 发送  ·  Shift+Enter 换行  ·  Ctrl+V 贴图')
        self.hint.setFont(font(8))
        self.hint.setStyleSheet('color: %s;' % MUTED)
        foot.addWidget(self.hint)
        foot.addSpacing(6)

        self.send_btn = QPushButton('发送', inbox)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFixedSize(80, 30)
        self.send_btn.setFont(font(9, True))
        self.send_btn.setStyleSheet(
            'QPushButton { background: %s; color: #ffffff; border: none;'
            ' border-radius: 8px; padding: 0; }'
            'QPushButton:hover { background: %s; }'
            'QPushButton:disabled { background: #dfe1ea; }'
            % (ACCENT, '#4a4ade'))
        self.send_btn.clicked.connect(self.send)
        foot.addWidget(self.send_btn)
        iv.addLayout(foot)
        return inbox

    # ---------------- 快捷互动 ----------------

    def _action_target(self):
        return None if self.key == CONV_ALL else self.key

    def _on_action(self, key):
        self._more_pop.hide()
        if self.on_action is None:
            return
        self.on_action(key, self._action_target())
        self.set_note('已发送「%s」' % actions.get(key)['label'])
        self.refresh_actions()

    def refresh_actions(self):
        """冷却状态每 200ms 刷一次。只改启用和角标，不改文字宽度。"""
        to = self._action_target()
        for key in actions.ORDER:
            enabled, cd = True, 0
            if self.action_state is not None:
                _label, enabled, cd = self.action_state(key, to)
            for b in (self._chips.get(key), self._more_btns.get(key)):
                if b is None:
                    continue
                b.setEnabled(enabled)
                b.set_cd(cd)
                b.setToolTip('还要等 %d 秒' % cd if cd else
                             actions.get(key).get('hint', ''))

    def _place_actions(self):
        """把按钮条按到消息区底部内侧：浮在灰色画布上，不占布局高度。"""
        bar = getattr(self, 'act_bar', None)
        scroll = getattr(self, 'scroll', None)
        if bar is None or scroll is None:
            return
        self._reflow_actions()
        r = scroll.geometry()
        if r.width() <= 0 or r.height() <= 0:
            return
        bar.move(r.x() + 12, r.bottom() - bar.height() - 8)
        bar.raise_()
        if not bar.isVisible():
            bar.show()

    def _reflow_actions(self):
        """按消息区宽度决定摆几颗，其余进「更多」。窗口拉大拉小都会重算。

        浮层宽度只包住真正显示的按钮，旁边的画布照样能点、能滚。
        """
        avail = self.scroll.width() - 24
        if avail <= 0 or avail == self._act_avail:
            return
        self._act_avail = avail
        lay = self._chip_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
        used = 0
        hidden = []
        for key in actions.ORDER:
            chip = self._chips[key]
            w = chip.width() + 5
            if not hidden and used + w + MORE_W + 8 <= avail:
                lay.addWidget(chip)
                chip.show()
                used += w
            else:
                hidden.append(key)
        lay.addStretch(1)
        self._more_keys = hidden
        self.more_btn.setVisible(bool(hidden))
        self.more_btn.setToolTip('%d 个互动在「更多」里' % len(hidden)
                                 if hidden else '')
        self._sync_more()
        self.act_bar.setFixedWidth(used + MORE_W + 9)

    def _sync_more(self):
        lay = self._more_grid
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
        for i, key in enumerate(self._more_keys):
            b = self._more_btns[key]
            lay.addWidget(b, i // 2, i % 2)
            b.show()
        self._more_pop.adjustSize()

    def _show_more(self):
        if not self._more_keys:
            return
        self._more_pop.adjustSize()
        pos = self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height() + 6))
        self._more_pop.move(pos)
        self._more_pop.show()

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
        self.target_lb.setText('发送到  %s' % self.name_of(key))
        self.refresh_list()
        self.refresh_actions()
        self.render()
        self.focus_input()

    def refresh_state(self):
        self._update_state()
        self._sync_rows()

    def _update_state(self):
        if self.key == CONV_ALL:
            online = sum(1 for r in ROLES if r == self.me or self.is_online(r))
            text = '群聊 · %d/%d 在线' % (online, len(ROLES))
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
                pix = avatar(e['who'], 34)
                if pix is not None:
                    av.setPixmap(pix)
            av.setFixedSize(34, 34)
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
                lb.setStyleSheet('background: #ffffff; padding: 5px; border-radius: 10px;')
            lb.mousePressEvent = lambda _e, p=pix: self._show_big(p)
            return lb

        if mt == 'file':
            return self._file_card(e)

        if mt == 'emoji':
            # 发出来的 emoji 原来是 10pt 的一行小字，聊天里根本看不清
            char = e.get('char') or e.get('text') or ''
            lb = QLabel(char)
            lb.setFont(emoji_font(30))
            lb.setStyleSheet(
                'background: %s; padding: 4px 10px; border-radius: 12px;'
                % (BUBBLE_ME if mine else BUBBLE_OTH))
            return lb

        text = e.get('text') or ''
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setFont(font(10))
        lb.setMaximumWidth(BUBBLE_MAX)
        lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if mine:
            lb.setStyleSheet(
                'background: %s; color: %s; padding: 8px 12px;'
                ' border-radius: 12px;' % (BUBBLE_ME, BUBBLE_ME_FG))
        else:
            lb.setStyleSheet(
                'background: %s; color: %s; padding: 8px 12px;'
                ' border: 1px solid #e6e6e6; border-radius: 12px;'
                % (BUBBLE_OTH, '#111827'))
        return lb

    def _img_pix(self, e):
        """条目里的图 → 缩放后的 QPixmap。表情包不套白框，普通图片按 240 宽缩。"""
        limit = 160 if (e.get('mt') == 'sticker') else 240
        im = media.thumb(e.get('img') or '', e.get('path') or '', limit)
        if im is None:
            return None
        return QPixmap.fromImage(_qimage(im))

    def _file_card(self, e):
        w = QWidget()
        w.setFixedWidth(250)
        w.setStyleSheet(
            'QWidget { background: #ffffff; border: 1px solid #e6e6e6;'
            ' border-radius: 12px; }')
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
        self.note.setVisible(bool(text))

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
            if kind == 'emoji':
                return                  # 再点一次表情就是收起
        if kind == 'emoji':
            pop = self._picker_pop()
            btn = self.icon_btn['emoji']
            pop.move(btn.mapToGlobal(QPoint(0, -pop.height() - 6)))
            pop.show()
        else:
            self._file_menu()

    def _file_menu(self):
        """图片和文件共用一颗按钮：先选类型，再进系统文件框。"""
        menu = QMenu(self)
        img = menu.addAction('发送图片')
        any_file = menu.addAction('发送文件（任意类型）')
        btn = self.icon_btn['file']
        pos = btn.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6))
        picked = menu.exec(pos)
        self._menu_prev = menu          # exec 返回后别立刻丢 Python 引用
        if picked is img:
            path, _ = QFileDialog.getOpenFileName(
                self, '发图片', '',
                '图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;所有文件 (*)')
            if path:
                self._send_media('img', path)
        elif picked is any_file:
            path, _ = QFileDialog.getOpenFileName(self, '发文件', '')
            if path:
                self._send_media('file', path)

    def _picker_pop(self):
        """表情 / 表情包拾取面板：三页，同一块格子换内容。"""
        pop = QFrame(None, Qt.Popup)
        self._pop = pop             # 弹层没有 parent，引用必须自己先攥住
        # QLabel 也是 QFrame，选择器必须限定 objectName，否则提示文字会
        # 被套上一圈白底圆角边框。
        pop.setObjectName('pick')
        pop.setStyleSheet(
            '#pick { background: #ffffff; border: 1px solid %s;'
            ' border-radius: 12px; }' % BORDER)
        v = QVBoxLayout(pop)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self._tabs = {}
        for page, label in (('emoji', '表情'), ('mine', '我的'),
                            ('others', '他人')):
            b = QToolButton(pop)
            b.setText(label)
            b.setCheckable(True)
            b.setFont(font(8))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                'QToolButton { border: none; border-radius: 7px;'
                ' padding: 5px 12px; color: %s; }'
                'QToolButton:checked { background: %s; color: %s; }'
                'QToolButton:hover { background: %s; }'
                % (MUTED, FIELD, FG, FIELD))
            b.clicked.connect(lambda _=False, p=page: self._fill_page(p))
            tabs.addWidget(b)
            self._tabs[page] = b
        tabs.addStretch(1)
        tip = QLabel('悬停可放大预览')
        tip.setFont(font(8))
        tip.setStyleSheet('color: %s;' % MUTED)
        tabs.addWidget(tip)
        v.addLayout(tabs)

        box = QWidget(pop)
        # 格子固定 8 列：翻页时宽度不跳，鼠标不用重新找位置
        self._pick_grid = QGridLayout(box)
        self._pick_grid.setContentsMargins(0, 0, 0, 0)
        self._pick_grid.setSpacing(2)
        self._pick_box = box
        v.addWidget(box)

        if self._preview is None:
            self._preview = PreviewPopup()

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

    def _show_preview(self, cell):
        if self._preview is None or not self._pop:
            return
        self._preview.show_for(cell, cell.mapToGlobal(QPoint(0, 0)))

    def _hide_preview(self):
        if self._preview is not None:
            self._preview.hide()

    def _fill_page(self, page):
        """换一页内容。表情是字符，表情包是素材里的卡片。"""
        if self._pick_grid is None:
            return
        self._hide_preview()
        self._clear_grid()
        for key, b in self._tabs.items():
            b.setChecked(key == page)
        self._page = page

        if page == 'emoji':
            for i, ch in enumerate(media.EMOJI_CHARS):
                cell = PickerCell(
                    None, ch, '表情 · 点一下发送',
                    lambda _=False, c=ch: self._pick_emoji(c),
                    self._show_preview, self._hide_preview, self._pick_box)
                self._pick_grid.addWidget(cell, i // COLS, i % COLS)
        else:
            cards = self._sticker_cards(page)
            for i, path in enumerate(cards):
                # 原图进 PickerCell，缩放只发生在绘制的那一刻
                pix = QPixmap(path)
                cell = PickerCell(
                    pix, '', self._sticker_tip(path),
                    lambda _=False, p=path: self._pick_sticker(p),
                    self._show_preview, self._hide_preview, self._pick_box)
                self._pick_grid.addWidget(cell, i // COLS, i % COLS)
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
        self._hide_preview()
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

    # ---------------- 层级 / 尺寸 ----------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        hint = getattr(self, 'hint', None)
        if hint is not None:
            hint.setVisible(self.width() > 780)
        if not self._reflow_pending:
            self._reflow_pending = True
            QTimer.singleShot(0, self._do_reflow)

    def _do_reflow(self):
        self._reflow_pending = False
        self._place_actions()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._place_actions)

    def lift(self):
        if self.isMinimized():
            self.showNormal()
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
        if not on:
            self._close_pop()
            self._hide_preview()
            if self._more_pop is not None:
                self._more_pop.hide()

    def alive(self):
        """窗口收起来（隐藏 / 最小化）之后，消息要照常算未读。"""
        return self.isVisible() and not self.isMinimized()

    def closeEvent(self, e):
        self._close_pop()
        self._hide_preview()
        if self._more_pop is not None:
            self._more_pop.hide()
        super().closeEvent(e)
