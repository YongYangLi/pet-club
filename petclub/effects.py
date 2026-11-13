# -*- coding: utf-8 -*-
"""纯视觉烟花：一块全屏透明、点击穿透的覆盖层。

不占角色素材，也不进动作表 —— 放完自己关掉。桌面端用它回应
`kind='fx'` 的互动事件，网页端有一份等价的 canvas 实现。
"""

import math
import random

from PySide6.QtCore import QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .host import work_area

COLORS = ('#ff5c8a', '#ffd166', '#6c7bff', '#4dd4ac', '#ff8c42',
          '#b388ff', '#ff6b6b', '#5ad1ff')


class Fireworks(QWidget):
    """全屏透明粒子层。用完由 App 收走引用，别让 Python 当场析构。"""

    finished = Signal()

    def __init__(self, parent=None):
        flags = (Qt.Tool | Qt.FramelessWindowHint
                 | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        left, top, right, bottom = work_area()
        self.setGeometry(left, top, right - left, bottom - top)
        self.parts = []
        self.t = 0.0
        self._second = False
        for _ in range(3):
            self._burst()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _burst(self):
        w, h = max(1, self.width()), max(1, self.height())
        cx = w * random.uniform(0.18, 0.82)
        cy = h * random.uniform(0.16, 0.58)
        for _ in range(random.randint(55, 85)):
            angle = random.uniform(0, 6.28318)
            speed = random.uniform(90, 350)
            self.parts.append({
                'x': cx, 'y': cy,
                'vx': math.cos(angle) * speed,
                'vy': math.sin(angle) * speed - 40,
                'life': 0.0,
                'max': random.uniform(1.1, 2.2),
                'color': random.choice(COLORS),
                'size': random.uniform(1.5, 4.2),
            })

    def _tick(self):
        dt = 0.016
        self.t += dt
        if self.t > 0.65 and not self._second:
            self._second = True
            self._burst()
        alive = []
        for p in self.parts:
            p['life'] += dt
            if p['life'] >= p['max']:
                continue
            p['vy'] += 260 * dt
            p['vx'] *= 0.992
            p['x'] += p['vx'] * dt
            p['y'] += p['vy'] * dt
            alive.append(p)
        self.parts = alive
        self.update()
        if self.t > 3.8 and not self.parts:
            self.stop()

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
        self.hide()
        self.finished.emit()

    def paintEvent(self, _e):
        if not self.parts:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        for it in self.parts:
            k = 1.0 - it['life'] / it['max']
            c = QColor(it['color'])
            c.setAlphaF(max(0.0, min(1.0, k)))
            p.setBrush(c)
            p.drawEllipse(QPointF(it['x'], it['y']), it['size'], it['size'])
        p.end()
