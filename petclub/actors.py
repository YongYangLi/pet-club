# -*- coding: utf-8 -*-
"""屏幕上的角色形象。

这一版桌面上常驻「服务器上所有被认领的身份」，所以只需要一种角色：
会从天而降出场、站着做动作、能被抓起来再扔掉。谁认领的、是不是自己，
由 app 记录，角色本身只关心自己的姿势和位置。

三个物理阶段（`phase`）：
  idle  站在地面上，只跑动作计时
  fall  受重力下落 —— 出场是从屏幕上方掉下来，松手是从手里掉下来，同一套
  hold  被鼠标抓着：完全跟手，不受重力；松手回到 fall
"""


class Pet:
    """一只站在桌面上的角色。

    (x, y) 是脚底中心。出场时从屏幕上方自由落体 ——
    新人认领身份的那一刻，他的角色会「掉」到所有人的桌面上。
    """

    GRAVITY = 2400.0        # 像素/秒²，从屏幕顶落到地面大约 1 秒，够快但不突兀
    HOLD_POSE = 'drag'      # 被抓着时用素材里的 drag.png（提溜着的样子）

    def __init__(self, sp, win, x, ground_y, drop=True):
        # 窗口由调用方造好再传进来：角色只关心「怎么动」，不关心窗口是谁家的。
        self.sp = sp
        self.win = win
        self.x = float(x)
        self.ground = float(ground_y)
        self.vy = 0.0
        self.phase = 'fall' if drop else 'idle'
        self.pose = 'fall' if drop else 'idle'
        self.timer = 0.0
        self.landed = not drop
        self.alive = True
        self.y = (-sp.h - 40.0) if drop else float(ground_y)   # 从屏幕上方看不见的地方开始

    def act(self, pose, dur):
        """做一个指定动作，dur 秒后自动回 idle。下落和被抓着时不接动作。"""
        if self.phase in ('fall', 'hold'):
            return
        self.pose = pose
        self.timer = dur

    # ---- 拖动（纯本地效果，一个字节都不发给别人）----

    def grab(self, x=None, y=None):
        """被抓住：从这一刻起不再受重力，位置交给鼠标。

        出场下落到一半也能抓 —— 抓起来就停在手上，松手继续掉。
        """
        if x is not None:
            self.x = float(x)
        if y is not None:
            self.y = float(y)
        self.vy = 0.0
        self.phase = 'hold'
        self.pose = self.HOLD_POSE
        self.timer = 0.0

    def move_to(self, x, y):
        """跟手。只改位置，不动姿势 —— 每帧重贴一张图太浪费。"""
        if self.phase != 'hold':
            return
        self.x = float(x)
        self.y = float(y)

    def release(self, vy=0.0):
        """松手：从当前位置自由落体，落地回到 idle（= 拉起来再扔下去）。"""
        if self.phase != 'hold':
            return
        self.phase = 'fall'
        self.pose = 'fall'
        self.vy = max(0.0, float(vy))

    @property
    def held(self):
        return self.phase == 'hold'

    def update(self, dt):
        if self.phase == 'fall':
            self.vy += self.GRAVITY * dt
            self.y += self.vy * dt
            if self.y >= self.ground:
                self.y = self.ground
                self.vy = 0.0
                self.phase = 'idle'
                self.pose = 'idle'
                self.landed = True
        elif self.phase == 'hold':
            pass                    # 位置由拖动直接写入，这里不加速度
        elif self.timer > 0:
            self.timer -= dt
            if self.timer <= 0:
                self.pose = 'idle'
        self.win.show(self.pose, int(self.x), int(self.y))

    def bubble_anchor(self):
        """气泡该挂在哪个点（角色头顶上方）。"""
        return self.x, self.y - self.sp.h - 6

    def destroy(self):
        self.alive = False
        self.win.destroy()
