# -*- coding: utf-8 -*-
"""桌面层：角色窗口与气泡。

几个绕不开的点：

【透明】两个声明：FramelessWindowHint（没有标题栏）+
  WA_TranslucentBackground（按 alpha 合成）。抗锯齿边缘、半透明、预乘 alpha
  全由 Qt 处理。

【点击穿透】Windows 的分层窗口按像素 alpha 做命中测试，alpha 为 0 的地方
  本来就穿透；只有 macOS 这类按矩形命中测试的平台需要额外裁一个
  QRegion mask，否则角色周围那一圈透明区域会挡住桌面图标。
  ⚠️ 别在 Windows 上装 mask：QRegion 是 1 bit 的，alpha < 128 的半透明
  像素会被整块裁掉，剑光/柔边这些细节会变成硬切边（看起来像丢了细节）。

【DPI】素材是物理像素，Qt 的窗口尺寸是逻辑像素。屏幕 dpr 负责把
  「物理坐标」翻成「Qt 逻辑坐标」；素材位图在建窗时按 scale 一次性放大到
  屏幕物理尺寸（LANCZOS），设备比设成 dpr —— 于是每帧只是把位图 1:1 搬到
  整数设备像素上，不再重采样（分数 dpr 下反复采样会让细节抖）。scale = 1
  是一比一，scale = 屏幕 dpr 就是旧 Tk 版那种「被系统再放大一次」的观感
  （见 config.PET_SCALE）。两者分开，拖动和落地用的业务坐标才不会跟着变。

【坐标】对外一律用**屏幕物理像素**，脚底中心对齐。
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBitmap, QColor, QFont, QFontMetrics, QImage,
                           QPainter, QPen, QPixmap, QRegion)
from PySide6.QtWidgets import QApplication, QWidget

from PIL import Image

from . import config, host

# 气泡配色，和认领窗/聊天面板同一套
BUBBLE_BG = '#ffffff'
BUBBLE_FG = '#111827'
BUBBLE_EDGE = '#e4e7ef'
BUBBLE_FONT = host.ui_font()


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
        # scale 是「观感倍数」：1 = 一比一，dpr = 旧 Tk 版的放大观感。
        # 位图设备比 = dpr / scale：素材像素因此被画成 scale 个屏幕物理像素。
        # 对外（App / Pet）仍一律用物理像素；只有 Qt 接口要换成逻辑像素。
        self.scale = max(0.01, config.pet_scale(self.dpr))
        self.pdpr = max(0.01, self.dpr / self.scale)
        self.win_w = float(sp.w) * self.scale      # 屏幕物理宽高
        self.win_h = float(sp.h) * self.scale
        # 站着时人实际占多宽（素材 idle 的宽度）：窗口比人宽 —— 它是所有姿势的
        # 最大包围盒。集合排队问的是「人有多宽」，所以另存一份。
        self.body_w = float(getattr(sp, 'body_w', sp.w)) * self.scale
        self._lw = self.win_w / self.dpr          # Qt 逻辑宽高
        self._lh = self.win_h / self.dpr
        self.setFixedSize(max(1, int(round(self._lw))),
                          max(1, int(round(self._lh))))

        self._pix = {}
        self._qim = {}
        for name, im in sp.poses.items():
            q = to_qimage(im)
            self._qim[name] = q                 # 素材像素：mask、命中点用
            self._pix[name] = self._device_pix(im)
        self._masks = {}
        self._hit_cache = {}
        self._pose = 'idle'
        self._mask_pose = 'idle'
        # 轨迹画布：不为 None 时窗口位置固定，人按这个物理原点做内部偏移绘制。
        # 见 begin_route()：顶层窗口每帧挪一次，Windows 会同时露出「新位置 +
        # 旧位图」和「新位置 + 新位图」，看起来就是整窗拖影。
        self._canvas = None
        # 见文件头【点击穿透】：Windows 靠 alpha 命中测试，装 mask 反而会
        # 把半透明像素裁掉；只有矩形命中的平台才需要 mask。
        self._masked = not host.IS_WIN
        self.role = None            # App 建好窗口后填：反查用（命中测试、菜单）
        self._apply_mask('idle')    # 需要 mask 的平台先装上，否则窗口是整块矩形
        self._x = 0.0
        self._y = 0.0
        super().show()                  # 注意不能写 self.show()：下面重载了同名方法

    # ---- 画 ----

    def _device_pix(self, im):
        """把素材一次性放大到屏幕物理尺寸，之后每帧按 1:1 贴。

        以前是把素材像素当逻辑像素、让 QPainter 用设备比现场放大：dpr 是
        1.25 这种分数时，每一帧都要重新做双线性采样；窗口一挪，采样相位还会
        在 .0/.25/.5/.75 之间来回跳，细节就跟着抖。放大挪到这里只做一次
        （LANCZOS），位图设备比设成 dpr，绘制时正好落在整数设备像素上，
        和网页端「光栅化一次、之后只平移」同一个思路。
        """
        w = max(1, int(round(im.width * self.scale)))
        h = max(1, int(round(im.height * self.scale)))
        if (w, h) != (im.width, im.height):
            im = im.resize((w, h), Image.LANCZOS)
        pm = QPixmap.fromImage(to_qimage(im))
        pm.setDevicePixelRatio(self.dpr)
        return pm

    def show(self, pose, x, y):
        """换姿势 + 挪位置。pose 不认识就退回 idle（和 sprites.get 一致）。

        注意这个方法**重载了 QWidget.show()**（`Pet.update` 每帧调
        `win.show(pose, x, y)`）。所以本类内部要显示窗口一律用
        `super().show()`，绝不能写 `self.show()`。
        """
        changed = pose != self._pose
        if changed:
            self._pose = pose
            # 多帧动作的每一帧共用一张「所有帧的 alpha 并集」遮罩：
            # 换帧时不用重装 mask，也不会把伸出去的胳膊裁掉。
            # 画布模式下 mask 是整条轨迹的并集，已经装好，换帧不再动它。
            if self._canvas is None:
                base = self.sp.mask_base.get(pose, pose)
                if base != self._mask_pose:
                    self._mask_pose = base
                    self._apply_mask(base)
        self.move_to(x, y)
        if not self.isVisible():
            super().show()
        if self._canvas is not None:
            # 画布里人每拍都在挪，必须每拍重画；窗口本身不动，没有拖影。
            self.update()
        elif changed:
            # 先挪窗口、再画：新位置和新那一帧在同一拍里提交，少一次
            # 「旧图停在新位置」的残影。
            if self.isVisible():
                self.repaint()
            else:
                self.update()

    # ---- 轨迹画布 ----

    def begin_route(self, points, mask_pose):
        """按整条轨迹撑开窗口，轨迹期间只重画、不再挪窗口。

        points 是这次动画会经过的脚底中心点（屏幕物理像素）。窗口按这些点
        的并集一次就位；需要 mask 的平台还要取这些点上人形的并集，开始/结束
        各装一次（代价是轨迹扫过的那一条都会吃鼠标）。Windows 靠 alpha 命中
        测试，这一步直接跳过。
        """
        points = list(points or ())
        if not points:
            return
        half = self.win_w / 2.0
        left = min(px for px, _ in points) - half
        top = min(py for _, py in points) - self.win_h
        right = max(px for px, _ in points) + half
        bottom = max(py for _, py in points)
        # 对齐到逻辑像素网格：Qt 的窗口坐标只收整数逻辑像素，先取整再反算
        # 物理原点，画布原点才和窗口实际原点重合。
        lx = int(round(left / self.dpr))
        ly = int(round(top / self.dpr))
        lw = max(1, int(round((right - left) / self.dpr)))
        lh = max(1, int(round((bottom - top) / self.dpr)))
        self._canvas = (lx * self.dpr, ly * self.dpr)
        # 换尺寸、换 mask 的中间态太容易被合成器看到，先停刷新，一次提交。
        self.setUpdatesEnabled(False)
        self.setFixedSize(lw, lh)
        self.move(lx, ly)

        base = self._region_for(mask_pose) if self._masked else None
        if base is not None:
            union = QRegion()
            seen = set()
            for px, py in points:
                # 采样点是屏幕物理像素，region 是窗口逻辑坐标，先换算
                dx = int(round((px - half - self._canvas[0]) / self.dpr))
                dy = int(round((py - self.win_h - self._canvas[1]) / self.dpr))
                if (dx, dy) in seen:
                    continue
                seen.add((dx, dy))
                part = QRegion(base)
                part.translate(dx, dy)
                union = union.united(part)
            self.setMask(union)
        self.setUpdatesEnabled(True)
        self.update()

    def end_route(self):
        """退出画布模式：窗口缩回紧包尺寸，装回当前姿势的 mask。"""
        if self._canvas is None:
            return
        self._canvas = None
        self.setUpdatesEnabled(False)
        self.setFixedSize(max(1, int(round(self._lw))),
                          max(1, int(round(self._lh))))
        lx = int(round(self._x / self.dpr - self._lw / 2.0))
        ly = int(round(self._y / self.dpr - self._lh))
        self.move(lx, ly)
        base = self.sp.mask_base.get(self._pose, self._pose)
        self._mask_pose = base
        self._apply_mask(base)
        self.setUpdatesEnabled(True)
        self.update()

    def move_to(self, x, y):
        """(x, y) 是脚底中心的屏幕坐标（物理像素）。"""
        self._x = float(x)
        self._y = float(y)
        if self._canvas is not None:
            return                      # 画布模式：位置只记在 _x/_y，窗口不动
        lx = int(round(x / self.dpr - self._lw / 2.0))
        ly = int(round(y / self.dpr - self._lh))
        if (lx, ly) != (self.x(), self.y()):
            self.move(lx, ly)

    def _region_for(self, pose):
        """紧包窗口坐标系里「只有人身上才吃鼠标」的区域（按姿势缓存）。

        不能用 QPixmap.mask()：那张 pixmap 设了 devicePixelRatio，
        mask() 在某些缩放比下直接返回空。老老实实从 alpha 生成 1bit 蒙版，
        再按绘制位置平移到窗口坐标系。
        """
        region = self._masks.get(pose)
        if region is None:
            src = self.sp.mask_images.get(pose)
            if src is not None:
                q = to_qimage(src)
            else:
                q = self._qim.get(pose) or next(iter(self._qim.values()))
            bm = QBitmap.fromImage(q.createAlphaMask())
            if bm.isNull():
                return None
            # 素材像素 → 逻辑像素（和 paintEvent 里的绘制尺寸同一个换算）
            w = max(1, int(round(q.width() / self.pdpr)))
            h = max(1, int(round(q.height() / self.pdpr)))
            bm = QBitmap(bm.scaled(w, h))   # scaled 返回 QPixmap，包回来
            region = QRegion(bm)
            dx = self.sp.dx.get(pose, 0) / self.pdpr
            region.translate(int(round((self._lw - w) / 2.0 + dx)),
                             int(round(self._lh - h)))
            self._masks[pose] = region
        return region

    def _apply_mask(self, pose):
        if not self._masked:
            return                      # Windows：alpha 命中测试，不裁形状
        region = self._region_for(pose)
        if region is not None:
            self.setMask(region)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pm = self._pix.get(self._pose) or next(iter(self._pix.values()))
        # 底部居中；循环帧的水平修正沿用 sprites 里的 dx（素材像素 → 物理像素）
        base = self.sp.mask_base.get(self._pose, self._pose)
        dx = self.sp.dx.get(self._pose, self.sp.dx.get(base, 0))
        off_x = off_y = 0.0
        if self._canvas is not None:
            # 画布模式：人的位置是「紧包窗口的左上角」相对画布原点差多少。
            off_x = self._x - self.win_w / 2.0 - self._canvas[0]
            off_y = self._y - self.win_h - self._canvas[1]
        # 落点先算成设备像素，再换回逻辑坐标交给 QPainter：目标尺寸和设备
        # 像素正好 1:1，Qt 只做位块搬运，不会再采样一次。
        x_dev = int(round((self.win_w - pm.width()) / 2.0
                          + dx * self.scale + off_x))
        y_dev = int(round(self.win_h - pm.height() + off_y))
        p.drawPixmap(QPointF(x_dev / self.dpr, y_dev / self.dpr), pm)
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
        img = self._qim.get(self._pose) or self._qim['idle']
        off = self._hit_cache.get(self._pose)
        if off is None:
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
        # 绘制时素材图在窗口内底部居中，循环帧另有 dx 修正；这里按同一套
        # 偏移把「素材像素」换算成「屏幕物理像素」（放大后一个像素顶 scale 个）。
        off_x = (self.sp.w - img.width()) / 2.0 + self.sp.dx.get(self._pose, 0)
        off_y = self.sp.h - img.height()
        left = self._x - self.win_w / 2.0
        top = self._y - self.win_h
        return (int(left + (off_x + off[0]) * self.scale),
                int(top + (off_y + off[1]) * self.scale))

    def reassert_top(self):
        """重新插回置顶带：置顶窗口是一个共享带，谁最后激活谁在上面。"""
        if not host.raise_top(self):
            self.raise_()

    def apply_top(self, on):
        """切「始终置顶」：只改 z 序属性，不重建窗口（保住 mask 和位置）。"""
        if not host.set_topmost(self, on):
            # 拿不到原生改层级的办法（没有 PyObjC、也不是 Windows）：
            # 退回 Qt 的 flag，代价是会重建窗口
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if on:
            self.reassert_top()

    def destroy(self):
        # 只 hide。不要 deleteLater() —— 调用方随后就丢掉 Python 引用，
        # shiboken 会立刻删 C++ 对象，Qt 的 DeferredDelete 再删一次就是
        # double free。引用一断，这里自然只删一次。
        self.hide()
        self.setParent(None)


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
        """cx/cy 是气泡底部中点（角色头顶，屏幕物理像素）。"""
        self.set_text(text)
        self.follow(cx, cy)      # 隐藏的窗口也能 move：先定位再显示
        self.show()
        self.raise_()

    def follow(self, cx, cy):
        # 这里**不能**因为不可见就 return。气泡是复用的，超时只 hide()，
        # 下次 show_at 时仍然不可见；一 return 就永远停在第一次的位置 ——
        # Qt 给没定位过的顶层窗口一个默认位置，看上去就是「屏幕中间」。
        self.move(int(round(cx / self.dpr - self.width() / 2.0)),
                  int(round(cy / self.dpr - self.height())))

    def apply_top(self, on):
        if not host.set_topmost(self, on):
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)

    def destroy(self):
        self.hide()
        self.setParent(None)
