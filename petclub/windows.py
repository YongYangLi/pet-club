# -*- coding: utf-8 -*-
"""桌面层：角色窗口与气泡。

几个绕不开的点：

【透明】两个声明：FramelessWindowHint（没有标题栏）+
  WA_TranslucentBackground（按 alpha 合成）。抗锯齿边缘、半透明、预乘 alpha
  全由 Qt 处理。

【点击穿透】分层窗口的透明像素天然不参与命中测试，Qt 的窗口却是矩形 ——
  不处理的话，角色周围那一大圈透明区域会把桌面图标挡住，用户点不到。
  所以按当前姿势的 alpha 生成一个 QRegion mask：只有画到人的地方才吃鼠标。
  mask 只在换姿势时重算（缓存），不是每帧。

【DPI】素材是物理像素，Qt 的窗口尺寸是逻辑像素。这里给每张图设
  devicePixelRatio = 屏幕缩放，窗口尺寸取 物理尺寸 / 缩放 ——
  于是 1 个图像像素永远落在一个屏幕像素上，150% 缩放下也是清楚的。

【坐标】对外一律用**屏幕逻辑坐标**（= Qt 的坐标），脚底中心对齐。
"""
import sys

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import (QBitmap, QColor, QFont, QFontMetrics, QImage,
                           QPainter, QPen, QPixmap, QRegion)
from PySide6.QtWidgets import QApplication, QWidget

# 气泡配色，和认领窗/聊天面板同一套
BUBBLE_BG = '#ffffff'
BUBBLE_FG = '#111827'
BUBBLE_EDGE = '#e4e7ef'
BUBBLE_FONT = 'Microsoft YaHei UI'


def to_qimage(im):
    """PIL.Image(RGBA) → QImage。必须 copy：QImage 不接管那块 bytes 的生命周期。"""
    im = im.convert('RGBA')
    q = QImage(im.tobytes('raw', 'RGBA'), im.width, im.height,
               QImage.Format_RGBA8888)
    return q.copy()


class PetWindow(QWidget):
    """一只角色的窗口。位置 = 脚底中心。

    owner 是 App：按下/拖动/松开/右键都回调给它，这边不判断业务
    （拖动要不要算单击、点别人还是自己，全在 App 里）。
    """

    def __init__(self, owner, sp):
        super().__init__(None)
        self.owner = owner
        self.sp = sp
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        screen = QApplication.primaryScreen()
        self.dpr = float(screen.devicePixelRatio()) if screen else 1.0
        # 对外（App / Pet）一律用物理像素（和素材同一套坐标）；
        # 只有 setFixedSize / move 这两个 Qt 接口要换成逻辑像素。
        self.win_w = float(sp.w)
        self.win_h = float(sp.h)
        self._lw = sp.w / self.dpr
        self._lh = sp.h / self.dpr
        self.setFixedSize(max(1, int(round(self._lw))),
                          max(1, int(round(self._lh))))

        self._pix = {}
        self._qim = {}
        for name, im in sp.poses.items():
            q = to_qimage(im)
            self._qim[name] = q                 # 物理尺寸，生成 mask 用
            pm = QPixmap.fromImage(q)
            pm.setDevicePixelRatio(self.dpr)
            self._pix[name] = pm
        self._masks = {}
        self._hit_cache = {}
        self._pose = 'idle'
        self.role = None            # App 建好窗口后填：反查用（命中测试、菜单）
        self._apply_mask('idle')    # 第一次也要装 mask，否则窗口是整块矩形
        self._x = 0.0
        self._y = 0.0
        super().show()                  # 注意不能写 self.show()：下面重载了同名方法

    # ---- 画 ----

    def show(self, pose, x, y):
        """换姿势 + 挪位置。pose 不认识就退回 idle（和 sprites.get 一致）。

        注意这个方法**重载了 QWidget.show()**（`Pet.update` 每帧调
        `win.show(pose, x, y)`）。所以本类内部要显示窗口一律用
        `super().show()`，绝不能写 `self.show()`。
        """
        if pose != self._pose:
            self._pose = pose
            self._apply_mask(pose)
            self.update()
        self.move_to(x, y)
        if not self.isVisible():
            super().show()

    def move_to(self, x, y):
        """(x, y) 是脚底中心的屏幕坐标（物理像素）。"""
        self._x = float(x)
        self._y = float(y)
        lx = int(round(x / self.dpr - self._lw / 2.0))
        ly = int(round(y / self.dpr - self._lh))
        if (lx, ly) != (self.x(), self.y()):
            self.move(lx, ly)

    def _apply_mask(self, pose):
        """给当前姿势装一个「只有人身上才吃鼠标」的区域。

        不能用 QPixmap.mask()：那张 pixmap 设了 devicePixelRatio，
        mask() 在某些缩放比下直接返回空。老老实实从 alpha 生成 1bit 蒙版，
        再按绘制位置平移到窗口坐标系。
        """
        region = self._masks.get(pose)
        if region is None:
            q = self._qim.get(pose) or next(iter(self._qim.values()))
            bm = QBitmap.fromImage(q.createAlphaMask())
            if bm.isNull():
                return
            # 物理像素 → 逻辑像素（和 paintEvent 里的绘制尺寸同一个换算）
            w = max(1, int(round(q.width() / self.dpr)))
            h = max(1, int(round(q.height() / self.dpr)))
            bm = QBitmap(bm.scaled(w, h))   # scaled 返回 QPixmap，包回来
            region = QRegion(bm)
            dx = self.sp.dx.get(pose, 0) / self.dpr
            region.translate(int(round((self._lw - w) / 2.0 + dx)),
                             int(round(self._lh - h)))
            self._masks[pose] = region
        self.setMask(region)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pm = self._pix.get(self._pose) or next(iter(self._pix.values()))
        # 底部居中；循环帧的水平修正沿用 sprites 里的 dx（物理像素 → 逻辑像素）
        dx = self.sp.dx.get(self._pose, 0) / self.dpr
        w = pm.width() / self.dpr
        h = pm.height() / self.dpr
        p.drawPixmap(QPoint(int(round((self._lw - w) / 2.0 + dx)),
                            int(round(self._lh - h))), pm)
        p.end()

    # ---- 鼠标 ----

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
            self.owner.on_pet_press(self, e.globalPosition().toPoint())
            e.accept()
        elif e.button() == Qt.RightButton:
            self.owner.on_pet_menu(self, e.globalPosition().toPoint())
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.owner.on_pet_drag(self, e.globalPosition().toPoint())
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)
            self.owner.on_pet_drop(self, e.globalPosition().toPoint())
            e.accept()

    # ---- 置顶 ----

    def hit_point(self):
        """角色身上一个不透明的点（屏幕物理坐标），给置顶巡检做命中测试。

        必须落在人身上：落在透明处会一路穿透到桌面，被误判成「被盖住了」，
        然后每 3 秒白白插一次队。
        """
        pm = self._pix.get(self._pose)
        if pm is None:
            return None
        off = self._hit_cache.get(self._pose)
        if off is None:
            img = pm.toImage()
            cx = img.width() // 2
            off = None
            for y in range(0, img.height(), 4):
                if img.pixelColor(cx, y).alpha() > 160:
                    off = (cx, y)
                    break
            if off is None:
                for y in range(0, img.height(), 4):
                    for x in range(0, img.width(), 4):
                        if img.pixelColor(x, y).alpha() > 160:
                            off = (x, y)
                            break
                    if off:
                        break
            self._hit_cache[self._pose] = off
        if off is None:
            return None
        left = self._x - self.win_w / 2.0
        top = self._y - self.win_h
        return (int(left + off[0]), int(top + off[1]))

    def reassert_top(self):
        """重新插回置顶带：置顶窗口是一个共享带，谁最后激活谁在上面。"""
        if sys.platform == 'win32':
            from . import win32
            win32.raise_top(int(self.winId()))
        else:
            self.raise_()

    def apply_top(self, on):
        """切「始终置顶」：只改 z 序属性，不重建窗口（保住 mask 和位置）。"""
        if sys.platform == 'win32':
            from . import win32
            win32.set_topmost(int(self.winId()), on)
            if on:
                self.reassert_top()
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            if on:
                self.raise_()

    def destroy(self):
        self.hide()
        self.deleteLater()


class Bubble(QWidget):
    """角色头顶的一句话气泡：白底、圆角、1px 描边，没有尾巴。"""

    PAD_X = 13
    PAD_Y = 9
    MAX_W = 260

    def __init__(self, owner):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text = ''
        self.dpr = float(QApplication.primaryScreen().devicePixelRatio())
        self._font = QFont(BUBBLE_FONT, 10)
        self._fm = QFontMetrics(self._font)
        self.resize(10, 10)
        self.hide()

    def set_text(self, text):
        self._text = text or ''
        # 先量，再截断：超过 MAX_W 就折行（最多 4 行，再长就省略）
        rect = self._fm.boundingRect(QRectF(0, 0, self.MAX_W, 10000).toRect(),
                                     Qt.TextWordWrap, self._text)
        w = min(self.MAX_W, max(24, rect.width())) + self.PAD_X * 2
        h = min(rect.height(), self._fm.lineSpacing() * 4) + self.PAD_Y * 2
        self.resize(int(w), int(h))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(BUBBLE_EDGE), 1))
        p.setBrush(QColor(BUBBLE_BG))
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor(BUBBLE_FG))
        p.setFont(self._font)
        p.drawText(QRectF(self.PAD_X, self.PAD_Y,
                          self.width() - self.PAD_X * 2,
                          self.height() - self.PAD_Y * 2),
                   Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter, self._text)
        p.end()

    def show_at(self, text, cx, cy):
        """cx/cy 是气泡底部中点（角色头顶，物理像素 —— 和 Pet 的坐标系一致）。"""
        self.set_text(text)
        self.follow(cx, cy)
        self.show()
        self.raise_()

    def follow(self, cx, cy):
        if not self.isVisible():
            return
        self.move(int(round(cx / self.dpr - self.width() / 2.0)),
                  int(round(cy / self.dpr - self.height())))

    def apply_top(self, on):
        if sys.platform == 'win32':
            from . import win32
            win32.set_topmost(int(self.winId()), on)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)

    def destroy(self):
        self.hide()
        self.deleteLater()
