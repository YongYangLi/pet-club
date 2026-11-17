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
                               QDialog, QFileDialog, QFrame,
                               QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

from PIL import Image

from . import upload
from .config import ROLE_NAME
from .custom import norm_label
from .theme import (ACCENT, ACCENT_DOWN, ACCENT_EDGE, ACCENT_SOFT, BORDER,
                    CARD_BG, ERR, FG, LINE, MUTED, SUB, font)

THUMB_H = 48        # 格子里缩略图的高度，和网页 .pose-thumb 一致
TILE_W = 72
TILE_H = 78
COLS = 5
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
        p.drawPixmap(int(round(inner.center().x() - w / 2)),
                     int(round(inner.center().y() - h / 2)),
                     int(round(w)), int(round(h)), self._pix)
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

    SIZE = 46

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
        self.setFixedSize(self.SIZE + 8, self.SIZE + 8)
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
        r = QRectF(2, 2, self.SIZE + 4, self.SIZE + 4)
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
        p.drawText(QRectF(4, self.height() - 14, 20, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, str(self.index + 1))
        # 右上角红叉
        c = QPointF(self.width() - 7, 7)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#d6455d'))
        p.drawEllipse(c, 7, 7)
        p.setPen(QPen(QColor('#ffffff'), 1.4))
        p.drawLine(QPointF(c.x() - 2.6, c.y() - 2.6),
                   QPointF(c.x() + 2.6, c.y() + 2.6))
        p.drawLine(QPointF(c.x() + 2.6, c.y() - 2.6),
                   QPointF(c.x() - 2.6, c.y() + 2.6))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().x() >= self.width() - 16 \
                and e.position().y() <= 16:
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
        self.setFixedSize(self.SIZE + 8, self.SIZE + 8)
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
        p.drawRoundedRect(QRectF(2, 2, self.SIZE + 4, self.SIZE + 4), 6, 6)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        p.setPen(QPen(QColor(ACCENT), 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - 7, cy), QPointF(cx + 7, cy))
        p.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 7))
        p.end()


class UploadDialog(QDialog):
    """targets: [{'key','label','icon','tip','badge'}]，第一项是「新建动作」（key 为空）。"""

    def __init__(self, role, max_box, targets, on_restore=None, taken=(),
                 parent=None, preset_frames=None, preset_fps=None,
                 preset_key='', load_preset=None):
        super().__init__(parent)
        self.role = role
        self.max_w, self.max_h = max_box
        self._on_restore = on_restore
        self._load_cb = load_preset
        self._loaded_key = None      # 当前这组帧是从哪个动作预载进来的
        self._user_picked = False    # 用户自己选过图，就别再被预载覆盖
        # 已经被占用的名字（我自己的自定义动作 + 内置动作）。
        # 名字是「跟着做」的配对键：自己重名就不知道该演哪个。
        self._taken = {norm_label(t) for t in taken if t}
        self._frames = []
        self._w = self._h = 0
        self._paths = []
        self._pixmaps = []
        self._preview_i = 0
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
        self.frame_scroll = QScrollArea()
        self.frame_scroll.setObjectName('frames')
        self.frame_scroll.setWidgetResizable(True)
        self.frame_scroll.setFixedHeight(62)
        self.frame_scroll.setFrameShape(QFrame.NoFrame)
        self.frame_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.frame_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.frame_host = QWidget()
        self.frame_host.setAutoFillBackground(False)
        self.frame_host.setStyleSheet('background: #ffffff;')
        self.frame_row = QHBoxLayout(self.frame_host)
        self.frame_row.setContentsMargins(0, 0, 0, 0)
        self.frame_row.setSpacing(6)
        self.frame_row.addStretch(1)
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
        fl = QGridLayout(self.fields)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setHorizontalSpacing(10)
        fl.setVerticalSpacing(8)
        name_lab = _hint('名字')
        self.name = QLineEdit()
        self.name.setMaxLength(10)
        self.name.setPlaceholderText('比如 跳舞、比耶')
        self.name.returnPressed.connect(self.accept)
        dur_lab = _hint('时长')
        self.dur = QLineEdit('2.0')
        self.dur.setValidator(QDoubleValidator(0.6, 8.0, 1, self))
        self.dur.setFixedWidth(86)
        self.dur.returnPressed.connect(self.accept)
        dur_row = QHBoxLayout()
        dur_row.setSpacing(8)
        dur_row.addWidget(self.dur)
        unit = QLabel('秒')
        unit.setFont(font(9))
        unit.setStyleSheet('color: %s;' % MUTED)
        dur_row.addWidget(unit)
        dur_row.addStretch(1)
        fl.addWidget(name_lab, 0, 0)
        fl.addWidget(self.name, 0, 1)
        fl.addWidget(dur_lab, 1, 0)
        fl.addLayout(dur_row, 1, 1)
        fl.setColumnStretch(1, 1)

        # 换图时这块不空着，也不让窗口变矮：换成一句说明 +「恢复默认图」，
        # 正好补上网页弹层有、桌面端之前只能在「我的动作」菜单里找的入口。
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

        self.stack = QStackedWidget()
        self.stack.setFixedHeight(76)
        self.stack.addWidget(self.fields)
        self.stack.addWidget(self.alt)
        bl.addWidget(self.stack)

        self.note = QLabel('图会自动裁边、缩放到和这个角色一样大，别人也能看到。')
        self.note.setWordWrap(True)
        self.note.setFont(font(9))
        self.note.setStyleSheet('color: %s;' % MUTED)
        bl.addWidget(self.note)
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
        QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
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
        self._frames = frames
        self._w = w
        self._h = h
        for data in frames:
            pix = QPixmap()
            pix.loadFromData(data, 'PNG')
            self._pixmaps.append(pix)
        self._preview_i = 0
        self._render_frames()
        self._start_preview()
        if len(self._pixmaps) > 1:
            self._say('已裁成 %d×%d · %d 帧 · %.1ffps 循环'
                      % (w, h, len(frames), self._fps))
        else:
            self._say('已裁成 %d×%d' % (w, h))
        self.ok.setEnabled(True)

    def _load_preset(self, frames, fps=None, key=None):
        """编辑已有动作：先把当前动画装进来，用户可以直接看到效果。"""
        self._clean_tmp()
        self._loaded_key = key
        self._user_picked = False
        self._frames = list(frames)
        self._pixmaps = []
        self._paths = []
        # 预载帧只有图片字节、没有文件路径。落到临时目录当文件用，
        # 「＋」加图、去白底重算才有统一的输入。
        self._tmp_dir = tempfile.mkdtemp(prefix='petclub-dlg-')
        for i, data in enumerate(self._frames):
            pix = QPixmap()
            pix.loadFromData(data, 'PNG')
            self._pixmaps.append(pix)
            try:
                path = os.path.join(self._tmp_dir, 'preset-%d.png' % i)
                with open(path, 'wb') as f:
                    f.write(data)
                self._paths.append(path)
            except OSError:
                pass
        if self._pixmaps:
            self._w = self._pixmaps[0].width()
            self._h = self._pixmaps[0].height()
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
        self._render_frames()
        self._start_preview()
        self.ok.setEnabled(bool(self._pixmaps))
        self._say('当前动作：%d 帧 · %.1ffps' % (len(self._frames), self._fps))

    def _start_preview(self):
        self._preview_timer.stop()
        if not self._pixmaps:
            return
        idx = self._preview_i % len(self._pixmaps)
        self._preview_i = idx
        self.stage.set_pixmap(self._pixmaps[idx])
        if not self.auto_box.isChecked() or len(self._pixmaps) < 2:
            return
        self._preview_timer.setInterval(max(40, int(1000.0 / self._fps)))
        self._preview_timer.start()

    def _sync_play(self, *_a):
        self._start_preview()

    def _fps_changed(self, value):
        self._fps = max(2.0, min(24.0, value / 10.0))
        self.fps_val.setText('%.1f' % self._fps)
        self._start_preview()

    def _render_frames(self):
        while self.frame_row.count():
            item = self.frame_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for i, pix in enumerate(self._pixmaps):
            self.frame_row.addWidget(FrameCell(
                i, pix, self._remove_frame,
                on_select=self._select_frame, on_move=self._move_frame,
                selected=(i == self._preview_i)))
        # 和网页一样：最右侧留一个「＋」继续加图。
        self.frame_row.addWidget(AddCell(self._pick_add))
        self.frame_row.addStretch(1)
        # 只剩一张也留着：用户要能确认删完留下的是哪张。
        self.frame_scroll.setVisible(bool(self._pixmaps))

    def _select_frame(self, index):
        """点缩略图：选中它；自动播放关掉时舞台就停在这一张。"""
        if not (0 <= index < len(self._pixmaps)):
            return
        self._preview_i = index
        for i in range(self.frame_row.count()):
            w = self.frame_row.itemAt(i).widget()
            if isinstance(w, FrameCell):
                w.set_selected(w.index == index)
        if not self.auto_box.isChecked() or len(self._pixmaps) < 2:
            self.stage.set_pixmap(self._pixmaps[index])

    def _move_frame(self, src, dst):
        """拖动排序：把第 src 帧挪到 dst 的位置。"""
        n = len(self._frames)
        if src == dst or not (0 <= src < n) or not (0 <= dst < n):
            return
        self._user_picked = True
        self._frames.insert(dst, self._frames.pop(src))
        self._pixmaps.insert(dst, self._pixmaps.pop(src))
        self._preview_i = dst
        self._render_frames()
        self._start_preview()
        self._say('顺序已调整。')

    def _pick_add(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '再加几张图（最多 16 帧）', '',
            '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if paths:
            self._append_paths(paths)

    def _append_paths(self, paths):
        """追加新帧：输出和现有帧同尺寸、脚底对齐，服务端尺寸校验才过得去。"""
        if not self._frames:
            self._paths = list(paths)[:16]
            self._load()
            return
        room = 16 - len(self._frames)
        if room <= 0:
            self._say('最多 16 帧', bad=True)
            return
        keyout = self.keyout.isChecked()
        added = 0
        for path in list(paths)[:room]:
            try:
                pngs, ow, oh = upload.normalize_frames(
                    [path], self._w, self._h, keyout=keyout)
            except Exception:
                continue
            try:
                part = Image.open(io.BytesIO(pngs[0])).convert('RGBA')
            except OSError:
                continue
            out = Image.new('RGBA', (self._w, self._h), (0, 0, 0, 0))
            out.paste(part, ((self._w - ow) // 2, self._h - oh))
            buf = io.BytesIO()
            out.save(buf, 'PNG', optimize=True)
            data = buf.getvalue()
            self._frames.append(data)
            pix = QPixmap()
            pix.loadFromData(data, 'PNG')
            self._pixmaps.append(pix)
            self._paths.append(path)
            added += 1
        if not added:
            self._say('新加的图读不出来，换几张吧', bad=True)
            return
        self._user_picked = True
        self._preview_i = max(0, min(self._preview_i,
                                    len(self._pixmaps) - 1))
        self._render_frames()
        self._start_preview()
        self.ok.setEnabled(True)
        self._say('已加到 %d 帧' % len(self._frames))

    def _remove_frame(self, index):
        if not (0 <= index < len(self._frames)):
            return
        self._user_picked = True
        del self._frames[index]
        del self._pixmaps[index]
        if self._preview_i > index:
            self._preview_i -= 1
        self._preview_i = max(0, min(self._preview_i,
                                    len(self._pixmaps) - 1))
        self._render_frames()
        if self._pixmaps:
            self.stage.set_pixmap(self._pixmaps[self._preview_i])
            self._start_preview()
            self.ok.setEnabled(True)
            self._say('还剩 %d 帧' % len(self._frames))
        else:
            self.stage.set_pixmap(None)
            self.ok.setEnabled(False)
            self._say('至少留一帧', bad=True)

    def _tick_preview(self):
        if len(self._pixmaps) < 2:
            return
        self._preview_i = (self._preview_i + 1) % len(self._pixmaps)
        self.stage.set_pixmap(self._pixmaps[self._preview_i])

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
            frames, fps = self._load_cb(key) or ([], None)
        except Exception:
            frames, fps = [], None
        if frames:
            self._load_preset(frames, fps, key)
        else:
            self._clear_frames(key)

    def _clear_frames(self, key=None):
        """切到还没图 / 新建动作：清掉上一组，别让旧图跟着上传。"""
        self._clean_tmp()
        self._frames = []
        self._pixmaps = []
        self._paths = []
        self._w = self._h = 0
        self._preview_i = 0
        self._preview_timer.stop()
        self._loaded_key = key
        self._user_picked = False
        self._render_frames()
        self.stage.set_pixmap(None)
        self.ok.setEnabled(False)
        self._say('')

    def _sync_mode(self):
        is_new = not self._key()
        self.ok.setText('上传' if is_new else '换这张')
        self.stack.setCurrentIndex(0 if is_new else 1)
        self.note.setText('图会自动裁边、缩放到和这个角色一样大，别人也能看到。'
                          if is_new else '换完所有人都能看到新的样子。')
        if not is_new:
            t = self.tiles[self._key()]
            if t.badge == '新增':
                self.alt_text.setText('这是你自己加的动作，换一张图就是改它的样子。')
                self.restore_btn.setVisible(bool(self._on_restore))
                self.restore_btn.setText('删掉这个动作')
            else:
                self.alt_text.setText('换掉「%s」现在的图，随时能换回。' % t.label)
                self.restore_btn.setText('恢复默认图')
                self.restore_btn.setVisible(bool(t.badge) and bool(self._on_restore))

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
        if key:
            out['action'] = key
        else:
            out['label'] = self.name.text().strip()
            try:
                out['dur'] = float(self.dur.text().replace(',', '.'))
            except ValueError:
                out['dur'] = 2.0
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
        if not key:
            label = self.name.text().strip()
            if not label:
                self._say('给这个动作起个名字', bad=True)
                self.name.setFocus()
                return
            # 服务端也会拒（唯一真源），这里先拦一道：省一个来回，
            # 也不至于弹层关掉之后才发现名字不能用、还得重新选图。
            if norm_label(label) in self._taken:
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
