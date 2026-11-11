# -*- coding: utf-8 -*-
"""界面基元：配色、全局样式表、卡片窗、按钮、输入框、圆头像。

【为什么自己画卡片窗】
系统标题栏和「轻量、低噪音」的调性不搭。这里用 FramelessWindowHint +
自绘圆角白底，拖动、关闭、Esc 自己补上。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)

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

FAMILY = 'Microsoft YaHei UI'

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


class CardWindow(QWidget):
    """无边框卡片窗：顶部一条可拖动的标题栏 + 右上角关闭。

    子类往 self.content 里塞内容（QVBoxLayout 已经建好）。
    """

    def __init__(self, parent=None, width=520, title='', stretch=True):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(width)
        self._drag = None
        self._radius = 14

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)     # 留给阴影
        self.card = QWidget(self)
        self.card.setObjectName('card')
        self.card.setStyleSheet(
            '#card { background: %s; border: 1px solid %s; border-radius: 14px; }'
            % (WINDOW, BORDER))
        outer.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(17, 24, 39, 46))
        self.card.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(22, 14, 22, 22)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        dot = QLabel()
        dot.setFixedSize(18, 18)
        dot.setPixmap(self._dot())
        head.addWidget(dot)
        brand = QLabel('pet-club')
        brand.setFont(font(10, True))
        brand.setStyleSheet('color: %s;' % SUB)
        head.addWidget(brand)
        head.addStretch(1)
        self.close_btn = QPushButton('✕')
        self.close_btn.setProperty('flat', 'true')
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            'QPushButton { color: %s; font-size: 12pt; border-radius: 13px; }'
            'QPushButton:hover { color: %s; background: %s; }'
            % (MUTED, FG, FIELD))
        self.close_btn.clicked.connect(self.cancel)
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

        self._title = title

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

    # ---- 拖动 ----

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancel()
            return
        super().keyPressEvent(e)

    def cancel(self):
        self.close()

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
