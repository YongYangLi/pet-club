# -*- coding: utf-8 -*-
"""屏幕上的角色形象。

这一版桌面上常驻「服务器上所有被认领的身份」，所以只需要一种角色：
会从天而降出场、站着做动作、能被抓起来再扔掉。谁认领的、是不是自己，
由 app 记录，角色本身只关心自己的姿势和位置。

五个物理阶段（`phase`）：
  idle   站在地面上，只跑动作计时
  fall   受重力下落 —— 出场是从屏幕上方掉下来，松手是从手里掉下来，同一套
  hold   被鼠标抓着：完全跟手，不受重力；松手回到 fall
  rise   起飞：一路升到屏幕最上方，到顶转成 fall（就是「拎起来再松手」）
  crawl  集合：贴地朝目标 x 爬，crawl_1 / crawl_2 两帧循环，到位回 idle

位置始终是**本机**的：集合要爬到哪儿由 App 按这块屏幕上的站位算出来，
只把目标 x 交给 Pet，网络上一个字节都不用多传。
"""

def gather_targets(anchor_x, xs, widths, anchor_w, gap, lo, hi):
    """集合落点：给每只角色算一个 x，返回顺序和 xs 一致。

    `gap` 是**两个人身体之间**的空隙（不是中心距）：按各自的宽度往外排，
    所以高矮胖瘦不同的人，看上去的间隔也一样大。发起人同样占一格，谁都不会
    叠到谁身上。早先的算法按「窗宽 × 0.9」算中心距、又从 ±0.5 格起排，
    结果贴着他的两个叠在一起、再往外却是一整格 —— 看上去就是一簇 + 一大片空。

    贴屏幕边缘时只做两件事，不做「逐个夹回屏内」（夹过的会挤成一摞）：
      · 这一侧排不下就把队尾的人换到另一侧，队形不交叉；
      · 两侧都满了（屏幕比队伍窄）就**等比收窄空隙**，间距仍然人人相等。
    """
    n = len(xs)
    if not n:
        return []
    gap = max(0.0, float(gap))

    near = sorted(range(n), key=lambda i: abs(xs[i] - anchor_x))
    left = [i for i in near if xs[i] < anchor_x]      # 近的在前，先站里圈
    right = [i for i in near if xs[i] >= anchor_x]
    room_l = max(0.0, anchor_x - lo)
    room_r = max(0.0, hi - anchor_x)

    def offsets(ids, g):
        """这一队人各自离发起人中心多远（按站队顺序，从里往外）。"""
        cur = float(anchor_w) / 2.0
        out = []
        for i in ids:
            cur += g + widths[i] / 2.0
            out.append(cur)
            cur += widths[i] / 2.0
        return out

    def fits(g):
        return ((not left or offsets(left, g)[-1] <= room_l + 1e-9)
                and (not right or offsets(right, g)[-1] <= room_r + 1e-9))

    # 哪边放不下就把队尾那位换到另一边 —— 从最远的开始换，队形不交叉
    for _ in range(n + 1):
        if left and offsets(left, gap)[-1] > room_l + 1e-9:
            right.append(left.pop())
        elif right and offsets(right, gap)[-1] > room_r + 1e-9:
            left.append(right.pop())
        else:
            break

    g = gap
    if not fits(g):
        if not fits(0.0):
            # 人挨着人也站不下：那就先按可用宽度把人数分匀，别一边挤爆、
            # 另一边空着 —— 分完再收窄，间距照样人人相等。
            total = room_l + room_r
            want = int(round(n * room_l / total)) if total > 0 else 0
            while len(left) > want:
                right.append(left.pop())
            while len(left) < want and right:
                left.append(right.pop())
        # 二分找「还能塞下所有人」的最大空隙
        low, high = 0.0, gap
        for _ in range(24):
            mid = (low + high) / 2.0
            if fits(mid):
                low = mid
            else:
                high = mid
        g = low

    targets = [0.0] * n
    for i, off in zip(left, offsets(left, g)):
        targets[i] = anchor_x - off
    for i, off in zip(right, offsets(right, g)):
        targets[i] = anchor_x + off
    return targets


class Pet:
    """一只站在桌面上的角色。

    (x, y) 是脚底中心。出场时从屏幕上方自由落体 ——
    新人认领身份的那一刻，他的角色会「掉」到所有人的桌面上。
    """

    GRAVITY = 2400.0        # 像素/秒²，从屏幕顶落到地面大约 1 秒，够快但不突兀
    RISE_SPEED = 2400.0     # 起飞上升速度，和重力同量级：上去半秒、下来半秒
    HOLD_POSE = 'drag'      # 被抓着时用素材里的 drag.png（提溜着的样子）
    CRAWL_FRAMES = ('crawl_1', 'crawl_2')
    CRAWL_FRAME_SEC = 0.16      # 一帧 0.16 秒：手脚交替约每秒 3 次
    CRAWL_BODY_PER_SEC = 1.5    # 每秒爬 1.5 个身位；按窗口宽算，缩放档位下观感一致

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
        self.y = (-win.win_h - 40.0) if drop else float(ground_y)   # 从屏幕上方看不见的地方开始
        self.top = -win.win_h - 40.0    # 起飞升到的最高处（整个身子刚好出屏）
        self._crawl_to = None           # 集合落点；在空中时先记着，落地再出发
        self._crawl_to_y = None         # 逗猫棒是二维的：光标在哪儿就爬到哪儿
        self._land_y = None             # 起飞后落回哪一层（空中松手 / 摆放过的位置）
        self._crawl_t = 0.0             # 爬行循环帧的计时

    # ---- 集合：爬过去 ----

    def start_crawl(self, target_x, target_y=None):
        """朝 (target_x, target_y) 爬过去，到位自动站回 idle。

        在空中 / 被抓着时先记下落点，落地那一刻自己出发 —— 刚出场或
        正被拎起来的那只也不会漏掉这一声集合。`target_y` 为 None 时保持
        当前高度（旧的「只横向爬」调用方不用改）。
        """
        self._crawl_to = float(target_x)
        self._crawl_to_y = None if target_y is None else float(target_y)
        if self.phase not in ('fall', 'hold', 'rise'):
            self._begin_crawl()

    def _begin_crawl(self):
        if self._crawl_to is None:
            return
        if self._crawl_to_y is None:
            self._crawl_to_y = self.y
        if (abs(self._crawl_to - self.x) < 1.0
                and abs(self._crawl_to_y - self.y) < 1.0):
            self._crawl_to = None       # 本来就在旁边，不用爬
            self._crawl_to_y = None
            return
        self.phase = 'crawl'
        self.pose = self.CRAWL_FRAMES[0]
        self.timer = 0.0
        self._crawl_t = 0.0

    def _stop_crawl(self):
        """后一个指令 / 拖动 / 起飞一律顶掉爬行：没有冷却，也不排队。"""
        self._crawl_to = None
        self._crawl_to_y = None
        if self.phase == 'crawl':
            self.phase = 'idle'

    def chase(self, target_x, target_y=None):
        """逗猫棒：朝光标爬过去（横竖都跟）。

        和集合共用同一套爬行物理，区别只在于目标可以每帧改 —— 所以正在爬
        的时候只更新落点，不重开一次爬行（重开会把两帧动画倒回第一帧）。
        """
        self._crawl_to = float(target_x)
        self._crawl_to_y = self.y if target_y is None else float(target_y)
        if self.phase in ('fall', 'hold', 'rise'):
            return
        if self.phase != 'crawl':
            self._begin_crawl()

    def stop_chase(self):
        """逗猫棒结束：停在原地，别继续朝最后那个落点爬。"""
        self._crawl_to = None
        self._crawl_to_y = None
        if self.phase == 'crawl':
            self.phase = 'idle'
            self.pose = 'idle'

    def act(self, pose, dur):
        """做一个指定动作，dur 秒后自动回 idle。下落、被抓着、起飞时都不接动作。

        新动作一律顶掉正在进行的爬行 —— 连点不用排队，也不会两个姿势交错。
        """
        self._stop_crawl()
        if self.phase in ('fall', 'hold', 'rise'):
            return
        self.pose = pose
        self.timer = dur

    # ---- 拖动（纯本地效果，一个字节都不发给别人）----

    def grab(self, x=None, y=None):
        """被抓住：从这一刻起不再受重力，位置交给鼠标。

        出场下落到一半也能抓 —— 抓起来就停在手上，松手继续掉。
        手一碰到人，正在爬的那段就作废：鼠标比指令优先。
        """
        self._stop_crawl()
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
        """松手：人就停在松手的位置，不再掉回地面。

        桌宠不是「只能站在屏幕底边」的挂件 —— 用户把它拖到哪儿，它就该待在
        哪儿。所以松手直接回到 idle，位置原样保留；只有出场（drop=True）和
        起飞落地走重力。
        """
        if self.phase != 'hold':
            return
        self.phase = 'idle'
        self.pose = 'idle'
        self.vy = 0.0
        self.landed = True
        self._land_y = None

    def take_off(self):
        """起飞：从当前位置升到屏幕最上方，到顶后掉回起飞前的高度。

        上层只改 y，下落仍由重力接管；落地高度记在 `_land_y`，所以摆在
        半空的人飞一圈后还回到原来那一层，不会被顺手扔回屏幕底边。
        """
        self._stop_crawl()
        self._land_y = self.y
        if self.y <= self.top + 1:
            self.phase = 'fall'         # 已经在顶上了，没什么可升的，直接掉
            self.pose = 'fall'
            self.vy = 0.0
            return
        self.phase = 'rise'
        self.pose = 'fall'              # 空中姿势
        self.vy = 0.0
        self.timer = 0.0
        self.landed = False

    @property
    def held(self):
        return self.phase == 'hold'

    @property
    def airborne(self):
        return self.phase in ('fall', 'rise')

    @property
    def crawling(self):
        return self.phase == 'crawl'

    @property
    def body_w(self):
        """站着时人实际占多宽（窗口包含透明留白，比人宽）。集合排队按它算。"""
        return self.win.body_w

    def update(self, dt):
        if self.phase == 'fall':
            self.vy += self.GRAVITY * dt
            self.y += self.vy * dt
            land_y = self.ground if self._land_y is None else self._land_y
            if self.y >= land_y:
                self.y = land_y
                self.vy = 0.0
                self.phase = 'idle'
                self.pose = 'idle'
                self.landed = True
                self._land_y = None
                if self._crawl_to is not None:
                    self._begin_crawl()    # 排队的那声集合，落地就出发
        elif self.phase == 'rise':
            self.y -= self.RISE_SPEED * dt
            if self.y <= self.top:
                # 到顶即松手：速度归零，剩下的交给重力（和 release 同一条路）
                self.y = self.top
                self.phase = 'fall'
                self.pose = 'fall'
                self.vy = 0.0
        elif self.phase == 'hold':
            pass                    # 位置由拖动直接写入，这里不加速度
        elif self.phase == 'crawl':
            remain = self._crawl_to - self.x
            remain_y = ((self._crawl_to_y if self._crawl_to_y is not None
                         else self.y) - self.y)
            step = self.win.win_w * self.CRAWL_BODY_PER_SEC * dt
            self.x += (remain if abs(remain) <= step
                       else (step if remain > 0 else -step))
            self.y += (remain_y if abs(remain_y) <= step
                       else (step if remain_y > 0 else -step))
            self._crawl_t += dt
            self.pose = self.CRAWL_FRAMES[
                int(self._crawl_t / self.CRAWL_FRAME_SEC) % 2]
            if abs(self._crawl_to - self.x) <= 0.5 and abs(remain_y) <= step:
                self.x = self._crawl_to
                self.y = (self._crawl_to_y if self._crawl_to_y is not None
                          else self.y)
                self._crawl_to = None
                self._crawl_to_y = None
                self.phase = 'idle'
                self.pose = 'idle'
        elif self.timer > 0:
            self.timer -= dt
            if self.timer <= 0:
                self.pose = 'idle'
        self.win.show(self.pose, int(self.x), int(self.y))

    def bubble_anchor(self):
        """气泡该挂在哪个点（角色头顶上方，屏幕物理像素）。

        用窗口的显示高度而不是素材高度：角色按 PET_SCALE 放大后，
        头顶比素材高度高一截，气泡得跟着抬上去。
        """
        return self.x, self.y - self.win.win_h - 6

    def destroy(self):
        self.alive = False
        self.win.destroy()
