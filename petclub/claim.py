# -*- coding: utf-8 -*-
"""认领窗。

五张立绘卡片，点一张选中、再点「进入」才提交；
别人先认领了某个身份，卡片当场变灰。

用 QDialog.exec() 做模态。
模态期间网络不能停：窗里挂一个 150ms 的定时器拉 net.poll()，
把 state 消息直接吞掉（用来刷新卡片），其余塞回 App 的 _stash。
"""
import os

from PIL import Image
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .config import ROLE_NAME, ROLES, assets_dir
from .theme import (ACCENT, ACCENT_SOFT, BORDER, FG, MUTED, OK_BG, OK_FG, SUB,
                   TAKEN_BG, TAKEN_FG, WINDOW, RoundButton, font)
from .windows import to_qimage

CARD_W, CARD_H = 134, 208
CARD_GAP = 13


class RoleCard(QWidget):
    """一张身份卡片：立绘 + 名字 + 状态角标。"""

    def __init__(self, role, pix, on_pick, parent=None):
        super().__init__(parent)
        self.role = role
        self._pix = pix              # 已按卡片尺寸缩放好的 QPixmap
        self._on_pick = on_pick
        self.taken = False
        self.selected = False
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(10))

    def set_state(self, taken=None, selected=None):
        if taken is not None:
            self.taken = bool(taken)
        if selected is not None:
            self.selected = bool(selected)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        bg = TAKEN_BG if self.taken else (ACCENT_SOFT if self.selected else '#ffffff')
        edge = ACCENT if self.selected else BORDER
        p.setPen(QPen(QColor(edge), 2 if self.selected else 1))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(QRectF(0.5, 0.5, CARD_W - 1, CARD_H - 1), 12, 12)

        if self._pix is not None:
            if self.taken:
                p.setOpacity(0.35)
            p.drawPixmap(int((CARD_W - self._pix.width()) / 2),
                         int(CARD_H - 44 - self._pix.height()), self._pix)
            p.setOpacity(1.0)

        p.setPen(QColor(TAKEN_FG if self.taken else FG))
        p.setFont(font(10, True))
        p.drawText(QRectF(0, CARD_H - 40, CARD_W, 20), Qt.AlignCenter,
                   ROLE_NAME.get(self.role, self.role))

        if self.taken:
            tag, fg, bg2 = '已被认领', TAKEN_FG, TAKEN_BG
        elif self.selected:
            tag, fg, bg2 = '就是你', ACCENT, '#ffffff'
        else:
            tag, fg, bg2 = '可选', OK_FG, OK_BG
        p.setFont(font(8, True))
        tw = p.fontMetrics().horizontalAdvance(tag) + 14
        bx = (CARD_W - tw) / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(bg2))
        p.drawRoundedRect(QRectF(bx, CARD_H - 20, tw, 16), 8, 8)
        p.setPen(QColor(fg))
        p.drawText(QRectF(bx, CARD_H - 20, tw, 16), Qt.AlignCenter, tag)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.taken:
            self._on_pick(self.role)
            e.accept()


class ClaimDialog(QDialog):
    """选身份。accept 时 `role` 就是选中的那个。"""

    def __init__(self, parent, players=None, note='', on_poll=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.players = players or {}
        self.on_poll = on_poll
        self.role = None
        self.cards = {}

        self._build(note)
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll)
        if on_poll is not None:
            self._timer.start()

    def _build(self, note):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget(self)
        body.setObjectName('claim')
        body.setStyleSheet(
            '#claim { background: %s; border: 1px solid %s; border-radius: 16px; }'
            % (WINDOW, BORDER))
        outer.addWidget(body)

        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(0)

        tip = QLabel('挑一个身份坐下 —— 同一时间每个身份只能有一个人')
        tip.setFont(font(10))
        tip.setStyleSheet('color: %s;' % SUB)
        lay.addWidget(tip)
        lay.addSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(CARD_GAP)
        for i, role in enumerate(ROLES):
            pix = self._preview(role)
            card = RoleCard(role, pix, self._pick)
            card.set_state(taken=bool(self.players.get(role)))
            self.cards[role] = card
            row.addWidget(card)
        lay.addLayout(row)
        lay.addSpacing(16)

        self.note = QLabel(note or '')
        self.note.setFont(font(9))
        self.note.setStyleSheet('color: %s;' % (MUTED if not note else SUB))
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(20)
        lay.addWidget(self.note)
        lay.addSpacing(6)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.ok_btn = RoundButton('进入', self._ok, primary=True, width=104)
        self.ok_btn.set_enabled(False)
        foot.addWidget(self.ok_btn)
        lay.addLayout(foot)

    @staticmethod
    def _preview(role):
        p = os.path.join(assets_dir(), 'roles', role, 'wave.png')
        if not os.path.exists(p):
            p = os.path.join(assets_dir(), 'roles', role, 'idle.png')
        try:
            im = Image.open(p).convert('RGBA')
        except (OSError, ValueError):
            return None
        # 卡片里留出名字和角标的位置
        box = im.split()[3].getbbox()
        if box:
            im = im.crop(box)
        h = CARD_H - 60
        w = max(1, int(round(im.width * h / float(im.height))))
        if w > CARD_W - 12:
            w = CARD_W - 12
            h = max(1, int(round(im.height * w / float(im.width))))
        im = im.resize((w, h), Image.LANCZOS)
        return QPixmap.fromImage(to_qimage(im))

    # ---- 状态 ----

    def _poll(self):
        if self.on_poll is None:
            return
        self.on_poll()
        self._sync()

    def _sync(self):
        for role, card in self.cards.items():
            card.set_state(taken=bool(self.players.get(role)),
                           selected=(role == self.role))
        ok = bool(self.role) and not self.players.get(self.role)
        self.ok_btn.set_enabled(ok)

    def set_players(self, players):
        self.players = players or {}
        self._sync()

    def _pick(self, role):
        if self.players.get(role):
            return
        self.role = role
        self._sync()

    def _ok(self):
        if not self.role or self.players.get(self.role):
            return
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        # 居中到主屏
        screen = self.screen().availableGeometry()
        self.move(int(screen.center().x() - self.width() / 2),
                  int(screen.center().y() - self.height() / 2))

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)

    def set_note(self, text):
        self.note.setText(text or '')

    def note_text(self):
        return self.note.text()
