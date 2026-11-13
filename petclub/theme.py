# -*- coding: utf-8 -*-
"""界面基元：配色、全局样式表、卡片窗、按钮、输入框、圆头像。

【为什么自己画卡片窗】
系统标题栏和「轻量、低噪音」的调性不搭。这里用 FramelessWindowHint +
自绘圆角白底，拖动、关闭、Esc 自己补上。
"""
import os

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
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

# 聊天窗的「外框色」：顶部标题带和左侧会话栏共用它，中间没有分隔线，
# 视觉上是连在一起的一整块。
CHROME = '#f1f2f6'
CHROME_LINE = '#e3e5ee'
CHROME_HOVER = '#e7e9f2'
CHROME_SEL = '#ffffff'
CARD_BG = '#f7f8fc'
CARD_LINE = '#eaecf4'

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
    padding: 8px 10px; selection-background-color: %s; selection-color: %s;
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
    background: #ffffff; border: 1px solid %s; border-radius: 8px; padding: 5px;
}
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: %s; }
QMenu::item:disabled { color: %s; }
QMenu::separator { height: 1px; background: %s; margin: 4px 8px; }
""" % (FAMILY, FG, FIELD, LINE, '#dcdfff', '#111827', ACCENT_EDGE,
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

    flat=True 是聊天窗用的「微信式」：直角矩形、不要阴影留白、标题栏是
    一条横跨窗口的浅灰带，内容区直接铺满卡片，由子类自己分栏。
    认领窗等小卡片仍用默认的圆角卡片。

    子类往 self.content 里塞内容（QVBoxLayout 已经建好）。
    """

    EDGE = 10
    CURSORS = {'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
               't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
               'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
               'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor}

    def __init__(self, parent=None, width=520, title='', stretch=True,
                 win_buttons=False, resizable=False, height=None, flat=False):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self._resizable = resizable
        self._flat = flat
        self._flat_bg = WINDOW
        self._maxed = False
        self._drag = None
        self._resize = None
        self._radius = 14
        self._title = title

        if flat:
            # 聊天窗**故意不半透明**：Windows 上的半透明无边框窗口是分层窗口，
            # 一最小化，任务栏缩略图就只剩一块残缺色块。圆角改由 DWM 裁
            # （见 showEvent），窗口本身保持不透明，缩略图才是整张界面。
            pal = self.palette()
            pal.setColor(QPalette.Window, QColor(self._flat_bg))
            self.setPalette(pal)
            self.setAutoFillBackground(True)
        else:
            self.setAttribute(Qt.WA_TranslucentBackground, True)

        pad = 0 if flat else 14
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pad, pad, pad, pad)     # 留给阴影
        self._outer = outer
        self.card = QWidget(self)
        self.card.setObjectName('card')
        outer.addWidget(self.card)

        if flat:
            self._shadow = None
        else:
            self._shadow = QGraphicsDropShadowEffect(self)
            self._shadow.setBlurRadius(28)
            self._shadow.setOffset(0, 6)
            self._shadow.setColor(QColor(17, 24, 39, 46))
            self.card.setGraphicsEffect(self._shadow)
        self._sync_frame()

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(0 if flat else 22, 0 if flat else 14,
                               0 if flat else 22, 0 if flat else 22)
        lay.setSpacing(0)

        if flat:
            self.titlebar = QWidget(self.card)
            self.titlebar.setObjectName('titlebar')
            self.titlebar.setFixedHeight(46)
            # 透明：底色由整窗的外框色给，标题带和左边会话栏才是同一个颜色，
            # 中间不画分隔线，看上去连成一块。
            self.titlebar.setStyleSheet(
                '#titlebar { background: transparent; border: none; }')
            self.card.setMouseTracking(True)
            self.titlebar.setMouseTracking(True)
            head = QHBoxLayout(self.titlebar)
            head.setContentsMargins(16, 0, 10, 0)
            head_holder = self.titlebar

        else:
            self.titlebar = None
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head_holder = None
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
        if flat:
            lay.addWidget(head_holder)      # 一条横跨窗口的标题带
        else:
            lay.addLayout(head)
            lay.addSpacing(14)

        self.head = head
        self.content = QVBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(0 if flat else 10)
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
        if getattr(self, '_flat', False) and self.isVisible():
            host.round_corners(self, not self._maxed)
        if self.max_btn is not None:
            self.max_btn.set_kind('restore' if self._maxed else 'max')
        if self._grip is not None:
            self._grip.setVisible(not self._maxed)

    def _sync_frame(self):
        maxed = getattr(self, '_maxed', False)
        flat = getattr(self, '_flat', False)
        pad = 0 if (maxed or flat) else 14
        self._outer.setContentsMargins(pad, pad, pad, pad)
        if maxed:
            css = ('#card { background: %s; border: none;'
                   ' border-radius: 0; }' % self._flat_bg)
        elif flat:
            css = ('#card { background: %s; border: 1px solid %s;'
                   ' border-radius: 12px; }' % (self._flat_bg, CHROME_LINE))
        else:
            css = ('#card { background: %s; border: 1px solid %s;'
                   ' border-radius: 14px; }' % (WINDOW, BORDER))
        self.card.setStyleSheet(css)
        # 构造期连续设两次样式，Qt 不会立刻重算已经算好的那一份：聊天窗
        # 首次 show 时外框停在初始化时的白底（标题带发白），最小化再恢复
        # 才对 —— 因为那次改动才真正触发重排。这里手动重刷一次，首帧就对。
        st = self.card.style()
        st.unpolish(self.card)
        st.polish(self.card)
        self.card.update()
        if getattr(self, '_shadow', None) is not None:
            self._shadow.setEnabled(not maxed)

    def set_flat_bg(self, color):
        """改 chat 窗那种「一整块外框色」的底。配色在外框色和白色之间切换用。"""
        self._flat_bg = color
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(color))
        self.setPalette(pal)
        self._sync_frame()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self._sync_state()
        super().changeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        # 窗口句柄要到第一次 show 才存在；圆角交给系统裁（Win11 才有，
        # Win10 上 round_corners 返回 False，窗口就是直角）。
        if getattr(self, '_flat', False):
            host.round_corners(self, not self._maxed)

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
    """圆形头像：把整只角色（全身）缩进圆里，和网页版保持同一观感。

    以前裁的是「头」：只看得到脸，会议栏里分不清谁是谁。现在按 alpha
    包围盒取整身，等比缩进一个正方形画布，再套圆形蒙版。
    返回 QPixmap（已按 size 抗锯齿缩放），拿不到就返回 None。
    """
    path = os.path.join(assets_dir(), 'roles', role, 'idle.png')
    try:
        from PIL import Image, ImageChops, ImageDraw
        im = Image.open(path).convert('RGBA')
    except (OSError, ValueError):
        return None
    box = im.split()[3].getbbox()
    if box:
        im = im.crop(box)
    big = size * 4
    canvas = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    # 和网页版 .avatar 一样的浅灰圆底 + 一圈细边：全身图放在圆里，
    # 不再是一张透明背景的图直接贴在会话栏上。
    ring = max(1, big // max(1, size))
    half = ring / 2.0
    draw.ellipse((half, half, big - 1 - half, big - 1 - half),
                 fill=(238, 240, 245, 255),
                 outline=(229, 231, 235, 255), width=ring)
    # 留一圈边，圆不会切到头顶和脚底
    fit = big * 0.84
    k = min(fit / max(1, im.width), fit / max(1, im.height))
    w = max(1, int(round(im.width * k)))
    h = max(1, int(round(im.height * k)))
    im = im.resize((w, h), Image.LANCZOS)
    canvas.alpha_composite(im, ((big - w) // 2, (big - h) // 2))
    mask = Image.new('L', (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    # 乘上去，不要直接 putalpha：直接替换会把角色周围的透明底
    # 在圆里变成不透明的黑块。
    canvas.putalpha(ImageChops.multiply(canvas.getchannel('A'), mask))
    canvas = canvas.resize((size, size), Image.LANCZOS)

    from .windows import to_qimage
    pm = QPixmap.fromImage(to_qimage(canvas))
    return pm
