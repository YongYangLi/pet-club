# -*- coding: utf-8 -*-
"""角色素材加载与循环帧对齐。"""

import os

from PIL import Image

# crawl_1 / crawl_2 是「集合」爬行时交替的两帧
POSES = ('idle', 'wave', 'jump', 'sleep', 'fall', 'drag',
         'drink', 'heart', 'knock', 'crawl_1', 'crawl_2')

# 需要当作循环动画对齐的帧组
LOOP_GROUPS = (('crawl_1', 'crawl_2'),)


def _centroid_x(im):
    """alpha 加权的水平质心。

    用来对齐循环动画的两帧。⚠️ 只能对齐「质心」，不能改成「最大化两帧像素重合度」——
    两帧是不同的姿态，重合度最优解会滑向对齐四肢，结果比不对齐还晃。
    """
    a = im.split()[3]
    px = a.load()
    w, h = a.size
    tot = 0.0
    acc = 0.0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            v = px[x, y]
            if v:
                tot += v
                acc += v * x
    return (acc / tot) if tot else w / 2.0


class Sprites:
    """一个角色的全部姿势图 + 统一窗口尺寸 + 循环帧修正。

    窗口尺寸取所有姿势的最大包围盒，精灵在窗口内「底部居中」贴 ——
    于是不同姿势的脚底始终落在同一个 y 上，换姿势时人不会上下跳。
    """

    def __init__(self, role_dir):
        self.poses = {}
        for name in POSES:
            p = os.path.join(role_dir, name + '.png')
            if os.path.exists(p):
                self.poses[name] = Image.open(p).convert('RGBA')
        if 'idle' not in self.poses:
            raise FileNotFoundError('缺少 idle.png：%s' % role_dir)
        self.w = max(im.width for im in self.poses.values())
        self.h = max(im.height for im in self.poses.values())

        # 站着时的实际身体宽度。`w` 是所有姿势的最大包围盒 —— 横过来的
        # crawl / 张开手的 heart 都比站立宽一大截，拿它当「一个人占多宽」
        # 会把队伍排得稀稀拉拉（集合的间距按这个值算）。
        self.body_w = self.poses['idle'].width

        # 循环帧的水平锚点修正：crawl_1 与 crawl_2 的包围盒宽度不同，
        # 直接底部居中贴会让身体在换帧时横向跳一下（看起来像"闪"）。
        self.dx = {}
        for grp in LOOP_GROUPS:
            frames = [n for n in grp if n in self.poses]
            if len(frames) < 2:
                continue
            cs = [_centroid_x(self.poses[n]) for n in frames]
            avg = sum(cs) / len(cs)
            for n, c in zip(frames, cs):
                self.dx[n] = int(round(avg - c))

    def get(self, pose):
        return self.poses.get(pose) or self.poses['idle']


def load_all(assets_dir, roles):
    """加载指定角色。素材缺失的角色直接跳过，不让一个坏目录拖垮整个程序。"""
    out = {}
    for r in roles:
        try:
            out[r] = Sprites(os.path.join(assets_dir, 'roles', r))
        except (OSError, FileNotFoundError):
            pass
    return out
