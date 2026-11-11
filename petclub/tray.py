# -*- coding: utf-8 -*-
"""托盘图标（QSystemTrayIcon）。

行为：
  · 左键 = 打开聊天（有未读落到最新那条）
  · 右键 = 菜单（始终置顶 / 检查更新 / 换身份 / 退出）
  · 有未读时两张图标交替闪，悬停提示带条数
"""
import os

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class Tray:

    def __init__(self, icon=None, alert=None, tip='pet-club', on_left=None,
                 on_right=None):
        self.on_left = on_left
        self.on_right = on_right
        self.ok = False
        self._on = False
        self._icon = QIcon(icon) if icon and os.path.exists(icon) else QIcon()
        self._alert = (QIcon(alert) if alert and os.path.exists(alert)
                       else QIcon())
        if self._icon.isNull():
            self._icon = QIcon.fromTheme('application-x-executable')
        self._tray = QSystemTrayIcon(self._icon)
        self._tray.setToolTip(tip)
        self._tray.activated.connect(self._activated)
        if self._tray.isSystemTrayAvailable():
            self._tray.show()
            self.ok = True
        else:
            print('[pet-club] 系统托盘不可用')

    @property
    def can_blink(self):
        return self.ok and not self._alert.isNull()

    def blink(self, on):
        """这一拍显示哪张。on=True 是带红点的那张。"""
        if not self.ok:
            return False
        if on and self.can_blink:
            self._tray.setIcon(self._alert)
            self._on = True
        else:
            self._tray.setIcon(self._icon)
            self._on = False
        return self._on

    def set_tip(self, text):
        if self.ok:
            self._tray.setToolTip(text)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger and self.on_left:
            self.on_left()
        elif reason == QSystemTrayIcon.Context and self.on_right:
            self.on_right()

    def close(self):
        self._tray.hide()
        self.ok = False


def build_menu(parent, rows, checked=None):
    """把 [(文字, 回调, 是否可点)] 铺成 QMenu。

    QMenu 不能在建好后「重新铺一遍」，但 QAction.setText 是有效的：菜单弹着
    的时候改文案，Qt 会重画。所以这里返回 (menu, actions)，由调用方挂一个
    QTimer 自己改倒计时。
    """
    menu = QMenu(parent)
    actions = []
    for item in rows:
        if item is None:
            menu.addSeparator()
            continue
        text, cmd = item[0], item[1]
        enabled = item[2] if len(item) > 2 else True
        if cmd is None:
            act = menu.addAction(text)
            act.setEnabled(False)
        else:
            act = menu.addAction(text)
            act.setEnabled(enabled)
            act.triggered.connect(cmd)
        actions.append(act)
    return menu, actions
