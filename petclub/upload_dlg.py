# -*- coding: utf-8 -*-
"""桌面端的「加个动作 / 换一张图」弹层。

版式和网页版 style.css 里的 .upload-card 一一对应：标题条 +「选一张图」
虚线按钮 + 棋盘格预览台 + 动作缩略图网格 + 底部状态/取消/上传。颜色全从
assets/theme.json 来（theme.py 读同一份，网关注入成 CSS 变量），两端改一处
一起变。

为什么不直接用系统控件：QDialog 的标题栏、QListWidget 图标模式自带的选中
蓝框、QDoubleSpinBox 右侧的上下箭头，都是 Windows 自己的观感。聊天窗是自绘
的无边框卡片，两套放在一起就会显得「这是哪来的一个窗口」。所以这里全部自绘，
只借 QDialog 的模态和 exec()。

图片处理仍然全在 upload.normalize 里，这个文件只管交互。
"""

import io
import os
import shutil
import tempfile

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QDoubleValidator, QDrag, QPainter,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup,
                               QComboBox, QDialog, QFileDialog, QFrame,
                               QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QVBoxLayout, QWidget)

from PIL import Image

from . import motion, upload
from .config import ROLE_NAME
from .custom import norm_label
from .theme import (ACCENT, ACCENT_DOWN, ACCENT_EDGE, ACCENT_SOFT, BORDER,
                    CARD_BG, ERR, FG, LINE, MUTED, SUB, font)

THUMB_H = 48        # 格子里缩略图的高度，和网页 .pose-thumb 一致
TILE_W = 72
TILE_H = 78
COLS = 5
FRAME_COLS = 9      # 帧条一行几格（38px + 8 间距正好塞进 432 内容宽，和网页一致）
FRAME_GAP = 8
STAGE_H = 148


def _thumb_pix(path):
    """缩略图按原图比例放进 48 高的框，不放大也不拉扁。"""
    if not path or not os.path.exists(path):
        return None
    pix = QPixmap(path)
    if pix.isNull():
        return None
    if pix.height() > THUMB_H or pix.width() > TILE_W - 6:
        pix = pix.scaled(TILE_W - 6, THUMB_H, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
    return pix


def _median_alpha_height(frames):
    """一组帧里人物（alpha）高度的中位数，用来自动估一个大合适的初始比例。"""
    heights = []
    for data in frames:
        try:
            im = Image.open(io.BytesIO(data)).convert('RGBA')
        except (OSError, ValueError):
            continue
        box = im.getchannel('A').getbbox()
        if box:
            heights.append(box[3] - box[1])
    if not heights:
        return 0
    heights.sort()
    return heights[len(heights) // 2]


def _pad_frame(data, cw, ch):
    """把一帧放进 cw×ch 的画布、底部居中（多出来的只是透明留白）。"""
    try:
        im = Image.open(io.BytesIO(data)).convert('RGBA')
    except (OSError, ValueError):
        return data
    if im.size == (cw, ch):
        return data
    canvas = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
    x = (cw - im.width) // 2
    y = ch - im.height
    left, top = max(0, x), max(0, y)
    right, bottom = min(cw, x + im.width), min(ch, y + im.height)
    if right > left and bottom > top:
        part = im.crop((left - x, top - y, right - x, bottom - y))
        canvas.paste(part, (left, top), part)
    buf = io.BytesIO()
    canvas.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def _zoom_png(data, scale):
    """把一帧以底部中心为锚点缩放，画布尺寸不变（超出的部分裁掉）。

    多帧动作上传后只做过「裁边 + 缩小」，永远不会放大；带特效的动作
    人物占比会很小。这里给用户一个手动补偿的机会，缩放直接烘进 PNG，
    播放端（桌面/网页）完全不用知道这件事。
    """
    if abs(scale - 1.0) < 0.001:
        return data
    try:
        im = Image.open(io.BytesIO(data)).convert('RGBA')
    except (OSError, ValueError):
        return data
    w, h = im.size
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if (nw, nh) == (w, h):
        return data
    scaled = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    x = (w - nw) // 2
    y = h - nh
    left, top = max(0, x), max(0, y)
    right, bottom = min(w, x + nw), min(h, y + nh)
    if right > left and bottom > top:
        part = scaled.crop((left - x, top - y, right - x, bottom - y))
        canvas.paste(part, (left, top), part)
    buf = io.BytesIO()
    canvas.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def _hint(text):
    """表单左侧那列灰标签。"""
    lab = QLabel(text)
    lab.setFont(font(9))
    lab.setStyleSheet('color: %s;' % SUB)
    return lab


class CloseButton(QAbstractButton):
    """标题条右上角的 ×。悬停变红，和聊天窗那颗关闭按钮同一套观感。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('关闭')
        self._hover = False

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
            p.setBrush(QColor('#f0445a'))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setPen(QPen(QColor('#ffffff' if self._hover else MUTED), 1.5,
                      Qt.SolidLine, Qt.RoundCap))
        cx, cy = self.width() / 2.0, self.height() / 2.0
        p.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
        p.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))
        p.end()


class Check(QAbstractButton):
    """自绘小勾选框。QCheckBox 的原生方框在 150% 缩放下会糊、也对不齐文字。"""

    BOX = 14

    def __init__(self, text, checked=True, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(9))
        self.setFixedHeight(20)
        self.setMinimumWidth(20 + self.fontMetrics().horizontalAdvance(text) + 4)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        box = QRectF(0.5, (self.height() - self.BOX) / 2.0 + 0.5,
                     self.BOX, self.BOX)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QPen(QColor('#ffffff'), 1.6, Qt.SolidLine,
                          Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(box.left() + 3.2, box.top() + 7.4),
                       QPointF(box.left() + 5.9, box.top() + 10.0))
            p.drawLine(QPointF(box.left() + 5.9, box.top() + 10.0),
                       QPointF(box.left() + 10.8, box.top() + 4.4))
        else:
            p.setPen(QPen(QColor(BORDER), 1.2))
            p.setBrush(QColor('#ffffff'))
            p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor(SUB))
        p.drawText(QRectF(20, 0, self.width() - 20, self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()

    def sizeHint(self):
        return QSize(self.minimumWidth(), 20)


class Stage(QWidget):
    """棋盘格预览台：透明的地方露格子，图有没有去干净一眼能看出来。"""

    PAD = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = None
        self._empty = '还没选图'
        self._dx = 0.0
        self._dy = 0.0
        self.setFixedHeight(STAGE_H)
        self._board = self._make_board()

    @staticmethod
    def _make_board():
        pm = QPixmap(16, 16)
        pm.fill(QColor('#ffffff'))
        p = QPainter(pm)
        p.fillRect(0, 0, 8, 8, QColor('#f3f5fa'))
        p.fillRect(8, 8, 8, 8, QColor('#f3f5fa'))
        p.end()
        return pm

    def set_pixmap(self, pix):
        self._pix = pix
        self.update()

    def set_offset(self, dx, dy):
        """轨迹预览：在当前图的基础上再挪一点（会被预览台边界裁掉）。"""
        if abs(dx - self._dx) < 0.3 and abs(dy - self._dy) < 0.3:
            return
        self._dx = float(dx)
        self._dy = float(dy)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QBrush(self._board))
        p.drawRoundedRect(body, 10, 10)

        inner = body.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        if self._pix is None or self._pix.isNull():
            p.setPen(QColor(MUTED))
            p.setFont(font(9))
            p.drawText(inner, Qt.AlignCenter, self._empty)
            p.end()
            return
        k = min(inner.width() / max(1, self._pix.width()),
                inner.height() / max(1, self._pix.height()), 1.0)
        w = self._pix.width() * k
        h = self._pix.height() * k
        # 轨迹预览会把人移出原来的位置，裁在预览台里，别画到卡片外面。
        p.save()
        p.setClipRect(inner)
        p.drawPixmap(int(round(inner.center().x() - w / 2 + self._dx)),
                     int(round(inner.center().y() - h / 2 + self._dy)),
                     int(round(w)), int(round(h)), self._pix)
        p.restore()
        p.end()


class Tile(QAbstractButton):
    """一个动作格子：缩略图 + 名字，选中时 accent 描边，右上角挂角标。"""

    def __init__(self, key, label, pix=None, new=False, badge='', parent=None):
        super().__init__(parent)
        self.key = key
        self.label = label
        self.badge = badge
        self.new = new
        self._pix = pix
        self._hover = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(TILE_W, TILE_H)
        self.setToolTip(label)

    def sizeHint(self):
        return QSize(TILE_W, TILE_H)

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
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self.isChecked():
            pen, fill = QPen(QColor(ACCENT), 2), QColor(ACCENT_SOFT)
        elif self._hover:
            pen, fill = QPen(QColor(ACCENT_EDGE), 1), QColor(ACCENT_SOFT)
        else:
            pen, fill = QPen(QColor(BORDER), 1), QColor('#ffffff')
        if self.new and not self.isChecked():
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([3, 2])
        p.setPen(pen)
        p.setBrush(fill)
        half = pen.widthF() / 2.0
        p.drawRoundedRect(QRectF(self.rect()).adjusted(half, half, -half, -half),
                          9, 9)

        top = 6.0
        if self._pix is not None:
            p.drawPixmap(int(round((self.width() - self._pix.width()) / 2.0)),
                         int(round(top + THUMB_H - self._pix.height())), self._pix)
        elif self.new:
            cx, cy = self.width() / 2.0, top + THUMB_H / 2.0
            p.setPen(QPen(QColor(ACCENT), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 7, cy), QPointF(cx + 7, cy))
            p.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 7))

        p.setFont(font(8))
        p.setPen(QColor(SUB))
        fm = p.fontMetrics()
        text = fm.elidedText(self.label, Qt.ElideRight, self.width() - 8)
        p.drawText(QRectF(3, self.height() - 18, self.width() - 6, 14),
                   Qt.AlignHCenter | Qt.AlignVCenter, text)

        if self.badge:
            # 白底加一圈细边：角标永远压在缩略图脑袋上，纯色底会显得脏。
            p.setFont(font(7))
            bw = p.fontMetrics().horizontalAdvance(self.badge) + 8
            br = QRectF(self.width() - bw - 4, 3, bw, 13)
            p.setPen(QPen(QColor(ACCENT_EDGE), 1))
            p.setBrush(QColor('#ffffff'))
            p.drawRoundedRect(br, 5, 5)
            p.setPen(QColor(ACCENT))
            p.drawText(br, Qt.AlignCenter, self.badge)
        p.end()


class FrameCell(QWidget):
    """预览条上的一帧：缩略图 + 左上角序号 + 右上角删除叉。

    和网页帧条同一套交互：单击选中（紫色描边），拖动换顺序。
    """

    SIZE = 38          # 和网页 .upload-frame 一样大：一行正好放 9 格，不出现横向滚动条

    def __init__(self, index, pix, on_remove, on_select=None, on_move=None,
                 selected=False, parent=None):
        super().__init__(parent)
        self.index = index
        self._pix = pix
        self._on_remove = on_remove
        self._on_select = on_select
        self._on_move = on_move
        self._selected = selected
        self._press = None
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setToolTip('单击预览，拖动排序')

    def set_selected(self, on):
        if on != self._selected:
            self._selected = on
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = QRectF(0.5, 0.5, self.SIZE - 1, self.SIZE - 1)
        if self._selected:
            p.setPen(QPen(QColor(ACCENT), 2))
            p.setBrush(QColor(ACCENT_SOFT))
        else:
            p.setPen(QPen(QColor(BORDER), 1))
            p.setBrush(QColor('#ffffff'))
        p.drawRoundedRect(r, 6, 6)
        if self._pix is not None and not self._pix.isNull():
            pm = self._pix.scaled(self.SIZE - 4, self.SIZE - 4,
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int((self.width() - pm.width()) / 2),
                         int((self.height() - pm.height()) / 2), pm)
        p.setPen(QColor(MUTED))
        p.setFont(font(7))
        p.drawText(QRectF(3, self.height() - 12, 18, 10),
                   Qt.AlignLeft | Qt.AlignVCenter, str(self.index + 1))
        # 右上角红叉
        c = QPointF(self.width() - 6.5, 6.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#d6455d'))
        p.drawEllipse(c, 6, 6)
        p.setPen(QPen(QColor('#ffffff'), 1.3))
        p.drawLine(QPointF(c.x() - 2.2, c.y() - 2.2),
                   QPointF(c.x() + 2.2, c.y() + 2.2))
        p.drawLine(QPointF(c.x() + 2.2, c.y() - 2.2),
                   QPointF(c.x() - 2.2, c.y() + 2.2))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().x() >= self.width() - 14 \
                and e.position().y() <= 14:
            self._on_remove(self.index)
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(e)
            return
        moved = (e.position().toPoint() - self._press).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText('petclub-frame:%d' % self.index)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self._press = None
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press is not None:
            self._press = None
            if self._on_select:
                self._on_select(self.index)
        super().mouseReleaseEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasText() \
                and e.mimeData().text().startswith('petclub-frame:'):
            e.setDropAction(Qt.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        text = e.mimeData().text() if e.mimeData().hasText() else ''
        if not text.startswith('petclub-frame:') or self._on_move is None:
            e.ignore()
            return
        try:
            src = int(text.split(':', 1)[1])
        except ValueError:
            src = -1
        if src >= 0:
            self._on_move(src, self.index)
        e.accept()


class AddCell(QAbstractButton):
    """帧条最右侧的加图位：虚线小方框 + 加号，和网页的「＋」一致。"""

    SIZE = FrameCell.SIZE

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('再加几张图')
        self.clicked.connect(on_click)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(ACCENT_EDGE), 1.2)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(ACCENT_SOFT))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.SIZE - 1, self.SIZE - 1), 6, 6)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        p.setPen(QPen(QColor(ACCENT), 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - 6, cy), QPointF(cx + 6, cy))
        p.drawLine(QPointF(cx, cy - 6), QPointF(cx, cy + 6))
        p.end()


class UploadDialog(QDialog):
    """targets: [{'key','label','icon','tip','badge'}]，第一项是「新建动作」（key 为空）。"""

    def __init__(self, role, max_box, targets, on_restore=None, taken=(),
                 parent=None, preset_frames=None, preset_fps=None,
                 preset_key='', load_preset=None, idle_h=0):
        super().__init__(parent)
        self.role = role
        self.max_w, self.max_h = max_box
        self._idle_h = float(idle_h or 0)
        self._on_restore = on_restore
        self._load_cb = load_preset
        self._loaded_key = None      # 当前这组帧是从哪个动作预载进来的
        self._user_picked = False    # 用户自己选过图，就别再被预载覆盖
        # 「人物大小」：原始归一化后的帧 + 当前缩放。缩放只改上传的图，
        # 播放端完全不用知道（见 _zoom_png）。
        self._base_frames = []
        self._scale = 1.0
        # 已经被占用的名字（我自己的自定义动作 + 内置动作）。
        # 名字是「跟着做」的配对键：自己重名就不知道该演哪个。
        self._taken = {norm_label(t) for t in taken if t}
        self._frames = []
        self._w = self._h = 0
        self._paths = []
        self._pixmaps = []
        self._preview_i = 0
        self._move_prev_t = 0.0
        self._fps = 8.0
        self._drag = None
        self._tmp_dir = None       # 编辑已有动作时，把预载帧落到这里当文件用
        # 多帧动作在预览台上按 8fps 循环，用户能直接看到动起来的效果。
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(125)
        self._preview_timer.timeout.connect(self._tick_preview)

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle('加个动作')
        self.setStyleSheet(self._qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.panel = QFrame(self)
        self.panel.setObjectName('panel')
        outer.addWidget(self.panel)

        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(17, 24, 39, 64))
        self.panel.setGraphicsEffect(shadow)

        head = QFrame(self.panel)
        head.setObjectName('head')
        head.setFixedHeight(48)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 0, 10, 0)
        self.title = QLabel('给「%s」加个动作' % ROLE_NAME.get(role, role))
        self.title.setFont(font(10, True))
        self.title.setStyleSheet('color: %s;' % FG)
        hl.addWidget(self.title)
        hl.addStretch(1)
        self.close_btn = CloseButton(head)
        self.close_btn.clicked.connect(self.reject)
        hl.addWidget(self.close_btn)

        body = QWidget(self.panel)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(10)

        pickrow = QHBoxLayout()
        pickrow.setSpacing(10)
        self.pick_btn = QPushButton('选图（可多选）…')
        self.pick_btn.setObjectName('pick')
        self.pick_btn.setCursor(Qt.PointingHandCursor)
        self.pick_btn.clicked.connect(self._pick)
        pickrow.addWidget(self.pick_btn, 0, Qt.AlignLeft)
        pickrow.addStretch(1)
        self.keyout = Check('去掉白色背景', True)
        self.keyout.toggled.connect(self._reload)
        pickrow.addWidget(self.keyout, 0, Qt.AlignRight)
        bl.addLayout(pickrow)

        self.stage = Stage()
        bl.addWidget(self.stage)

        # 多帧预览条：每帧一个缩略图，右上角的叉可以删掉这一帧。
        # 和网页一样按行换行（9 格一行），不再横向滚动 —— 横条在窄卡片里
        # 会顶出一条滚动条，看着比网页脏。
        self.frame_scroll = QScrollArea()
        self.frame_scroll.setObjectName('frames')
        self.frame_scroll.setWidgetResizable(True)
        self.frame_scroll.setFixedHeight(FrameCell.SIZE + 2)
        self.frame_scroll.setFrameShape(QFrame.NoFrame)
        self.frame_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.frame_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.frame_host = QWidget()
        self.frame_host.setAutoFillBackground(False)
        self.frame_host.setStyleSheet('background: #ffffff;')
        self.frame_grid = QGridLayout(self.frame_host)
        self.frame_grid.setContentsMargins(0, 0, 0, 0)
        self.frame_grid.setHorizontalSpacing(FRAME_GAP)
        self.frame_grid.setVerticalSpacing(FRAME_GAP)
        self.frame_grid.setColumnStretch(FRAME_COLS, 1)
        self.frame_scroll.setWidget(self.frame_host)
        self.frame_scroll.viewport().setAutoFillBackground(False)
        self.frame_scroll.viewport().setStyleSheet('background: #ffffff;')
        self.frame_scroll.setVisible(False)
        bl.addWidget(self.frame_scroll)

        # 播放控制：和网页弹层同一行、同一顺序 —— 自动播放、播放速度、
        # 滑条、数值，紧跟在帧条下面，而不是压在最底部。
        self.play_row = QWidget()
        pl = QHBoxLayout(self.play_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        self.auto_box = Check('自动播放', True)
        self.auto_box.toggled.connect(self._sync_play)
        pl.addWidget(self.auto_box, 0)
        pl.addWidget(_hint('播放速度'), 0)
        self.fps_slider = QSlider(Qt.Horizontal)
        self.fps_slider.setRange(20, 240)
        self.fps_slider.setValue(80)
        self.fps_slider.setMinimumWidth(90)
        self.fps_slider.setCursor(Qt.PointingHandCursor)
        self.fps_slider.valueChanged.connect(self._fps_changed)
        pl.addWidget(self.fps_slider, 1)
        self.fps_val = QLabel('8.0')
        self.fps_val.setFont(font(9))
        self.fps_val.setStyleSheet('color: %s;' % SUB)
        self.fps_val.setFixedWidth(30)
        self.fps_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pl.addWidget(self.fps_val, 0)
        bl.addWidget(self.play_row)

        grid_host = QWidget()
        grid_host.setStyleSheet('background: #ffffff;')
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.tiles = {}
        for i, t in enumerate(targets):
            key = t.get('key') or ''
            tile = Tile(key, t.get('label') or key,
                        pix=None if not key else _thumb_pix(t.get('icon')),
                        new=not key, badge=t.get('badge') or '')
            tile.clicked.connect(lambda _c=False, k=key: self._on_tile(k))
            self._group.addButton(tile)
            self.tiles[key] = tile
            grid.addWidget(tile, i // COLS, i % COLS, Qt.AlignHCenter)
        for c in range(COLS):
            grid.setColumnStretch(c, 1)
        rows = max(1, (len(targets) + COLS - 1) // COLS)
        need = rows * TILE_H + max(0, rows - 1) * 6 + 4
        self.scroll = QScrollArea()
        self.scroll.setObjectName('poses')
        self.scroll.setWidget(grid_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(min(178, need))
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet('background: #ffffff;')
        # 「动作」这一列和网页一致：40px 标签 + 右边缩略图网格。
        pose_row = QHBoxLayout()
        pose_row.setSpacing(8)
        pose_lab = _hint('动作')
        pose_lab.setFixedWidth(40)
        pose_row.addWidget(pose_lab, 0)
        pose_row.addWidget(self.scroll, 1)
        bl.addLayout(pose_row)

        self.fields = QWidget()
        fl = QVBoxLayout(self.fields)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)

        # 名字只给「新建动作」；时长自定义动作也能改，所以两行分开控制。
        self.name_row = QWidget()
        nl = QHBoxLayout(self.name_row)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(10)
        name_lab = _hint('名字')
        name_lab.setFixedWidth(40)
        self.name = QLineEdit()
        self.name.setMaxLength(10)
        self.name.setPlaceholderText('比如 跳舞、比耶')
        self.name.returnPressed.connect(self.accept)
        nl.addWidget(name_lab)
        nl.addWidget(self.name, 1)
        fl.addWidget(self.name_row)

        self.dur_row = QWidget()
        dl = QHBoxLayout(self.dur_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        dur_lab = _hint('时长')
        dur_lab.setFixedWidth(40)
        self.dur = QLineEdit('2.0')
        self.dur.setValidator(QDoubleValidator(0.6, 8.0, 1, self))
        self.dur.setFixedWidth(86)
        self.dur.returnPressed.connect(self.accept)
        unit = QLabel('秒')
        unit.setFont(font(9))
        unit.setStyleSheet('color: %s;' % MUTED)
        dl.addWidget(dur_lab)
        dl.addWidget(self.dur)
        dl.addWidget(unit)
        dl.addStretch(1)
        fl.addWidget(self.dur_row)

        # 人物大小：上传时只裁边、只缩小，带特效的动作人物会显得小。
        # 这里按底部中心缩放，直接烘进上传的帧里，播放端不需要改。
        self.scale_row = QWidget()
        sl = QHBoxLayout(self.scale_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        scale_lab = _hint('大小')
        scale_lab.setFixedWidth(40)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(70, 160)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(5)
        self.scale_slider.setValue(100)
        self.scale_slider.setMinimumWidth(120)
        self.scale_slider.setCursor(Qt.PointingHandCursor)
        self.scale_slider.valueChanged.connect(self._scale_changed)
        self.scale_val = QLabel('100%')
        self.scale_val.setFont(font(9))
        self.scale_val.setStyleSheet('color: %s;' % SUB)
        self.scale_val.setFixedWidth(38)
        self.scale_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sl.addWidget(scale_lab)
        sl.addWidget(self.scale_slider, 1)
        sl.addWidget(self.scale_val)
        fl.addWidget(self.scale_row)

        # 换图时这块不空着：说明 +「恢复默认图 / 删掉这个动作」。
        self.alt = QWidget()
        al = QHBoxLayout(self.alt)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(10)
        self.alt_text = QLabel('')
        self.alt_text.setWordWrap(True)
        self.alt_text.setFont(font(9))
        self.alt_text.setStyleSheet('color: %s;' % SUB)
        al.addWidget(self.alt_text, 1)
        self.restore_btn = QPushButton('恢复默认图')
        self.restore_btn.setObjectName('ghost')
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.clicked.connect(self._restore)
        al.addWidget(self.restore_btn, 0, Qt.AlignRight)

        bl.addWidget(self.fields)

        # 移动轨迹：御剑飞行那种。只对自定义动作开放 —— 内置姿势的语义
        # 和时长是写死的，飞起来反而怪。数值全是比例，播放时按屏幕算。
        self.move_box = QWidget()
        mvl = QVBoxLayout(self.move_box)
        mvl.setContentsMargins(0, 0, 0, 0)
        mvl.setSpacing(6)

        mr = QHBoxLayout()
        mr.setSpacing(8)
        mr.addWidget(_hint('移动'))
        self.move_kind = QComboBox()
        self.move_kind.addItem('静止', '')
        self.move_kind.addItem('横穿（飞出去再回来）', 'line')
        self.move_kind.addItem('弧线（腾空飞越）', 'arc')
        self.move_kind.addItem('环绕（绕一圈）', 'orbit')
        self.move_kind.addItem('原地悬停', 'hover')
        self.move_kind.setCursor(Qt.PointingHandCursor)
        self.move_kind.currentIndexChanged.connect(self._move_changed)
        mr.addWidget(self.move_kind, 1)
        mvl.addLayout(mr)

        nr = QHBoxLayout()
        nr.setSpacing(8)
        nr.addWidget(_hint('高度'))
        self.move_h = QSlider(Qt.Horizontal)
        self.move_h.setRange(5, 80)
        self.move_h.setValue(35)
        self.move_h.setMinimumWidth(80)
        self.move_h.setCursor(Qt.PointingHandCursor)
        self.move_h.valueChanged.connect(self._move_changed)
        nr.addWidget(self.move_h, 1)
        self.move_h_val = QLabel('35%')
        self.move_h_val.setFont(font(9))
        self.move_h_val.setStyleSheet('color: %s;' % SUB)
        self.move_h_val.setFixedWidth(34)
        self.move_h_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nr.addWidget(self.move_h_val)
        nr.addSpacing(8)
        nr.addWidget(_hint('跨度'))
        self.move_d = QSlider(Qt.Horizontal)
        self.move_d.setRange(20, 100)
        self.move_d.setValue(80)
        self.move_d.setMinimumWidth(80)
        self.move_d.setCursor(Qt.PointingHandCursor)
        self.move_d.valueChanged.connect(self._move_changed)
        nr.addWidget(self.move_d, 1)
        self.move_d_val = QLabel('80%')
        self.move_d_val.setFont(font(9))
        self.move_d_val.setStyleSheet('color: %s;' % SUB)
        self.move_d_val.setFixedWidth(34)
        self.move_d_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nr.addWidget(self.move_d_val)
        mvl.addLayout(nr)
        bl.addWidget(self.move_box)
        self._move_changed()

        # 说明 +「删掉 / 恢复默认图」：和网页一样排在移动设置下面。
        bl.addWidget(self.alt)
        bl.addStretch(1)

        foot = QFrame(self.panel)
        foot.setObjectName('foot')
        foot.setFixedHeight(56)
        ft = QHBoxLayout(foot)
        ft.setContentsMargins(16, 0, 16, 0)
        ft.setSpacing(8)
        self.status = QLabel('')
        self.status.setFont(font(9))
        self.status.setStyleSheet('color: %s;' % SUB)
        ft.addWidget(self.status, 1)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.setObjectName('ghost')
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        ft.addWidget(self.cancel_btn)
        self.ok = QPushButton('上传')
        self.ok.setObjectName('primary')
        self.ok.setCursor(Qt.PointingHandCursor)
        self.ok.setMinimumWidth(84)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        ft.addWidget(self.ok)

        panel_lay = QVBoxLayout(self.panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.setSpacing(0)
        panel_lay.addWidget(head)
        panel_lay.addWidget(body, 1)
        panel_lay.addWidget(foot)

        # 和网页 .upload-card 同宽（460 内容宽）+ 两侧各 18 的阴影留白。
        self.setFixedWidth(496)
        first = targets[0].get('key') or ''
        self.tiles[first].setChecked(True)
        self._sync_mode()
        if preset_frames:
            self._load_preset(preset_frames, preset_fps, preset_key)
        self.resize(496, self.panel.sizeHint().height() + 36)
        self._center()

    # ---- 样式 ----

    def _qss(self):
        return """
        #panel { background: %(window)s; border-radius: 16px; }
        #head { background: %(window)s; border-top-left-radius: 16px;
                border-top-right-radius: 16px; border-bottom: 1px solid %(line)s; }
        #foot { background: %(card_bg)s; border-bottom-left-radius: 16px;
                border-bottom-right-radius: 16px; border-top: 1px solid %(line)s; }
        QLineEdit { background: #ffffff; border: 1px solid %(border)s;
                    border-radius: 8px; padding: 5px 9px; color: %(fg)s;
                    font-size: 12px; selection-background-color: %(accent_soft)s;
                    selection-color: %(fg)s; }
        QLineEdit:focus { border-color: %(accent_edge)s; }
        QScrollArea#poses { background: transparent; border: none; }
        QScrollArea#frames { background: transparent; border: none; }
        QScrollBar:vertical { width: 8px; background: transparent; margin: 2px; }
        QScrollBar::handle:vertical { background: #d6d9e3; border-radius: 4px;
                                      min-height: 24px; }
        QScrollBar:horizontal { height: 8px; background: transparent; margin: 2px; }
        QScrollBar::handle:horizontal { background: #d6d9e3; border-radius: 4px;
                                        min-width: 24px; }
        QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
        QPushButton#pick { border: 1px dashed %(accent_edge)s; border-radius: 10px;
                           background: %(accent_soft)s; color: %(accent)s;
                           font-size: 13px; font-weight: bold; padding: 8px 14px; }
        QPushButton#pick:hover { background: #e8e9ff; }
        QPushButton#primary { background: %(accent)s; color: #ffffff; border: none;
                              border-radius: 9px; padding: 8px 14px;
                              font-size: 13px; font-weight: bold; }
        QPushButton#primary:hover { background: %(accent_down)s; }
        QPushButton#primary:disabled { background: #dfe1ea; color: #ffffff; }
        QPushButton#ghost { background: transparent; color: %(sub)s;
                            border: 1px solid %(border)s; border-radius: 9px;
                            padding: 8px 14px; font-size: 13px; }
        QPushButton#ghost:hover { background: %(field)s; color: %(fg)s; }
        QSlider::groove:horizontal { height: 4px; border-radius: 2px;
                                     background: #e3e5ee; }
        QSlider::sub-page:horizontal { background: %(accent)s;
                                       border-radius: 2px; }
        QSlider::handle:horizontal { width: 12px; height: 12px;
                                     margin: -5px 0; border-radius: 6px;
                                     background: #ffffff;
                                     border: 2px solid %(accent)s; }
        """ % {'window': '#ffffff', 'line': LINE, 'card_bg': CARD_BG,
               'border': BORDER, 'fg': FG, 'sub': SUB, 'muted': MUTED,
               'field': '#f7f8fb', 'accent': ACCENT, 'accent_down': ACCENT_DOWN,
               'accent_soft': ACCENT_SOFT, 'accent_edge': ACCENT_EDGE}

    def _center(self):
        scr = self.screen()
        if scr is None:
            return
        g = scr.availableGeometry()
        self.move(g.center().x() - self.width() // 2,
                  g.center().y() - self.height() // 2)

    # ---- 选图 ----

    def _pick(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选一张或多张图（多张会按 8fps 循环）', '',
            '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if paths:
            self._paths = list(paths[:16])
            self._load()

    def _reload(self, *_a):
        if self._paths:
            self._load()

    # ---- 人物大小 ----

    def _scale_changed(self, value):
        if abs(value / 100.0 - self._scale) < 0.001:
            return
        self._scale = value / 100.0
        self.scale_val.setText('%d%%' % value)
        if self._base_frames:
            self._apply_scale()

    def _set_bases(self, frames, auto=False):
        """换一组基准帧；auto=True 时按站立高度估一个初始大小。"""
        self._base_frames = [bytes(f) for f in frames]
        scale = 1.0
        if auto and self._base_frames and self._idle_h > 0:
            body = _median_alpha_height(self._base_frames)
            if body > 0:
                scale = 0.9 * self._idle_h / body
        scale = max(0.7, min(1.3, round(scale * 20) / 20.0))   # 5% 一档
        self._scale = scale
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(int(round(scale * 100)))
        self.scale_slider.blockSignals(False)
        self.scale_val.setText('%d%%' % int(round(scale * 100)))
        self._apply_scale()

    def _apply_scale(self):
        """按当前比例重新生成上传用的帧和预览图（基准帧不动）。"""
        if not self._base_frames:
            self._frames = []
            self._pixmaps = []
            self._render_frames()
            return
        self._frames = [_zoom_png(data, self._scale) for data in self._base_frames]
        self._pixmaps = []
        for data in self._frames:
            pix = QPixmap()
            pix.loadFromData(data, 'PNG')
            self._pixmaps.append(pix)
        if self._pixmaps:
            self._w = self._pixmaps[0].width()
            self._h = self._pixmaps[0].height()
        self._preview_i = max(0, min(self._preview_i, len(self._pixmaps) - 1))
        self._render_frames()
        self._start_preview()

    def _load(self):
        self._user_picked = True
        self._loaded_key = None
        self._frames = []
        self._pixmaps = []
        self._preview_timer.stop()
        self._w = self._h = 0
        self.ok.setEnabled(False)
        self._render_frames()
        self._say('处理中…')
        try:
            frames, w, h = upload.normalize_frames(
                self._paths, self.max_w, self.max_h,
                keyout=self.keyout.isChecked())
        except ValueError as e:
            self._say(str(e), bad=True)
            self.stage.set_pixmap(None)
            return
        except Exception as e:                  # 坏文件 / 没有读权限
            self._say('读图失败：%s' % e, bad=True)
            self.stage.set_pixmap(None)
            return
        # normalize_frames 已经把画布撑成角色的最大包围盒；实际尺寸以 PNG 为准。
        try:
            self._w, self._h = Image.open(io.BytesIO(frames[0])).size
        except (OSError, ValueError):
            self._w, self._h = w, h
        self._preview_i = 0
        self._set_bases(frames, auto=True)
        if len(self._pixmaps) > 1:
            self._say('已裁成 %d×%d · %d 帧 · %.1ffps 循环'
                      % (w, h, len(frames), self._fps))
        else:
            self._say('已裁成 %d×%d' % (w, h))
        self.ok.setEnabled(True)

    def _load_preset(self, frames, fps=None, key=None, move=None, dur=None):
        """编辑已有动作：先把当前动画装进来，用户可以直接看到效果。"""
        self._clean_tmp()
        self._loaded_key = key
        self._user_picked = False
        if dur is not None:
            try:
                self.dur.setText('%.1f' % max(0.6, min(8.0, float(dur))))
            except (TypeError, ValueError):
                pass
        mv = move if isinstance(move, dict) else {}
        if hasattr(self, 'move_kind'):
            self.move_kind.blockSignals(True)
            self.move_h.blockSignals(True)
            self.move_d.blockSignals(True)
            self.move_kind.setCurrentIndex(max(
                0, self.move_kind.findData(mv.get('kind') or '')))
            try:
                h = float(mv.get('height') or 0.35)
            except (TypeError, ValueError):
                h = 0.35
            try:
                d = float(mv.get('distance') or 0.8)
            except (TypeError, ValueError):
                d = 0.8
            self.move_h.setValue(int(round(h * 100)))
            self.move_d.setValue(int(round(d * 100)))
            self.move_kind.blockSignals(False)
            self.move_h.blockSignals(False)
            self.move_d.blockSignals(False)
            self._move_labels()
        # 老动作的帧可能只有紧贴人物的画布；先补到角色的最大包围盒，
        # 这样「人物大小」往上调时不会一放大就裁头。
        self._base_frames = [_pad_frame(bytes(f), int(self.max_w),
                                        int(self.max_h)) for f in frames]
        self._paths = []
        # 预载帧只有图片字节、没有文件路径。落到临时目录当文件用，
        # 「＋」加图、去白底重算才有统一的输入（写基准帧，不写缩放后的）。
        self._tmp_dir = tempfile.mkdtemp(prefix='petclub-dlg-')
        for i, data in enumerate(self._base_frames):
            try:
                path = os.path.join(self._tmp_dir, 'preset-%d.png' % i)
                with open(path, 'wb') as f:
                    f.write(data)
                self._paths.append(path)
            except OSError:
                pass
        if self._base_frames:
            try:
                self._w, self._h = Image.open(
                    io.BytesIO(self._base_frames[0])).size
            except (OSError, ValueError):
                self._w = self._h = 0
        if fps is not None:
            try:
                self._fps = max(2.0, min(24.0, float(fps)))
            except (TypeError, ValueError):
                pass
        self.fps_slider.blockSignals(True)
        self.fps_slider.setValue(int(round(self._fps * 10)))
        self.fps_slider.blockSignals(False)
        self.fps_val.setText('%.1f' % self._fps)
        self._preview_i = 0
        # 编辑已有动作时不再自动放大：传进来的帧已经是用户上次确认过的样子。
        self._set_bases(self._base_frames, auto=False)
        self.ok.setEnabled(bool(self._pixmaps))
        self.alt_text.setText(
            '当前动作：%d 帧 · %.1ffps。可以删帧或重新选图。'
            % (len(self._frames), self._fps))

    def _start_preview(self):
        self._preview_timer.stop()
        if not self._pixmaps:
            return
        idx = self._preview_i % len(self._pixmaps)
        self._preview_i = idx
        self.stage.set_pixmap(self._pixmaps[idx])
        self.stage.set_offset(0.0, 0.0)
        self._move_prev_t = 0.0
        if not self.auto_box.isChecked():
            return
        # 只有一帧但带轨迹时也要跑定时器：静态姿势也能飞。
        if len(self._pixmaps) < 2 and not self._move_cfg():
            return
        self._preview_timer.setInterval(max(40, int(1000.0 / self._fps)))
        self._preview_timer.start()

    def _sync_play(self, *_a):
        self._start_preview()

    def _fps_changed(self, value):
        self._fps = max(2.0, min(24.0, value / 10.0))
        self.fps_val.setText('%.1f' % self._fps)
        self._start_preview()

    def _move_cfg(self):
        """当前选择的移动配置；静止返回 None。"""
        if not hasattr(self, 'move_kind'):
            return None
        kind = self.move_kind.currentData() or ''
        if not kind:
            return None
        return {'kind': kind, 'dir': 'lr',
                'height': round(self.move_h.value() / 100.0, 2),
                'distance': round(self.move_d.value() / 100.0, 2)}

    def _move_labels(self):
        """只刷新百分比和可用状态，不重开预览。"""
        if not hasattr(self, 'move_h_val'):
            return
        kind = self.move_kind.currentData() or ''
        moving = bool(kind)
        self.move_h.setEnabled(moving)
        self.move_d.setEnabled(moving and kind != 'hover')
        self.move_h_val.setText('%d%%' % self.move_h.value())
        self.move_d_val.setText('%d%%' % self.move_d.value())

    def _move_changed(self, *_a):
        """下拉/滑条变了：更新百分比，并在预览台上重放一遍轨迹。"""
        self._move_labels()
        if self._pixmaps:
            self._start_preview()

    def _render_frames(self):
        while self.frame_grid.count():
            item = self.frame_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        cells = []
        for i, pix in enumerate(self._pixmaps):
            cells.append(FrameCell(
                i, pix, self._remove_frame,
                on_select=self._select_frame, on_move=self._move_frame,
                selected=(i == self._preview_i)))
        # 和网页一样：最右侧留一个「＋」继续加图。
        cells.append(AddCell(self._pick_add))
        for i, w in enumerate(cells):
            self.frame_grid.addWidget(w, i // FRAME_COLS, i % FRAME_COLS)
        rows = max(1, (len(cells) + FRAME_COLS - 1) // FRAME_COLS)
        self.frame_scroll.setFixedHeight(
            rows * FrameCell.SIZE + (rows - 1) * FRAME_GAP + 2)
        # 只剩一张也留着：用户要能确认删完留下的是哪张。
        self.frame_scroll.setVisible(bool(self._pixmaps))
        if self.isVisible():
            self.resize(496, self.panel.sizeHint().height() + 36)

    def _select_frame(self, index):
        """点缩略图：选中它；自动播放关掉时舞台就停在这一张。"""
        if not (0 <= index < len(self._pixmaps)):
            return
        self._preview_i = index
        for i in range(self.frame_grid.count()):
            w = self.frame_grid.itemAt(i).widget()
            if isinstance(w, FrameCell):
                w.set_selected(w.index == index)
        if not self.auto_box.isChecked() or len(self._pixmaps) < 2:
            self.stage.set_offset(0.0, 0.0)
            self.stage.set_pixmap(self._pixmaps[index])

    def _move_frame(self, src, dst):
        """拖动排序：把第 src 帧挪到 dst 的位置。"""
        n = len(self._base_frames)
        if src == dst or not (0 <= src < n) or not (0 <= dst < n):
            return
        self._user_picked = True
        self._base_frames.insert(dst, self._base_frames.pop(src))
        self._preview_i = dst
        self._apply_scale()
        self._say('顺序已调整。')

    def _pick_add(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '再加几张图（最多 16 帧）', '',
            '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if paths:
            self._append_paths(paths)

    def _append_paths(self, paths):
        """追加新帧：输出和现有帧同尺寸、脚底对齐，服务端尺寸校验才过得去。"""
        if not self._base_frames:
            self._paths = list(paths)[:16]
            self._load()
            return
        room = 16 - len(self._base_frames)
        if room <= 0:
            self._say('最多 16 帧', bad=True)
            return
        keyout = self.keyout.isChecked()
        added = 0
        for path in list(paths)[:room]:
            try:
                pngs, _ow, _oh = upload.normalize_frames(
                    [path], self._w, self._h, keyout=keyout)
            except Exception:
                continue
            # normalize_frames 出来的已经是 (self._w × self._h) 的画布。
            self._base_frames.append(pngs[0])
            self._paths.append(path)
            added += 1
        if not added:
            self._say('新加的图读不出来，换几张吧', bad=True)
            return
        self._user_picked = True
        self._apply_scale()
        self.ok.setEnabled(True)
        self._say('已加到 %d 帧' % len(self._frames))

    def _remove_frame(self, index):
        if not (0 <= index < len(self._base_frames)):
            return
        self._user_picked = True
        del self._base_frames[index]
        if self._preview_i > index:
            self._preview_i -= 1
        self._preview_i = max(0, min(self._preview_i,
                                    len(self._base_frames) - 1))
        self._apply_scale()
        if self._pixmaps:
            self.ok.setEnabled(True)
            self._say('还剩 %d 帧' % len(self._frames))
        else:
            self.stage.set_pixmap(None)
            self.ok.setEnabled(False)
            self._say('至少留一帧', bad=True)

    def _tick_preview(self):
        if len(self._pixmaps) > 1:
            self._preview_i = (self._preview_i + 1) % len(self._pixmaps)
            self.stage.set_pixmap(self._pixmaps[self._preview_i])
        cfg = self._move_cfg()
        if not cfg:
            self.stage.set_offset(0.0, 0.0)
            return
        # 预览台就是一块缩小的屏幕：轨迹跑 2 秒一轮，结束正好回到原点。
        self._move_prev_t += self._preview_timer.interval() / 1000.0
        t = (self._move_prev_t / 2.0) % 1.0
        w = max(40.0, self.stage.width() - 2.0 * Stage.PAD)
        h = max(40.0, self.stage.height() - 2.0 * Stage.PAD)
        dx, dy = motion.offset(cfg, t, w, h, w / 2.0, w / 2.0,
                               h * 0.55, h * 0.45)
        self.stage.set_offset(dx, dy)

    def _clean_tmp(self):
        if self._tmp_dir:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None

    def done(self, result):
        self._preview_timer.stop()
        self._clean_tmp()
        super().done(result)

    # ---- 模式 ----

    def _key(self):
        for key, tile in self.tiles.items():
            if tile.isChecked():
                return key
        return ''

    def select(self, key):
        tile = self.tiles.get(key)
        if tile is not None:
            tile.setChecked(True)
            self._on_tile(key)

    def _on_tile(self, key):
        """切换动作格子：更新模式，需要时把该动作现有的动画装进弹层。"""
        self._sync_mode()
        if self._load_cb is None or key == self._loaded_key:
            return
        if self._user_picked:
            return
        try:
            got = self._load_cb(key) or ([], None, None)
        except Exception:
            got = ([], None, None)
        frames = got[0] if got else []
        fps = got[1] if len(got) > 1 else None
        move = got[2] if len(got) > 2 else None
        dur = got[3] if len(got) > 3 else None
        if frames:
            self._load_preset(frames, fps, key, move, dur)
        else:
            self._clear_frames(key)

    def _clear_frames(self, key=None):
        """切到还没图 / 新建动作：清掉上一组，别让旧图跟着上传。"""
        self._clean_tmp()
        if hasattr(self, 'dur'):
            self.dur.setText('2.0')
        if hasattr(self, 'move_kind'):
            self.move_kind.blockSignals(True)
            self.move_h.blockSignals(True)
            self.move_d.blockSignals(True)
            self.move_kind.setCurrentIndex(0)
            self.move_h.setValue(35)
            self.move_d.setValue(80)
            self.move_kind.blockSignals(False)
            self.move_h.blockSignals(False)
            self.move_d.blockSignals(False)
            self._move_labels()
        self._base_frames = []
        self._frames = []
        self._pixmaps = []
        self._paths = []
        self._w = self._h = 0
        self._preview_i = 0
        self._preview_timer.stop()
        self._loaded_key = key
        self._user_picked = False
        self._scale = 1.0
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(100)
        self.scale_slider.blockSignals(False)
        self.scale_val.setText('100%')
        self._render_frames()
        self.stage.set_pixmap(None)
        self.ok.setEnabled(False)
        self._say('')

    def _sync_mode(self):
        key = self._key()
        is_new = not key
        tile = self.tiles.get(key) if key else None
        custom = is_new or bool(tile is not None and tile.badge == '新增')
        self.ok.setText('上传' if is_new else '换这张')
        self.name_row.setVisible(is_new or custom)
        # 时长、大小只对自定义动作开放：内置姿势的时长/比例是语义的一部分。
        self.dur_row.setVisible(custom)
        self.scale_row.setVisible(custom)
        # 说明文字和网页同一句，并且固定在「移动」下面、按钮左边。
        self.alt.setVisible(True)
        if is_new:
            self.dur.setText('2.0')
            self.name.setText('')
            self.alt_text.setText(
                '可以多选；多张会按设定帧率循环。图会自动裁边、缩放到和这个角色一样大。')
            self.restore_btn.setVisible(False)
        else:
            t = self.tiles[key]
            if custom:
                # 自定义动作可以改名：和换图一起提交，key / 图都不变。
                self.name.setText(t.label)
                self.alt_text.setText(
                    '当前动作：%d 帧 · %.1ffps。可以删帧或重新选图。'
                    % (len(self._frames), self._fps))
            if t.badge == '新增':
                self.restore_btn.setVisible(bool(self._on_restore))
                self.restore_btn.setText('删掉这个动作')
            else:
                if not custom:
                    self.alt_text.setText('换掉「%s」现在的图，随时能换回。' % t.label)
                self.restore_btn.setText('恢复默认图')
                self.restore_btn.setVisible(bool(t.badge) and bool(self._on_restore))
        self.move_box.setVisible(custom)
        if self.isVisible():
            # 内置动作没有时长/轨迹这两块，窗口收回去，别留空白。
            self.resize(496, self.panel.sizeHint().height() + 36)

    def _restore(self):
        key = self._key()
        if key and self._on_restore:
            # 先关窗再回调：删除动作会弹确认框，套在弹层里会出现两层模态。
            self.reject()
            self._on_restore(key)

    def _say(self, text, bad=False):
        self.status.setText(text)
        self.status.setStyleSheet('color: %s;' % (ERR if bad else SUB))

    # ---- 结果 ----

    def payload(self):
        """调用方拿这个直接传给 upload.send。没选图就返回 None。"""
        if not self._frames:
            return None
        out = {'role': self.role, 'frames': list(self._frames),
               'fps': self._fps}
        key = self._key()
        # 时长和轨迹只属于自定义动作；内置姿势不带这些字段，服务端不动它。
        if not key or self.tiles[key].badge == '新增':
            out['move'] = self._move_cfg()
            try:
                d = float(self.dur.text().replace(',', '.'))
            except ValueError:
                d = 2.0
            out['dur'] = min(8.0, max(0.6, d))
        if key:
            out['action'] = key
            if self.tiles[key].badge == '新增':
                out['label'] = self.name.text().strip()
        else:
            out['label'] = self.name.text().strip()
        return out

    def accept(self):
        if not self._frames:
            self._say('先选一张图', bad=True)
            return
        key = self._key()
        if len(self._frames) > 1:
            # 内置姿势只有一张图：多帧动画属于用户自己新建的动作。
            if key and self.tiles[key].badge != '新增':
                self._say('内置姿势只能放一张图', bad=True)
                return
        if not key or self.tiles[key].badge == '新增':
            label = self.name.text().strip()
            if not label:
                self._say('给这个动作起个名字', bad=True)
                self.name.setFocus()
                return
            # 服务端也会拒（唯一真源），这里先拦一道：省一个来回，
            # 也不至于弹层关掉之后才发现名字不能用、还得重新选图。
            # 改名的动作保留自己原来的名字不算重名。
            current = self.tiles[key].label if key else ''
            if (norm_label(label) != norm_label(current)
                    and norm_label(label) in self._taken):
                self._say('「%s」已经有一个了，换个名字' % label, bad=True)
                self.name.setFocus()
                self.name.selectAll()
                return
        super().accept()

    # ---- 拖动（只有标题条能拖） ----

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            local = self.panel.mapFrom(self, e.position().toPoint())
            if local.y() <= 48:
                self._drag = (e.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)
