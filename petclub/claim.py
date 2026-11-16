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
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QVBoxLayout, QWidget)

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
        self.allow_taken = False     # 启用了口令：被占的身份也能点，进去再问顶不顶号
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
        if e.button() == Qt.LeftButton and (self.allow_taken or not self.taken):
            self._on_pick(self.role)
            e.accept()


class ClaimDialog(QDialog):
    """认领。六张立绘卡片始终都在，先选人：

    · 没启用口令：点一张选中，再点「进入」；
    · 启用了口令：选中的身份设了口令，卡片下方才出现一个口令框。
      口令只和选中的身份比，不会全局反查，别人撞了同一条口令也不串身份。
    口令错了 / 身份在别处在线，都会留在这一页重试（在线时另弹确认框）。
    """

    def __init__(self, parent, players=None, note='', on_poll=None,
                 auth=False, locked=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.players = players or {}
        self.on_poll = on_poll
        self.auth_mode = bool(auth)
        self.locked = set(locked or ())
        self.role = None
        self.cards = {}
        self.code_edit = None
        self.code_box = None

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

        tip = QLabel('先选一个身份，再输入属于他的认领口令' if self.auth_mode
                     else '挑一个身份坐下 —— 同一时间每个身份只能有一个人')
        tip.setFont(font(10))
        tip.setStyleSheet('color: %s;' % SUB)
        lay.addWidget(tip)
        lay.addSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(CARD_GAP)
        for role in ROLES:
            pix = self._preview(role)
            card = RoleCard(role, pix, self._pick)
            # 启用了口令：被占的身份也能点，进去再确认要不要顶号。
            card.allow_taken = self.auth_mode
            card.set_state(taken=bool(self.players.get(role)))
            self.cards[role] = card
            row.addWidget(card)
        lay.addLayout(row)
        lay.addSpacing(14)

        # 口令框：只有选中了「设了口令的身份」才出现，平时藏起来省地方。
        self.code_box = QWidget(body)
        cb = QVBoxLayout(self.code_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(7)
        self.code_who = QLabel('')
        self.code_who.setFont(font(9))
        self.code_who.setStyleSheet('color: %s;' % SUB)
        cb.addWidget(self.code_who)
        self.code_edit = QLineEdit(self.code_box)
        self.code_edit.setEchoMode(QLineEdit.Password)
        self.code_edit.setPlaceholderText('管理员发给你的认领口令')
        self.code_edit.setFont(font(10))
        self.code_edit.setMinimumHeight(38)
        self.code_edit.textChanged.connect(self._sync)
        self.code_edit.returnPressed.connect(self._ok)
        cb.addWidget(self.code_edit)
        self.code_box.setVisible(False)
        lay.addWidget(self.code_box)
        lay.addSpacing(10)

        self.note = QLabel(note or '')
        self.note.setFont(font(9))
        self.note.setStyleSheet('color: %s;' % (MUTED if not note else SUB))
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(20)
        lay.addWidget(self.note)
        lay.addSpacing(6)

        foot = QHBoxLayout()
        foot.addStretch(1)
        # 没有这个按钮，没认领的人就只能靠托盘菜单退出（窗口是 frameless，
        # 连关闭叉都没有）。reject 会让 exec() 返回 0，App 据此退出进程。
        foot.addWidget(RoundButton('退出', lambda: self.reject(),
                                   primary=False, width=76))
        self.ok_btn = RoundButton('进入', self._ok, primary=True, width=104)
        foot.addWidget(self.ok_btn)
        lay.addLayout(foot)
        self._sync()

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
        need = bool(self.role) and self.auth_mode and self.role in self.locked
        self.code_box.setVisible(need)
        if need:
            self.code_who.setText('「%s」的认领口令'
                                  % ROLE_NAME.get(self.role, self.role))
            ok = bool(self.code)
        else:
            ok = bool(self.role) and not self.players.get(self.role)
        self.ok_btn.set_enabled(ok)

    def set_players(self, players):
        self.players = players or {}
        self._sync()

    def _pick(self, role):
        # 启用了口令时，被占的身份也允许选中 —— 进入后由服务端回
        # role_online，客户端再弹「要不要让对方下线」的确认框。
        if not self.auth_mode and self.players.get(role):
            return
        self.role = role
        self._sync()

    @property
    def code(self):
        return self.code_edit.text().strip() if self.code_edit else ''

    def _ok(self):
        if not self.role:
            return
        need = self.auth_mode and self.role in self.locked
        if need and not self.code:
            return
        if not need and self.players.get(self.role):
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


class SetCodeDialog(QDialog):
    """改自己的认领口令，和网页端的弹层对齐：两次输入、至少 6 位。

    口令即身份 —— 改完旧口令立刻失效，别的设备要用新口令重新登录。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.code = ''

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget(self)
        body.setObjectName('code')
        body.setStyleSheet(
            '#code { background: %s; border: 1px solid %s; border-radius: 16px; }'
            % (WINDOW, BORDER))
        outer.addWidget(body)

        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(0)

        tip = QLabel('修改自己的认领口令 —— 改完旧口令立即失效')
        tip.setFont(font(9))
        tip.setStyleSheet('color: %s;' % SUB)
        lay.addWidget(tip)
        lay.addSpacing(14)

        self.code_edit = QLineEdit(body)
        self.code_edit.setEchoMode(QLineEdit.Password)
        self.code_edit.setPlaceholderText('新口令（至少 6 位）')
        self.code_edit.setFont(font(10))
        self.code_edit.setMinimumHeight(36)
        self.code_edit.setMinimumWidth(260)
        lay.addWidget(self.code_edit)
        lay.addSpacing(8)

        self.again_edit = QLineEdit(body)
        self.again_edit.setEchoMode(QLineEdit.Password)
        self.again_edit.setPlaceholderText('再输入一次')
        self.again_edit.setFont(font(10))
        self.again_edit.setMinimumHeight(36)
        self.again_edit.returnPressed.connect(self._ok)
        lay.addWidget(self.again_edit)
        lay.addSpacing(8)

        self.note = QLabel('')
        self.note.setFont(font(9))
        self.note.setStyleSheet('color: %s;' % MUTED)
        self.note.setMinimumHeight(18)
        lay.addWidget(self.note)
        lay.addSpacing(6)

        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(RoundButton('取消', self.reject,
                                   primary=False, width=76))
        foot.addWidget(RoundButton('保存', self._ok,
                                   primary=True, width=96))
        lay.addLayout(foot)

    def _ok(self):
        code = self.code_edit.text().strip()
        if len(code) < 6:
            self.note.setText('口令至少 6 位')
            return
        if code != self.again_edit.text().strip():
            self.note.setText('两次输入不一致')
            return
        self.code = code
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        screen = self.screen().availableGeometry()
        self.move(int(screen.center().x() - self.width() / 2),
                  int(screen.center().y() - self.height() / 2))
