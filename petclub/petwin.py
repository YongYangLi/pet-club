# -*- coding: utf-8 -*-
"""宠物的分层窗口与气泡窗口。"""

import tkinter as tk

from PIL import Image

from .surface import IS_WIN, LayeredSurface, raise_top

BUBBLE_FONT = ('Microsoft YaHei UI', 10, 'bold')
BUBBLE_KEY = '#ff00fe'      # 气泡窗口的色键，挑一个不会出现在 UI 里的洋红

# ---------------- 图层 ----------------
#
# 桌宠默认压在一切之上（本来就是挂在桌面上的东西）。但「始终置顶」这个属性
# 有时候恰恰是麻烦：要拖个文件进浏览器，角色正好站在那儿，鼠标怎么点都点在它
# 身上。所以托盘里留了一个开关，能在「始终置顶」和「会被别的窗口盖住」之间切。
#
# 状态放在模块里、而不是放在 App 里：窗口是在这里造的，造的时候得知道该给
# 什么值 —— 否则切完开关再冒出一个气泡，它又自己置顶回去了。
TOP_ON = {'on': True}


def set_layer(on):
    """切图层。True = 始终置顶，False = 允许被浏览器、资源管理器盖住。"""
    TOP_ON['on'] = bool(on)
    return TOP_ON['on']


def apply_top(top):
    """把一个窗口按当前图层设置摆好。"""
    try:
        top.attributes('-topmost', TOP_ON['on'])
    except Exception:
        # 窗口已经没了、或者平台不支持这个属性 —— 都不该拖垮调用方
        pass


class PetWindow:
    """一只宠物的分层窗口。

    窗口尺寸固定为该角色所有姿势的最大包围盒，精灵在窗口内「底部居中」贴 ——
    于是 (x, y) 永远是脚底中心的坐标，换姿势时人不会上下跳。
    """

    def __init__(self, root, sprites):
        self.sp = sprites
        self.win_w = sprites.w
        self.win_h = sprites.h

        top = tk.Toplevel(root)
        top.overrideredirect(True)
        apply_top(top)
        top.geometry('%dx%d+0+0' % (self.win_w, self.win_h))
        top.withdraw()
        self.top = top
        self.surface = LayeredSurface(top)
        # 先铺一层全透明，避免 layered 化之后闪出默认底色
        self.surface.set_image(
            Image.new('RGBA', (self.win_w, self.win_h), (0, 0, 0, 0)), 0, 0)
        top.deiconify()

        self._key = None
        self._pos = None
        self._hit_off = None    # 精灵不透明区域中心的窗口内坐标（给置顶巡检做命中测试）

    def show(self, pose, x, y):
        # 落点必须是 int：paste 与 UpdateLayeredWindow 都不收浮点
        x = int(round(x - self.win_w / 2.0))
        y = int(round(y - self.win_h))
        if self._key != pose:
            self._key = pose
            sprite = self.sp.get(pose)
            # 循环动画的水平锚点修正：两帧包围盒宽度不同，按质心对齐才不会横跳
            dx = self.sp.dx.get(pose, 0)
            im = Image.new('RGBA', (self.win_w, self.win_h), (0, 0, 0, 0))
            ox = (self.win_w - sprite.width) // 2 + dx
            im.paste(sprite, (int(ox), self.win_h - sprite.height))
            self.surface.set_image(im, x, y)
            self._pos = (x, y)
            # 记住一个「一定不透明」的点：置顶巡检靠它判断自己有没有被盖住。
            # 落在这个点上必然是角色的身体，不会穿透到桌面去。
            box = sprite.split()[3].getbbox()
            if box:
                self._hit_off = (int(ox + (box[0] + box[2]) / 2.0),
                                 int(self.win_h - sprite.height
                                     + (box[1] + box[3]) / 2.0))
            else:
                self._hit_off = (self.win_w // 2,
                                 self.win_h - sprite.height // 2)
        elif (x, y) != self._pos:
            self._pos = (x, y)
            self.surface.move(x, y)

    def hit_point(self):
        """角色身上一个确定的屏幕坐标；还没摆过位置时返回 None。"""
        if self._hit_off is None or self._pos is None:
            return None
        return (self._pos[0] + self._hit_off[0], self._pos[1] + self._hit_off[1])

    def reassert_top(self):
        """被别的程序压住之后，把自己重新插回置顶带最前面。"""
        return raise_top(self.surface.hwnd)

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class Bubble:
    """可复用的气泡窗口。

    气泡是规则矩形，用色键透明就够了 —— 它不会和宠物窗口互相打洞
    （色键的坑只出现在「多只宠物挤在一起、前面那只的矩形盖住后面身体」时）。
    """

    H = 34

    def __init__(self, root):
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        apply_top(top)
        top.configure(bg=BUBBLE_KEY)
        if IS_WIN:
            try:
                top.wm_attributes('-transparentcolor', BUBBLE_KEY)
            except tk.TclError:
                pass
        self.top = top
        self.canvas = tk.Canvas(top, bg=BUBBLE_KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.box = self.canvas.create_rectangle(0, 0, 0, 0, fill='#ffffff',
                                                outline='#c9d2e3')
        self.tx = self.canvas.create_text(0, 0, text='', fill='#1b2231',
                                          font=BUBBLE_FONT)
        self.visible = False
        self.text = ''
        self._size = None
        top.withdraw()

    def show(self, text, cx, cy):
        self.text = text
        tw = max(80, len(text) * 15 + 28)
        th = self.H
        if self._size != (tw, th):
            self._size = (tw, th)
            self.canvas.configure(width=tw, height=th)
            self.canvas.coords(self.box, 1, 1, tw - 1, th - 1)
            self.canvas.coords(self.tx, tw / 2, th / 2)
            self.top.geometry('%dx%d' % (tw, th))
        self.canvas.itemconfig(self.tx, text=text)
        self.top.geometry('+%d+%d' % (int(cx - tw / 2), int(cy - th / 2)))
        if not self.visible:
            self.top.deiconify()
            self.visible = True

    def follow(self, cx, cy):
        """只挪位置不重排文字 —— 拖动角色时让气泡跟着走。"""
        if not self.visible or self._size is None:
            return
        tw, th = self._size
        self.top.geometry('+%d+%d' % (int(cx - tw / 2), int(cy - th / 2)))

    def hide(self):
        if self.visible:
            self.top.withdraw()
            self.visible = False

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass
