# -*- coding: utf-8 -*-
"""界面基元：配色、全局样式表、卡片窗、按钮、输入框、圆头像。

【为什么自己画卡片窗】
系统标题栏和「轻量、低噪音」的调性不搭。这里用 FramelessWindowHint +
自绘圆角白底，拖动、关闭、Esc 自己补上。
"""
import os

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizeGrip, QVBoxLayout,
                               QWidget)

from . import host
from .config import assets_dir

# ---------------- 配色 ----------------
WINDOW = '#ffffff'
BORDER = '#e4e7ef'
FG = '#111827'
SUB = '#6b7280'
MUTED = '#9ca3af'
LINE = '#e5e7eb'
FIELD = '#f7f8fb'

ACCENT = '#5b5bf0'
ACCENT_DOWN = '#4a4ade'
ACCENT_SOFT = '#f1f2ff'
ACCENT_EDGE = '#b9bbff'

ERR = '#e5484d'

OK_FG = '#0f9d76'
OK_BG = '#e9f8f2'
TAKEN_FG = '#a8aeba'
TAKEN_BG = '#f1f2f5'

FAMILY = host.ui_font()

QSS = """
QWidget { font-family: "%s"; font-size: 10pt; color: %s; }
QLineEdit {
    background: %s; border: 1px solid %s; border-radius: 9px;
    padding: 8px 10px; selection-background-color: %s;
}
QLineEdit:focus { border-color: %s; background: #ffffff; }
QPushButton {
    background: %s; color: #ffffff; border: none; border-radius: 9px;
    padding: 9px 16px; font-weight: bold;
}
QPushButton:hover { background: %s; }
QPushButton:disabled { background: #dfe1ea; color: #ffffff; }
QPushButton[flat="true"] {
    background: transparent; color: %s; font-weight: normal;
}
QPushButton[flat="true"]:hover { background: %s; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #d6d9e3; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QMenu {
    background: #ffffff; border: 1px solid %s; border-radius: 8px; padding: 6px;
}
QMenu::item { padding: 7px 26px 7px 20px; border-radius: 6px; }
QMenu::item:selected { background: %s; }
QMenu::item:disabled { color: %s; }
QMenu::separator { height: 1px; background: %s; margin: 5px 8px; }
""" % (FAMILY, FG, FIELD, LINE, ACCENT_SOFT, ACCENT_EDGE,
       ACCENT, ACCENT_DOWN, SUB, FIELD, BORDER, ACCENT_SOFT, MUTED, LINE)


def font(size=10, bold=False):
    f = QFont(FAMILY)
    f.setPointSize(size)
    f.setBold(bold)
    return f


class WinButton(QPushButton):
    """标题栏右上角的一颗按钮。

    符号用 QPainter 画，不靠字体里的 '✕' '□' 那些字符 —— 不同字体下
    形状、基线都不一样，缩放一变就歪。hover 时才浮出底色。
    """

    LABEL = {'min': '最小化', 'max': '最大化', 'restore': '还原', 'close': '关闭'}

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setFixedSize(30, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self.LABEL.get(kind, ''))
        self.setStyleSheet('QPushButton { border: none; background: transparent; }')

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.setToolTip(self.LABEL.get(kind, ''))
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#f0445a' if self.kind == 'close' else FIELD))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        hot = self._hover and self.kind == 'close'
        p.setPen(QPen(QColor('#ffffff' if hot else (FG if self._hover else MUTED)),
                      1.5, Qt.SolidLine, Qt.RoundCap))
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self.kind == 'min':
            p.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
        elif self.kind == 'max':
            p.drawRoundedRect(QRectF(cx - 5, cy - 4.5, 10, 9), 1.5, 1.5)
        elif self.kind == 'restore':
            p.drawRoundedRect(QRectF(cx - 5.5, cy - 1.5, 8, 7), 1, 1)
            p.drawRoundedRect(QRectF(cx - 1.5, cy - 5.5, 8, 7), 1, 1)
        else:
            p.drawLine(QPointF(cx - 4.5, cy - 4.5), QPointF(cx + 4.5, cy + 4.5))
            p.drawLine(QPointF(cx + 4.5, cy - 4.5), QPointF(cx - 4.5, cy + 4.5))
        p.end()


class CornerGrip(QSizeGrip):
    """右下角的斜纹把手。QSizeGrip 自己会改顶层窗口尺寸，不用手算边缘。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setStyleSheet('background: transparent;')

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(MUTED), 1.4, Qt.SolidLine, Qt.RoundCap))
        for off in (4, 8, 12):
            p.drawLine(QPointF(off, 16), QPointF(16, off))
        p.end()


class CardWindow(QWidget):
    """无边框卡片窗：可拖的标题栏 + 右上角窗口按钮。

    win_buttons 打开后是「最小化 / 最大化 / 关闭」三颗：三个都只动窗口，
    不碰进程 —— 桌宠要留在托盘里。resizable 打开后能拖边缘和右下角把手
    改大小；关掉时仍是原来的固定宽度卡片。

    子类往 self.content 里塞内容（QVBoxLayout 已经建好）。
    """

    EDGE = 10
    CURSORS = {'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
               't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
               'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
               'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor}

    def __init__(self, parent=None, width=520, title='', stretch=True,
                 win_buttons=False, resizable=False, height=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._resizable = resizable
        self._maxed = False
        self._drag = None
        self._resize = None
        self._radius = 14
        self._title = title

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)     # 留给阴影
        self._outer = outer
        self.card = QWidget(self)
        self.card.setObjectName('card')
        outer.addWidget(self.card)

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(28)
        self._shadow.setOffset(0, 6)
        self._shadow.setColor(QColor(17, 24, 39, 46))
        self.card.setGraphicsEffect(self._shadow)
        self._sync_frame()

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(22, 14, 22, 22)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        dot = QLabel()
        dot.setFixedSize(18, 18)
        dot.setPixmap(self._dot())
        head.addWidget(dot)
        brand = QLabel('pet-club')
        brand.setFont(font(10, True))
        brand.setStyleSheet('color: %s;' % SUB)
        head.addWidget(brand)
        head.addStretch(1)

        self.min_btn = self.max_btn = None
        if win_buttons:
            self.min_btn = WinButton('min', self)
            self.max_btn = WinButton('max', self)
            self.min_btn.clicked.connect(self.minimize)
            self.max_btn.clicked.connect(self.toggle_max)
            head.addWidget(self.min_btn)
            head.addWidget(self.max_btn)
        self.close_btn = WinButton('close', self)
        self.close_btn.clicked.connect(self.dismiss)
        head.addWidget(self.close_btn)
        lay.addLayout(head)
        lay.addSpacing(14)

        self.head = head
        self.content = QVBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(10)
        lay.addLayout(self.content)
        if stretch:
            lay.addStretch(1)

        if resizable:
            self.setMinimumSize(420, 320)
            self.resize(width, height or 560)
            self.setMouseTracking(True)
            self.card.setMouseTracking(True)
            self._grip = CornerGrip(self)
            self._place_grip()
        else:
            self.setFixedWidth(width)
            self._grip = None

    @staticmethod
    def _dot():
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(3, 3, 30, 30)
        p.setBrush(QColor('#ffffff'))
        p.drawEllipse(13, 13, 10, 10)
        p.end()
        return pm.scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ---- 窗口按钮 ----

    def minimize(self):
        """收到任务栏；进程照常在托盘里跑。"""
        self.showMinimized()

    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()
        self._sync_state()

    def dismiss(self):
        """「关闭」只是把窗口收起来，不是退出程序。"""
        self.hide()

    def cancel(self):
        self.dismiss()

    def _sync_state(self):
        """最大化时贴边：去掉阴影留白和圆角，否则四周会有一圈灰。"""
        self._maxed = self.isMaximized()
        self._sync_frame()
        if self.max_btn is not None:
            self.max_btn.set_kind('restore' if self._maxed else 'max')
        if self._grip is not None:
            self._grip.setVisible(not self._maxed)

    def _sync_frame(self):
        maxed = getattr(self, '_maxed', False)
        self._outer.setContentsMargins(0 if maxed else 14, 0 if maxed else 14,
                                       0 if maxed else 14, 0 if maxed else 14)
        if maxed:
            css = '#card { background: %s; border: none; border-radius: 0; }' % WINDOW
        else:
            css = ('#card { background: %s; border: 1px solid %s;'
                   ' border-radius: 14px; }' % (WINDOW, BORDER))
        self.card.setStyleSheet(css)
        if getattr(self, '_shadow', None) is not None:
            self._shadow.setEnabled(not maxed)

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self._sync_state()
        super().changeEvent(e)

    # ---- 拖动 / 拖边缘改大小 ----

    def _hit_edge(self, pos):
        if not self._resizable or self._maxed:
            return None
        m = self.EDGE
        r = self.rect()
        left, right = pos.x() <= m, pos.x() >= r.width() - m
        top, bottom = pos.y() <= m, pos.y() >= r.height() - m
        if not (left or right or top or bottom):
            return None
        return (('t' if top else '') + ('b' if bottom else '')
                + ('l' if left else '') + ('r' if right else ''))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        edge = self._hit_edge(e.position().toPoint())
        if edge:
            self._resize = (edge, e.globalPosition().toPoint(), QRect(self.geometry()))
            self._drag = None
        else:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._resize is not None and (e.buttons() & Qt.LeftButton):
            edge, start, geo = self._resize
            d = e.globalPosition().toPoint() - start
            g = QRect(geo)
            if 'l' in edge:
                g.setLeft(min(geo.left() + d.x(), geo.right() - self.minimumWidth()))
            if 'r' in edge:
                g.setWidth(max(self.minimumWidth(), geo.width() + d.x()))
            if 't' in edge:
                g.setTop(min(geo.top() + d.y(), geo.bottom() - self.minimumHeight()))
            if 'b' in edge:
                g.setHeight(max(self.minimumHeight(), geo.height() + d.y()))
            self.setGeometry(g)
            e.accept()
            return
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()
            return
        cur = self.CURSORS.get(self._hit_edge(e.position().toPoint()))
        self.unsetCursor() if cur is None else self.setCursor(cur)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._resize = None
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_grip()

    def _place_grip(self):
        if getattr(self, '_grip', None) is not None:
            self._grip.move(self.width() - 22, self.height() - 22)
            self._grip.raise_()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.dismiss()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        if getattr(self, 'on_close', None) is not None:
            cb, self.on_close = self.on_close, None
            cb()
        super().closeEvent(e)


class RoundButton(QPushButton):
    """主按钮 / 次按钮，只有两种视觉。"""

    def __init__(self, text, cmd=None, primary=True, width=None, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(10, primary))
        if not primary:
            self.setProperty('flat', 'true')
        if width:
            self.setMinimumWidth(width)
        if cmd:
            self.clicked.connect(cmd)
        self.setEnabled(True)

    def set_enabled(self, on):
        self.setEnabled(bool(on))


def field(placeholder='', width=None):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(False)
    if width:
        e.setFixedWidth(width)
    return e


def avatar(role, size):
    """圆形头像：从 idle 立绘上裁头（和 chat.head_square 同一套逻辑）。

    返回 QPixmap（已按 size 抗锯齿缩放、圆形裁剪），拿不到就返回 None。
    """
    path = os.path.join(assets_dir(), 'roles', role, 'idle.png')
    try:
        from PIL import Image, ImageDraw
        im = Image.open(path).convert('RGBA')
    except (OSError, ValueError):
        return None
    box = im.split()[3].getbbox()
    if box:
        im = im.crop(box)
    side = max(8, min(im.width, int(round(im.height * 0.44))))
    cx = im.width / 2.0
    im = im.crop((int(round(cx - side / 2.0)), 0,
                  int(round(cx + side / 2.0)), side))
    big = size * 4
    im = im.resize((big, big), Image.LANCZOS)
    mask = Image.new('L', (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    im.putalpha(mask)
    im = im.resize((size, size), Image.LANCZOS)

    from .windows import to_qimage
    pm = QPixmap.fromImage(to_qimage(im))
    return pm
