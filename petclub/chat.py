# -*- coding: utf-8 -*-
"""聊天面板。

一块圆角外框，里面分成两栏：
  左：会话栏（搜索 + 会话列表）—— 和顶部的标题带**同一个底色**，
      中间不画分隔线，看上去是一整块外框
  右：白色的会话平面（标题 / 消息 / 输入），左上角一个小圆角

窗口本身不半透明：Windows 上的半透明无边框窗是分层窗口，最小化以后
任务栏缩略图会花掉。圆角改由系统裁（Win11 的 DWM），缩略图才是整张界面。

输入区是一个浅灰的圆角卡片：上面一排快捷互动，中间多行输入框，下面
表情 / 文件两颗图标和发送。图片和文件都**先落进输入框**（变成一张张待发
附件），点发送才真的出去 —— 和微信一样。

数据仍然不在这个类里：`App.chat_log` 是唯一一份，窗口只是它的视图；
未读也是 `App.unread`（共享字典），切会话时清零，托盘闪的就是它。
"""
import io
import os
import time

from PIL import Image
from PySide6.QtCore import (QBuffer, QIODevice, QPoint, QPointF, QRectF, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics,
                           QGuiApplication, QIcon, QPainter, QPen, QPixmap,
                           QTextOption)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from . import actions, host, media
from .config import (PET_SIZE_NAME, PET_SIZE_ORDER, ROLE_NAME, ROLES,
                     assets_dir)
from .theme import (ACCENT, ACCENT_EDGE, ACCENT_SOFT, BORDER, CARD_BG, CARD_LINE,
                    CHROME, CHROME_HOVER, CHROME_LINE, CHROME_SEL, FG, MUTED, SUB,
                    CardWindow, avatar, font)
from .windows import to_qimage

CONV_ALL = 'all'

WIDTH, HEIGHT = 860, 620
MIN_W, MIN_H = 680, 470
LIST_W = 252                # 会话栏宽度
ROW_H = 62
HEAD_H = 56                 # 右侧会话标题行
PASTE_MAX = 3               # 一次最多带几个附件：多了就是刷屏
GAP_TIME = 300.0
BUBBLE_MAX = 460                # 气泡最宽（窗口够宽时才吃得到，窄了按聊天区收）
CELL = 56                   # 拾取面板一格；表情和贴纸都按这个放大
CELL_GAP = 2                # 格子之间的缝（算面板尺寸时要跟着一起算）
COLS = 8
CHIP_H = 22                 # 互动药丸的高度：比正文小一号，挤在输入卡片顶上
CHIP_GAP = 4                # 药丸之间的缝
ACT_ROWS = 2                # 最多排两行，排不下就换行，不再收进「更多」
COMPOSER_H = 72             # 多行输入框：三行多文字
PREVIEW_BOX = 200           # 悬停预览的逻辑边长
PREVIEW_DPR = 2.0           # 位图按 2x 物理像素生成，高分屏上不糊

TALK_BG = '#ffffff'         # 右侧一整块平面
BUBBLE_ME = ACCENT
BUBBLE_ME_FG = '#ffffff'
BUBBLE_OTH = '#f2f3f8'
BUBBLE_OTH_FG = '#111827'
SEL_BG = '#dcdfff'          # 选中背景：浅紫，压在白气泡上也看得清
SEL_FG = '#111827'          # 选中文字保持深色 —— 系统默认会给白字，在浅底上等于隐形
TIME_FG = '#a6abb8'

SIDE_BG = CHROME            # 会话栏 = 顶部标题带同一个颜色
SIDE_SEL = CHROME_SEL
SIDE_HOVER = CHROME_HOVER

# 头像色块按 ROLES 的顺序取色，一个身份一个颜色 —— 加角色就往后面接一个，
# 别复用前面的：两个身份撞色时，会话栏里只能靠首字分辨人。
AVATAR_COLORS = ('#5b5bf0', '#0f9d76', '#e08c2a', '#d6455d', '#2b8fd6',
                 '#8a4fcf')

# 系统 UI 字体（微软雅黑 / 苹方）不含彩色 emoji 字形，拿它画 😀 会落到
# 一套很小的位图 fallback 上，放大就糊。emoji 一律走各平台自己的彩色字体。
EMOJI_FAMILY = ('Apple Color Emoji' if host.IS_MAC else
                'Segoe UI Emoji' if host.IS_WIN else host.ui_font())


def emoji_font(size):
    f = QFont(EMOJI_FAMILY)
    f.setPointSizeF(float(size))
    return f


def conv_pix(key, size):
    """会话头像：单聊裁角色立绘的头，群聊画一个圆底「群」。

    角色素材缺失时也画圆底首字，不会留一个空白洞。
    """
    if key != CONV_ALL:
        pm = avatar(key, size)
        if pm is not None:
            return pm
        text = ROLE_NAME.get(key, key)[:1]
        idx = ROLES.index(key) if key in ROLES else 0
        color = AVATAR_COLORS[idx % len(AVATAR_COLORS)]
    else:
        text, color = '群', ACCENT

    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size * 2 - 1, size * 2 - 1)
    p.setPen(QColor('#ffffff'))
    f = QFont(host.ui_font())
    f.setPixelSize(max(9, int(size * 0.95)))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, size * 2, size * 2), Qt.AlignCenter, text)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return pm


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

    TIPS = {'emoji': '表情 / 表情包', 'file': '发送图片或文件'}

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
            # 一张带折角的文件：图片也走这颗按钮，点一下直接开系统文件框
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
    """快捷互动的一颗：白色小药丸，压在输入卡片的浅灰底上。

    没有冷却、没有角标、没有禁用态 —— 点得动就真的会动。
    """

    def __init__(self, label, cmd, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setText(label)
        self.setFont(font(7))
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QToolButton { border: none; background: transparent; }')
        w = QFontMetrics(self.font()).horizontalAdvance(label) + 14
        self.setFixedSize(max(34, w), CHIP_H)
        self.clicked.connect(cmd)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2.0
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(r, rad, rad)
            _zoom(p, self, 1.06)
        else:
            p.setPen(QPen(QColor(CARD_LINE), 1))
            p.setBrush(QColor('#ffffff'))
            p.drawRoundedRect(r, rad, rad)
        p.setPen(QColor('#ffffff' if self._hover else SUB))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class BubbleText(QTextEdit):
    """气泡里的文字：只读、自动换行、能选中复制。

    不用 QLabel：它的自动换行只在「词边界」断，一长串英文、路径或 URL
    没有空格就横着被裁掉，看起来就是「发出去的字显示不全」。QTextEdit
    可以设 WrapAtWordBoundaryOrAnywhere，中文、英文、链接都断得开。

    顺带把选中配色也管住：系统默认的深蓝压在气泡上很扎眼，输入框那边
    还会把选中的字染成白色，浅底上几乎看不见。
    """

    PAD_X, PAD_Y = 13, 9

    def __init__(self, text, mine, parent=None, max_w=None):
        super().__init__(parent)
        self._max = int(max_w) if max_w else BUBBLE_MAX
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setFont(font(10))
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.document().setDocumentMargin(0)
        self.setCursor(Qt.IBeamCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        if mine:
            self.setStyleSheet(
                'QTextEdit { background: %s; color: %s; padding: %dpx %dpx;'
                ' border: none; border-radius: 14px; border-top-right-radius: 4px;'
                ' selection-background-color: %s; selection-color: %s; }'
                % (BUBBLE_ME, BUBBLE_ME_FG, self.PAD_Y, self.PAD_X,
                   '#3f3fd6', '#ffffff'))
        else:
            self.setStyleSheet(
                'QTextEdit { background: %s; color: %s; padding: %dpx %dpx;'
                ' border: none; border-radius: 14px; border-top-left-radius: 4px;'
                ' selection-background-color: %s; selection-color: %s; }'
                % (BUBBLE_OTH, BUBBLE_OTH_FG, self.PAD_Y, self.PAD_X,
                   SEL_BG, SEL_FG))
        self.setPlainText(text)
        self._fit()

    def set_max_width(self, w):
        """窗口变宽/变窄时重算一次；宽度没变就什么都不做。"""
        w = int(w) if w else BUBBLE_MAX
        if w == self._max:
            return
        self._max = w
        self._fit()

    def _fit(self):
        """宽度按最长一行定（上限 self._max），高度按排完版之后的文档算。

        高度必须用同一份 document 算 —— 换成 fontMetrics 估算，中英混排
        和换行位置差一行，最后一行就会被切掉。
        """
        fm = QFontMetrics(self.font())
        natural = 0
        for line in self.toPlainText().split('\n'):
            natural = max(natural, fm.horizontalAdvance(line))
        w = min(self._max, natural + self.PAD_X * 2 + 2)
        self.setFixedWidth(w)
        doc = self.document()
        doc.setTextWidth(w - self.PAD_X * 2)
        h = int(doc.size().height() + 0.999) + self.PAD_Y * 2
        self.setFixedHeight(max(h, fm.height() + self.PAD_Y * 2))

    def wheelEvent(self, e):
        # 气泡自己不吃滚轮：消息多了要能直接把整段聊天滚上去
        e.ignore()

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction('复制').triggered.connect(self.copy)
        m.addAction('全选').triggered.connect(self.selectAll)
        m.exec(self.mapToGlobal(pos))


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


class StatePill(QWidget):
    """标题行里那枚状态胶囊：一颗小圆点 + 一行小字。

    以前是「绿底绿字」的标签，压在一块纯白平面上，颜色和整套界面都不搭。
    现在底色统一成外框那一档浅灰，状态只靠圆点的颜色区分 —— 在线是
    绿的、空闲是灰的，一眼能看出来，但不跟标题抢注意力。
    """

    BG = CHROME
    DOT_LIVE = '#22b07d'
    DOT_OFF = '#c4c9d6'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ''
        self._live = False
        self.setFont(font(8))
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_state(self, text, live):
        text, live = text or '', bool(live)
        if (text, live) == (self._text, self._live):
            return
        self._text, self._live = text, live
        # 文字从 20px 起（左边留出圆点），右边再留 11px
        fm = QFontMetrics(self.font())
        self.setFixedWidth(fm.horizontalAdvance(text) + 31)
        self.update()

    def text(self):
        return self._text

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.BG))
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        p.setBrush(QColor(self.DOT_LIVE if self._live else self.DOT_OFF))
        p.drawEllipse(QPointF(11, r.center().y()), 3, 3)
        p.setPen(QColor(SUB))
        p.setFont(self.font())
        p.drawText(QRectF(20, 0, self.width() - 24, self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._text)
        p.end()


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


class ImageViewer(QFrame):
    """点开聊天里的图片：置顶、原图、滚轮缩放、按住拖动。

    原来是把气泡里那张 240 宽的缩略图塞进一个普通窗口 —— 一放大就糊，
    还因为聊天窗是置顶的，预览直接被压在下面。这里改用原图重新解码、
    显式置顶，滚轮缩放（1x ~ 6x），按住能平移，单击 / Esc 关掉。
    """

    MAX_ZOOM = 6.0
    BG = '#0f1115'

    def __init__(self, owner=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.owner = owner
        self._pix = None
        self._dpr = 1.0
        self._base = 1.0
        self._zoom = 1.0
        self._off = QPointF(0, 0)
        self._drag = None
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

    # ---- 打开 ----

    def show_image(self, pix, near=None):
        if pix is None or pix.isNull():
            return
        self._pix = pix
        scr = (near.screen() if near is not None else
               QGuiApplication.screenAt(QCursor.pos())
               or QGuiApplication.primaryScreen()).availableGeometry()
        self._dpr = pix.devicePixelRatio() or 1.0
        w = pix.width() / self._dpr
        h = pix.height() / self._dpr
        # 1x = 原图像素 1:1（小图不硬拉大，大图缩到屏幕里）
        self._base = min(1.0, scr.width() * 0.86 / max(1.0, w),
                         scr.height() * 0.84 / max(1.0, h))
        self._zoom = 1.0
        self._off = QPointF(0, 0)
        self.resize(max(240, int(w * self._base)),
                    max(180, int(h * self._base)))
        ox = scr.left() + (scr.width() - self.width()) // 2
        oy = scr.top() + (scr.height() - self.height()) // 2
        self.move(ox, oy)
        self.show()
        # 桌宠窗口本身是置顶的，普通 raise_() 抢不过原生置顶带；
        # 这里直接把自己插到置顶带最前面，预览才不会被人物盖住。
        if not host.raise_top(self):
            self.raise_()
        self.activateWindow()

    # ---- 交互 ----

    def _scale(self):
        return self._base * self._zoom

    def _clamp_off(self):
        """放大后别让图被拖到窗口外面只剩一条边。"""
        if self._pix is None:
            return
        s = self._scale()
        vw = self._pix.width() / self._dpr * s
        vh = self._pix.height() / self._dpr * s
        mx = max(0.0, (vw - self.width()) / 2.0)
        my = max(0.0, (vh - self.height()) / 2.0)
        self._off = QPointF(max(-mx, min(mx, self._off.x())),
                            max(-my, min(my, self._off.y())))

    def wheelEvent(self, e):
        step = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self._zoom = max(1.0, min(self.MAX_ZOOM, self._zoom * step))
        self._clamp_off()
        self.update()
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.position(), QPointF(self._off))
            self.setCursor(Qt.ClosedHandCursor)
        elif e.button() == Qt.RightButton:
            self.hide()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        p0, off0 = self._drag
        self._off = QPointF(off0.x() + e.position().x() - p0.x(),
                            off0.y() + e.position().y() - p0.y())
        self._clamp_off()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        moved = (abs(e.position().x() - self._drag[0].x())
                 + abs(e.position().y() - self._drag[0].y()))
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)
        if moved < 3:               # 只是点了一下：当「关闭」
            self.hide()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        if e.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self._zoom = min(self.MAX_ZOOM, self._zoom * 1.2)
        elif e.key() == Qt.Key_Minus:
            self._zoom = max(1.0, self._zoom / 1.2)
        else:
            super().keyPressEvent(e)
            return
        self._clamp_off()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.BG))
        if self._pix is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            s = self._scale()
            w = self._pix.width() / self._dpr * s
            h = self._pix.height() / self._dpr * s
            p.drawPixmap(QRectF((self.width() - w) / 2 + self._off.x(),
                                (self.height() - h) / 2 + self._off.y(),
                                w, h), self._pix, QRectF(self._pix.rect()))
        # 底部一行浅色提示，位置固定，不跟着图动
        p.setFont(font(8))
        p.setPen(QColor('#8b90a0'))
        p.drawText(QRectF(0, self.height() - 26, self.width(), 20),
                   Qt.AlignCenter,
                   '滚轮缩放 · 按住拖动 · 单击关闭')
        p.end()


class ConvRow(QWidget):
    """会话栏的一行：圆角胶囊，选中的那一行是白色（贴着右边的白面板）。"""

    clicked = Signal(str)

    def __init__(self, key, name, me, parent=None):
        super().__init__(parent)
        self.key = key
        self.name = name
        self.selected = False
        self._hover = False
        self.unread = 0
        self.pix = conv_pix(key, 38)
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

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
            e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if self.selected:
            p.setPen(QPen(QColor(CHROME_LINE), 1))
            p.setBrush(QColor(SIDE_SEL))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        elif self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(SIDE_HOVER))
            p.drawRoundedRect(r, 10, 10)

        ax, ay, s = 10, (ROW_H - 38) // 2, 38
        if self.pix is not None:
            p.drawPixmap(ax, ay, self.pix)

        x = ax + s + 10
        right = self.width() - 10
        p.setPen(QColor(MUTED))
        p.setFont(font(8))
        tw = p.fontMetrics().horizontalAdvance(self.stamp)
        p.drawText(QRectF(right - tw, 11, tw, 13), Qt.AlignRight | Qt.AlignVCenter,
                   self.stamp)

        p.setPen(QColor(FG))
        p.setFont(font(10, True))
        name_w = max(30, right - tw - 6 - x)
        name = p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(name_w))
        p.drawText(QRectF(x, 10, name_w, 18), Qt.AlignLeft | Qt.AlignVCenter, name)

        badge_w = 0
        if self.unread:
            txt = str(self.unread) if self.unread < 10 else '9+'
            p.setFont(font(7, True))
            bw = max(18, p.fontMetrics().horizontalAdvance(txt) + 10)
            badge_w = bw + 6
            bx = right - bw
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#f0445a'))
            p.drawRoundedRect(QRectF(bx, 33, bw, 16), 8, 8)
            p.setPen(QColor('#ffffff'))
            p.drawText(QRectF(bx, 33, bw, 16), Qt.AlignCenter, txt)

        p.setPen(QColor(MUTED))
        p.setFont(font(8))
        pw = max(20, right - badge_w - x)
        text = p.fontMetrics().elidedText(self.preview, Qt.ElideRight, int(pw))
        p.drawText(QRectF(x, 32, pw, 17), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()


class MeBar(QFrame):
    """会话栏底部那条「我是谁」：点一下弹出身份菜单。

    做成可点，是因为「桌宠大小 / 同步远程互动 / 换一个身份 / 退出」原来
    只藏在桌面角色的右键和托盘里 —— 人坐在聊天窗里的时候，谁也想不起来
    去那儿找。悬停给一层浅色底，不然看不出它是能点的。
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('mebar')
        self.setStyleSheet('#mebar { background: transparent; }')
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        if not self._hover:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SIDE_HOVER))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(8, 3, -8, -3), 8, 8)
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
            ' padding: 2px 4px; selection-background-color: %s;'
            ' selection-color: %s; }' % (SEL_BG, SEL_FG))
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


class AttachChip(QFrame):
    """输入框里的一张待发附件：缩略图（如果有）+ 文件名 + 删除。

    选完文件不立刻发，而是变成这样一张小卡片落在输入框上方 —— 和微信一样，
    先看清选对没有、配一句话，再一起点发送。
    """

    removed = Signal(object)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName('att')
        self.setStyleSheet(
            '#att { background: #ffffff; border: 1px solid %s;'
            ' border-radius: 9px; }' % CARD_LINE)
        h = QHBoxLayout(self)
        h.setContentsMargins(5, 3, 3, 3)
        h.setSpacing(6)

        pix = item.get('pix')
        if pix is not None and not pix.isNull():
            lb = QLabel(self)
            lb.setPixmap(pix.scaled(24, 24, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation))
            lb.setFixedSize(24, 24)
            lb.setStyleSheet('background: transparent; border: none;')
            h.addWidget(lb)

        name = item.get('name') or '文件'
        f = font(8)
        lb = QLabel(QFontMetrics(f).elidedText(name, Qt.ElideMiddle, 110), self)
        lb.setFont(f)
        lb.setStyleSheet('color: %s; background: transparent; border: none;' % SUB)
        lb.setToolTip(name)
        h.addWidget(lb)

        x = QToolButton(self)
        x.setText('×')
        x.setFont(font(10))
        x.setFixedSize(18, 18)
        x.setCursor(Qt.PointingHandCursor)
        x.setToolTip('移除')
        x.setStyleSheet(
            'QToolButton { border: none; background: transparent; color: %s;'
            ' border-radius: 9px; }'
            'QToolButton:hover { background: #f1f2f7; color: #f0445a; }' % MUTED)
        x.clicked.connect(lambda: self.removed.emit(self.item))
        h.addWidget(x)
        self.setFixedHeight(32)


class ChatWindow(CardWindow):
    """一个窗口，六（N）个会话。"""

    def __init__(self, parent=None, on_send=None, on_close=None, me=None,
                 is_online=None, log=None, on_media=None, unread=None,
                 on_action=None, on_leave=None, on_quit=None,
                 on_pet_size=None, pet_size='default',
                 remote_on=True, on_remote=None, remote_hint=None):
        super().__init__(parent, WIDTH, 'pet-club', stretch=False,
                         win_buttons=True, resizable=True, height=HEIGHT,
                         flat=True)
        self.setMinimumSize(MIN_W, MIN_H)
        self.on_send = on_send
        self.on_media = on_media
        self.on_close = on_close
        self.on_action = on_action
        self.on_leave = on_leave
        self.on_quit = on_quit
        self.on_pet_size = on_pet_size
        self.on_remote = on_remote
        self.remote_hint = remote_hint or (lambda: '')
        self.remote_on = remote_on
        self.pet_size = pet_size
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
        self._pick_top = None       # 面板上边缘，翻页时尽量不动
        self._page = 'emoji'
        self._top_on = True
        self._preview = None
        self._viewer = None
        self._me_menu = None
        self._me_menu_prev = None   # 刚关掉的身份菜单，多留一拍再放
        self._chips = {}
        self._act_rows = []
        self._act_avail = -1
        self._reflow_pending = False
        self._attach = []           # 还没发出去的附件（图片 / 文件）
        self._attach_chips = []

        self._build()
        self.refresh_list()
        self.switch(CONV_ALL)
        QTimer.singleShot(0, self._reflow_actions)

    # ---------------- 构建 ----------------

    def _search_icon(self):
        """搜索框里的那颗小放大镜。自绘，不依赖字体里的符号。"""
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(MUTED), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(QRectF(9, 9, 15, 15))
        p.drawLine(QPointF(22, 22), QPointF(28, 28))
        p.end()
        return QIcon(pm)

    def _build(self):
        """两栏：左边会话栏，右边白色会话平面。

        整个窗口是一块圆角外框：标题带和会话栏共用同一个外框色，中间没有
        分隔线（看上去是一整块），右边那块白色平面只有左上角一个圆角，
        和左栏之间靠色差分开。
        """
        self.set_flat_bg(CHROME)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        left = QWidget()
        left.setObjectName('side')
        left.setFixedWidth(LIST_W)
        left.setStyleSheet('#side { background: %s; }' % SIDE_BG)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        search_bar = QWidget(left)
        sb = QHBoxLayout(search_bar)
        sb.setContentsMargins(14, 2, 14, 10)
        sb.setSpacing(0)
        self.search = QLineEdit(search_bar)
        self.search.setPlaceholderText('搜索会话或消息')
        self.search.setFont(font(9))
        self.search.setFixedHeight(34)
        self.search.setStyleSheet(
            'QLineEdit { background:#ffffff; border:1px solid %s;'
            ' border-radius:10px; padding: 0 12px 0 32px; }'
            'QLineEdit:focus { border-color: %s; }' % (CHROME_LINE, ACCENT_EDGE))
        self.search.addAction(self._search_icon(), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._on_search)
        sb.addWidget(self.search, 1)
        lv.addWidget(search_bar)

        self.list_box = QVBoxLayout()
        self.list_box.setContentsMargins(8, 0, 8, 10)
        self.list_box.setSpacing(2)
        lv.addLayout(self.list_box)
        lv.addStretch(1)

        # 底部「我是谁」：点一下换身份 / 退出
        me_bar = MeBar(left)
        self.me_bar = me_bar
        mb = QHBoxLayout(me_bar)
        mb.setContentsMargins(14, 8, 14, 12)
        mb.setSpacing(8)
        self.me_av = QLabel(me_bar)
        self.me_av.setFixedSize(26, 26)
        if self.me:
            self.me_av.setPixmap(conv_pix(self.me, 26))
        mb.addWidget(self.me_av)
        me_lb = QLabel('%s · 我' % ROLE_NAME.get(self.me, self.me or '')
                       if self.me else '还没认领身份', me_bar)
        me_lb.setFont(font(8))
        me_lb.setStyleSheet('color: %s; background: transparent;' % SUB)
        mb.addWidget(me_lb)
        mb.addStretch(1)
        me_bar.setToolTip('点一下：桌宠大小 / 同步远程互动 / 换身份 / 退出')
        me_bar.clicked.connect(self._show_me_menu)
        lv.addWidget(me_bar)
        body.addWidget(left)

        right = QWidget()
        right.setObjectName('talk')
        # 只圆左上角：右边 / 下边贴着窗口边，由系统裁外框的圆角。
        right.setStyleSheet(
            '#talk { background: %s; border-top-left-radius: 14px; }' % TALK_BG)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        self.head_bar = QFrame(right)
        self.head_bar.setObjectName('talkhead')
        self.head_bar.setFixedHeight(HEAD_H)
        self.head_bar.setStyleSheet(
            '#talkhead { background: transparent; border: none;'
            ' border-bottom: 1px solid #f1f2f6; }')
        hh = QHBoxLayout(self.head_bar)
        hh.setContentsMargins(22, 0, 20, 0)
        hh.setSpacing(10)
        self.head_av = QLabel('')
        self.head_av.setFixedSize(30, 30)
        hh.addWidget(self.head_av)
        self.title = QLabel('')
        self.title.setFont(font(12, True))
        hh.addWidget(self.title)
        self.state_lb = StatePill()
        hh.addWidget(self.state_lb, 0, Qt.AlignVCenter)
        hh.addStretch(1)
        rv.addWidget(self.head_bar)

        self.scroll = QScrollArea(right)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setStyleSheet(
            'QScrollArea { background: %s; border: none; }' % TALK_BG)
        self.msg_host = QWidget()
        self.msg_host.setStyleSheet('background: %s;' % TALK_BG)
        self.msg_box = QVBoxLayout(self.msg_host)
        self.msg_box.setContentsMargins(22, 16, 22, 18)
        self.msg_box.setSpacing(10)
        self.msg_box.addStretch(1)
        self.scroll.setWidget(self.msg_host)
        rv.addWidget(self.scroll, 1)

        # 「贴底」状态：用户停在底部时，新消息、窗口变形、输入框长高都跟着
        # 贴住；往上翻看历史时就别把他拽回来。
        # 关键在 rangeChanged：消息刚插进布局的那一刻 maximum 还是旧值，
        # singleShot(0) 去 setValue(max) 会停在半路，刚发出去的那条就被切掉了。
        self._pinned = True
        self._auto_scroll = False
        sb = self.scroll.verticalScrollBar()
        sb.rangeChanged.connect(self._on_scroll_range)
        sb.valueChanged.connect(self._on_scroll_value)

        # 反馈提示只有出事时才占一行（平时收起来，不占位置）
        self.note = QLabel('')
        self.note.setFont(font(8))
        self.note.setStyleSheet('color: %s; background: %s;' % (SUB, TALK_BG))
        self.note.setFixedHeight(14)
        self.note.hide()
        rv.addWidget(self.note)

        rv.addWidget(self._build_inbox())
        body.addWidget(right, 1)
        self.side = left
        self.talk = right
        self.content.addLayout(body)
        self.setAcceptDrops(True)

    def _build_actions(self, parent):
        """输入卡片最上面的一排快捷互动。

        白色小药丸 + 浅灰底，看上去和输入框是一套东西。宽度不够就换行，
        最多两行 —— 不再有「更多」：藏在二级浮层里的按钮，点之前谁也不知道
        里面有什么，还不如直接摆出来。
        """
        bar = QWidget(parent)
        bar.setObjectName('actbar')
        bar.setStyleSheet('#actbar { background: transparent; }')

        col = QVBoxLayout(bar)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(CHIP_GAP)
        self._act_rows = []
        for _ in range(ACT_ROWS):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(CHIP_GAP)
            self._act_rows.append(row)
            col.addLayout(row)

        for key in actions.CHAT_ORDER:
            spec = actions.get(key)
            cmd = lambda _=False, k=key: self._on_action(k)
            chip = ActionChip(spec['label'], cmd, bar)
            chip.setToolTip(spec.get('hint', ''))
            chip.hide()
            self._chips[key] = chip

        # 先按一行占位：多数窗口够宽，_reflow_actions 真需要两行时再长高
        bar.setFixedHeight(CHIP_H)
        self.act_bar = bar
        return bar

    def _build_inbox(self):
        """输入区：一张浅灰圆角卡片，从上到下是「附件条 / 互动条 / 输入框 / 工具行」。

        上面用 1px 细线和消息区分开；卡片本身不再是白底，白色留给消息，
        输入框退成一层浅灰，层次感就出来了。
        """
        inbox = QFrame()
        inbox.setObjectName('inbox')
        inbox.setStyleSheet(
            '#inbox { background: %s; border: none;'
            ' border-top: 1px solid #f1f2f6; border-radius: 0; }' % TALK_BG)
        iv = QVBoxLayout(inbox)
        iv.setContentsMargins(12, 8, 12, 12)
        iv.setSpacing(0)

        card = QFrame(inbox)
        card.setObjectName('composer')
        card.setStyleSheet(
            '#composer { background: %s; border: 1px solid %s;'
            ' border-radius: 14px; }' % (CARD_BG, CARD_LINE))
        cv = QVBoxLayout(card)
        cv.setContentsMargins(10, 8, 10, 8)
        cv.setSpacing(6)

        # 待发附件：有内容时才出现，一张张可删除的小卡片
        self.attach_host = QWidget(card)
        self.attach_lay = QHBoxLayout(self.attach_host)
        self.attach_lay.setContentsMargins(2, 0, 2, 0)
        self.attach_lay.setSpacing(6)
        self.attach_lay.addStretch(1)
        self.attach_host.hide()
        cv.addWidget(self.attach_host)

        cv.addWidget(self._build_actions(card))

        line = QFrame(card)
        line.setFixedHeight(1)
        line.setStyleSheet('background: %s; border: none;' % CARD_LINE)
        cv.addWidget(line)

        self.field = Composer(card, self._paste)
        self.field.send_requested.connect(self.send)
        cv.addWidget(self.field)

        foot = QHBoxLayout()
        foot.setContentsMargins(2, 0, 0, 0)
        foot.setSpacing(4)
        self.icon_btn = {}
        for kind in ('emoji', 'file'):
            b = ToolIcon(kind, card)
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

        self.send_btn = QPushButton('发送', card)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFixedSize(78, 30)
        self.send_btn.setFont(font(9, True))
        self.send_btn.setStyleSheet(
            'QPushButton { background: %s; color: #ffffff; border: none;'
            ' border-radius: 15px; padding: 0; }'
            'QPushButton:hover { background: %s; }'
            'QPushButton:disabled { background: #dfe1ea; }'
            % (ACCENT, '#4a4ade'))
        self.send_btn.clicked.connect(self.send)
        foot.addWidget(self.send_btn)
        cv.addLayout(foot)

        iv.addWidget(card)
        self.inbox = inbox
        self.composer_card = card
        return inbox

    # ---------------- 快捷互动 ----------------

    def _show_me_menu(self):
        """左下角头像的菜单：桌宠大小 / 同步远程互动 / 换一个身份 / 退出。

        「同步远程互动」和托盘里那一项是同一个开关，只是人坐在聊天窗里时
        不必再去右下角找托盘图标。勾选状态每次开菜单时按当前值重新读。

        菜单没有 parent 托底，引用得自己攥着；上一个多留一拍再放 ——
        点菜单项的 triggered 还在它自己的派发栈上。
        """
        m = QMenu(self)
        a = m.addAction('%s · 当前身份' % ROLE_NAME.get(self.me, self.me or ''))
        a.setEnabled(False)
        m.addSeparator()
        sub = m.addMenu('桌宠大小')
        for level in PET_SIZE_ORDER:
            act = sub.addAction(PET_SIZE_NAME.get(level, level))
            act.setCheckable(True)
            act.setChecked(level == self.pet_size)
            act.triggered.connect(
                lambda _=False, lv=level: self._on_pet_size(lv))
        m.addSeparator()
        act = m.addAction('同步远程互动')
        act.setCheckable(True)
        act.setChecked(self.remote_on)
        act.setToolTip(self.remote_hint())
        act.triggered.connect(self._on_remote)
        m.addSeparator()
        m.addAction('换一个身份').triggered.connect(self._on_leave)
        m.addAction('退出').triggered.connect(self._on_quit)
        self._me_menu_prev, self._me_menu = self._me_menu, m
        m.adjustSize()
        at = self.me_bar.mapToGlobal(QPoint(0, 0))
        m.popup(QPoint(at.x() + 12, at.y() - m.height() - 4))

    def _on_pet_size(self, level):
        if level == self.pet_size:
            return
        self.pet_size = level
        if self.on_pet_size is not None:
            self.on_pet_size(level)

    def _on_remote(self, on=False):
        """头像菜单里勾 / 取消「同步远程互动」。

        真正落盘的是 App（托盘那一项走同一条路），这里只按它的返回值
        更新本地状态 —— 两边都以 App.remote_on 为准，不各记一份。
        """
        if self.on_remote is None:
            return
        self.remote_on = bool(self.on_remote(on))

    def _on_leave(self):
        if self.on_leave is not None:
            self.on_leave()

    def _on_quit(self):
        if self.on_quit is not None:
            self.on_quit()

    def _action_target(self):
        return None if self.key == CONV_ALL else self.key

    def _on_action(self, key):
        """点了互动条上的一颗：动作立刻生效，提示行一个字都不用写。

        动作的反馈就是角色本身动了一下 —— 再补一句「已发送…」是废话。
        """
        if self.on_action is None:
            return
        self.on_action(key, self._action_target())

    def _reflow_actions(self):
        """按宽度把药丸铺进一两行。

        先填满第一行再换第二行；窗口拉宽会自然回缩成一行。两行都放不下
        （窗口被拉到极窄）时，剩下的直接挤在最后一行里 —— 宁可挤，也不
        再藏进一个二级菜单。
        """
        avail = self.act_bar.width()
        if avail <= 0 or avail == self._act_avail:
            return
        self._act_avail = avail
        for row in self._act_rows:
            while row.count():
                item = row.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.hide()

        keys = list(actions.CHAT_ORDER)
        i, used_rows = 0, 0
        for row in self._act_rows:
            used = 0
            while i < len(keys):
                chip = self._chips[keys[i]]
                w = chip.width()
                need = w if used == 0 else used + CHIP_GAP + w
                if need > avail and used > 0:
                    break
                row.addWidget(chip)
                chip.show()
                used = need
                i += 1
            row.addStretch(1)
            used_rows += 1
            if i >= len(keys):
                break

        if i < len(keys):
            last = self._act_rows[used_rows - 1]
            while i < len(keys):
                last.insertWidget(last.count() - 1, self._chips[keys[i]])
                self._chips[keys[i]].show()
                i += 1

        self.act_bar.setFixedHeight(
            CHIP_H * used_rows + CHIP_GAP * (used_rows - 1))

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
        pix = conv_pix(key, 30)
        self.head_av.setPixmap(pix) if pix is not None else self.head_av.clear()
        self._update_state()
        self.set_note('')
        self.target_lb.setText('发送到  %s' % self.name_of(key))
        self.refresh_list()
        self._pinned = True          # 换会话总是从最新一条看起
        self.render()
        self.focus_input()

    def refresh_state(self):
        self._update_state()
        self._sync_rows()

    def _update_state(self):
        if self.key == CONV_ALL:
            online = sum(1 for r in ROLES if r == self.me or self.is_online(r))
            text = '%d/%d 在线' % (online, len(ROLES))
            live = online > 0
        else:
            live = self.is_online(self.key)
            text = '在线' if live else '空闲'
        self.state_lb.set_state(text, live)

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
            self._scroll_soon()

    def _scroll_soon(self):
        """排两次：一次让布局先算完，一次兜底图片/换行这种延迟撑高的行。"""
        QTimer.singleShot(0, self._scroll_if_pinned)
        QTimer.singleShot(60, self._scroll_if_pinned)

    def _scroll_if_pinned(self):
        if self._pinned:
            self._jump_bottom()

    def _jump_bottom(self, value=None):
        sb = self.scroll.verticalScrollBar()
        self._auto_scroll = True
        sb.setValue(sb.maximum() if value is None else value)
        self._auto_scroll = False

    def _on_scroll_value(self, v):
        if self._auto_scroll:
            return
        sb = self.scroll.verticalScrollBar()
        self._pinned = v >= sb.maximum() - 4

    def _on_scroll_range(self, _lo, hi):
        # 消息变多 / 视口被输入框挤矮，maximum 会跟着变；原本贴着底就继续贴住
        if self._pinned:
            self._jump_bottom(hi)

    def _scroll_end(self):
        self._pinned = True
        self._jump_bottom()

    def _time_row(self, ts):
        lb = QLabel(time.strftime('%m月%d日 %H:%M', time.localtime(ts)))
        lb.setAlignment(Qt.AlignCenter)
        lb.setFont(font(8))
        lb.setStyleSheet('color: %s;' % TIME_FG)
        return lb

    def _entry_row(self, e):
        if e.get('kind') == 'tip':
            lb = QLabel(e.get('text') or '')
            lb.setAlignment(Qt.AlignCenter)
            lb.setWordWrap(True)
            lb.setFont(font(8))
            lb.setStyleSheet('color: %s;' % TIME_FG)
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
                lb.setStyleSheet('background: #ffffff; padding: 4px;'
                                 ' border: 1px solid #eef0f5; border-radius: 12px;')
            lb.mousePressEvent = lambda _e, ent=e: self._show_big(ent)
            return lb

        if mt == 'file':
            return self._file_card(e)

        if mt == 'emoji':
            # 发出来的 emoji 原来是 10pt 的一行小字，聊天里根本看不清
            char = e.get('char') or e.get('text') or ''
            lb = QLabel(char)
            lb.setFont(emoji_font(30))
            lb.setStyleSheet(
                'background: %s; padding: 4px 10px; border-radius: 14px;'
                % (BUBBLE_ME if mine else BUBBLE_OTH))
            return lb

        text = e.get('text') or ''
        return BubbleText(text, mine, max_w=self._bubble_room())

    def _bubble_room(self):
        """气泡最宽能到多少：聊天区宽度去掉头像、间距和两侧留白。

        写死上限在窄窗口会横着戳出去，所以按当前视口算 —— 宽了就吃满
        BUBBLE_MAX，窄了跟着收，一行放不下才换行。
        """
        scroll = getattr(self, 'scroll', None)
        vw = scroll.viewport().width() if scroll is not None else 0
        if vw <= 0:
            return BUBBLE_MAX
        return max(200, min(BUBBLE_MAX, vw - 96))

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
            'QWidget { background: #ffffff; border: 1px solid #eef0f5;'
            ' border-radius: 12px; }')
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(3)
        name = QLabel(e.get('name') or '文件')
        name.setFont(font(9, True))
        name.setStyleSheet('color: %s; border: none;' % FG)
        name.setToolTip(e.get('path') or '')
        v.addWidget(name)
        size = QLabel('%s · 点击打开' % human_size(e.get('size') or 0))
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

    def _show_big(self, e):
        """点开图片：重新按原图解码，别拿气泡里那张 240 宽的缩略图放大。"""
        im = media.full(e.get('img') or '', e.get('path') or '')
        if im is None:
            return
        if self._viewer is None:
            self._viewer = ImageViewer(self)
        self._viewer.show_image(QPixmap.fromImage(_qimage(im)), near=self)

    # ---------------- 提示行 ----------------

    def set_note(self, text):
        self.note.setText(text or '')
        self.note.setVisible(bool(text))

    # ---------------- 发送 ----------------

    def send(self):
        """文字 + 已选附件一起发出去。发失败就把草稿留在原地。"""
        if self.on_send is None and self.on_media is None:
            return
        text = self.field.text().strip()
        if not text and not self._attach:
            return
        # 自己发出去的东西一定在底部：先贴住，免得回显那条只露出半截
        self._pinned = True
        if text:
            why = self.on_send(text) if self.on_send is not None else ''
            if why:
                self.set_note(why)
                return
            self.field.clear()
        self.set_note(self._flush_attach())
        self._scroll_soon()

    def _flush_attach(self):
        """把待发附件逐条发出去，返回要显示在提示行上的那句话（没有就空串）。

        发失败就停在那一条上，剩下的留在输入框里 —— 一次操作不该丢东西。
        """
        if self.on_media is None or not self._attach:
            return ''
        items = list(self._attach)
        self._attach = []
        why = ''
        for i, it in enumerate(items):
            why = self.on_media(it['mt'], it['payload']) or ''
            if why:
                self._attach = items[i:]
                break
        self._render_attach()
        return why

    def focus_input(self):
        self.field.setFocus()

    # ---------------- 表情 / 附件 ----------------

    def _on_icon(self, kind):
        if self._pop is not None and self._pop.isVisible():
            self._close_pop()
            if kind == 'emoji':
                return                  # 再点一次表情就是收起
        if kind == 'emoji':
            self._picker_pop()
            self._place_picker()
            self._pop.show()
        else:
            self._pick_files()

    def _pick_files(self):
        """文件按钮：直接开系统文件框，图片和文件在同一次选择里。

        不先弹「图片 / 文件」两个选项 —— 反正看扩展名就知道是什么，
        多一次点击只是拦路。选完不是立刻发，而是落进输入框等你一起发。
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, '发送图片 / 文件', '', '所有文件 (*)')
        if paths:
            self.stage_paths(paths)

    # ---------------- 待发附件 ----------------

    def stage_paths(self, paths):
        """一批本地文件：按扩展名分图片/文件，落进输入框。"""
        room = PASTE_MAX - len(self._attach)
        if room <= 0:
            self.set_note('一次最多带 %d 个附件' % PASTE_MAX)
            return
        for p in paths[:room]:
            ext = os.path.splitext(p)[1].lower()
            mt = 'img' if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp',
                                  '.bmp') else 'file'
            self.stage(mt, p)
        if len(paths) > room:
            self.set_note('一次最多带 %d 个附件，先放这些' % PASTE_MAX)

    def stage(self, mt, payload):
        """把一个附件放进输入框。payload 是路径（文件/图片）或 PIL.Image。"""
        name = ''
        pix = None
        if isinstance(payload, Image.Image):
            name = '粘贴的图片'
            im = payload.copy()
            im.thumbnail((44, 44), Image.LANCZOS)
            pix = QPixmap.fromImage(_qimage(im.convert('RGBA')))
        else:
            name = os.path.basename(payload or '') or '文件'
            if mt == 'img':
                im = media.thumb('', payload, 44)
                if im is not None:
                    pix = QPixmap.fromImage(_qimage(im))
        self._attach.append({'mt': mt, 'payload': payload, 'name': name,
                             'pix': pix})
        self._render_attach()
        self.set_note('')
        self.focus_input()

    def _render_attach(self):
        while self.attach_lay.count():
            item = self.attach_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
        self._attach_chips = []
        for it in self._attach:
            chip = AttachChip(it, self.attach_host)
            chip.removed.connect(self._unstage)
            self.attach_lay.addWidget(chip)
            self._attach_chips.append(chip)
        self.attach_lay.addStretch(1)
        self.attach_host.setVisible(bool(self._attach))

    def _unstage(self, item):
        self._attach = [it for it in self._attach if it is not item]
        self._render_attach()

    def _picker_pop(self):
        """表情 / 表情包拾取面板：三页，同一块格子换内容。"""
        pop = QFrame(None, Qt.Popup)
        self._pop = pop             # 弹层没有 parent，引用必须自己先攥住
        self._pick_top = None       # 每次打开重新算锚点
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
                % (MUTED, '#f4f5fa', FG, '#f4f5fa'))
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
        self._pick_grid.setSpacing(CELL_GAP)
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

        cells = []
        if page == 'emoji':
            # 第一格留给自定义贴图（assets/emoji/doge.png）：图在就占首位、走
            # sticker 通道发原件；图不在就只排字符，不留一个空格子。
            doge = media.doge_path(assets_dir())
            if os.path.exists(doge):
                cells.append(PickerCell(
                    QPixmap(doge), '', media.DOGE_LABEL + ' · 点一下发送',
                    lambda _=False, p=doge: self._pick_sticker(p),
                    self._show_preview, self._hide_preview, self._pick_box))
            for ch in media.EMOJI_CHARS:
                cells.append(PickerCell(
                    None, ch, '表情 · 点一下发送',
                    lambda _=False, c=ch: self._pick_emoji(c),
                    self._show_preview, self._hide_preview, self._pick_box))
        else:
            for path in self._sticker_cards(page):
                # 原图进 PickerCell，缩放只发生在绘制的那一刻
                cells.append(PickerCell(
                    QPixmap(path), '', self._sticker_tip(path),
                    lambda _=False, p=path: self._pick_sticker(p),
                    self._show_preview, self._hide_preview, self._pick_box))

        # 格子区按这一页的行数给死尺寸，再往里摆。不这么做的话，从「表情」
        # （三行）切到「我的」（一行）时 QGridLayout 还按旧的行高算，
        # 弹层不缩，格子被推到最下面还被裁掉一截。
        rows = max(1, (len(cells) + COLS - 1) // COLS)
        self._pick_box.setFixedSize(COLS * CELL + (COLS - 1) * CELL_GAP,
                                    rows * CELL + (rows - 1) * CELL_GAP)
        for i, cell in enumerate(cells):
            self._pick_grid.addWidget(cell, i // COLS, i % COLS)

        if self._pop is not None:
            self._pop.adjustSize()
            if self._pop.isVisible():
                self._place_picker(keep_top=True)

    def _place_picker(self, keep_top=False):
        """面板贴着表情按钮往上弹，宽度固定，贴到屏幕右边就往回挪。

        `keep_top` 是翻页用的：内容变少时上边缘不动（标签栏还在鼠标底下，
        面板只把底边收上去），内容变多才往上顶，免得压到输入框。
        """
        pop = self._pop
        if pop is None:
            return
        pop.adjustSize()
        btn = self.icon_btn['emoji']
        scr = (self.screen() or QGuiApplication.primaryScreen()
               ).availableGeometry()
        at = btn.mapToGlobal(QPoint(0, 0))
        x = max(scr.left() + 8, min(at.x(), scr.right() - pop.width() - 8))
        y = at.y() - pop.height() - 6       # 自然位置：底边贴着按钮上沿
        if keep_top and self._pick_top is not None:
            y = min(self._pick_top, y)      # 变矮就留住打开时的上边缘
        else:
            self._pick_top = y              # 打开时记一次，翻页都按它对齐
        if y < scr.top() + 8:       # 上面放不下就翻到按钮下面
            y = at.y() + btn.height() + 6
        pop.move(QPoint(int(x), int(y)))

    def _sticker_cards(self, page):
        """这一页有哪些卡片：我的 = 自己八张；他人 = 其余每个角色各八张。"""
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
        self._pick_top = None
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
            self.stage('img', pil)
            return True
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self.stage_paths(paths)
                return True
        self.field.paste()
        return True

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.stage_paths(paths)
        e.acceptProposedAction()

    # ---------------- 层级 / 尺寸 ----------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        hint = getattr(self, 'hint', None)
        if hint is not None:
            hint.setVisible(self.width() > 820)
        if not self._reflow_pending:
            self._reflow_pending = True
            QTimer.singleShot(0, self._do_reflow)

    def _do_reflow(self):
        self._reflow_pending = False
        self._reflow_actions()
        self._refit_bubbles()

    def _refit_bubbles(self):
        """窗口宽窄变了，已有气泡按新的聊天区宽度重排。"""
        room = self._bubble_room()
        for b in self.msg_host.findChildren(BubbleText):
            b.set_max_width(room)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._reflow_actions)
        if self._pinned:                 # 首次显示时布局才定尺寸，补一次贴底
            self._scroll_soon()

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

    def alive(self):
        """窗口收起来（隐藏 / 最小化）之后，消息要照常算未读。"""
        return self.isVisible() and not self.isMinimized()

    def dismiss(self):
        if self._viewer is not None:
            self._viewer.hide()
        super().dismiss()

    def closeEvent(self, e):
        self._close_pop()
        self._hide_preview()
        if self._viewer is not None:
            self._viewer.hide()      # 大图别留在屏幕上陪着一个已经收起的聊天窗
        super().closeEvent(e)
