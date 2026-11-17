# -*- coding: utf-8 -*-
"""角色素材加载与循环帧对齐。"""

import json
import os
import re

from PIL import Image, ImageChops

# crawl_1 / crawl_2 是「集合」爬行时交替的两帧
POSES = ('idle', 'wave', 'jump', 'sleep', 'fall', 'drag',
         'drink', 'heart', 'knock', 'crawl_1', 'crawl_2')

# 需要当作循环动画对齐的帧组
LOOP_GROUPS = (('crawl_1', 'crawl_2'),)

# 用户上传的多帧动作：<pose>.1.png / <pose>.2.png …，固定 8fps 循环。
# 和 petclub/custom.py 的 FPS 是同一个值，改一边就得改另一边。
FPS = 8
FRAME_RE = re.compile(r'^(?P<base>[a-z][a-z0-9_]{0,15})\.(?P<idx>\d+)$')


def _union_alpha(images):
    """把多帧的 alpha 叠成一张，给窗口当遮罩：动起来也不会被裁掉。"""
    union = images[0].getchannel('A')
    for im in images[1:]:
        union = ImageChops.lighter(union, im.getchannel('A'))
    out = Image.new('RGBA', images[0].size, (0, 0, 0, 0))
    out.putalpha(union)
    return out


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

    `overlay_dir` 是用户上传的覆盖层（见 sprite_sync）：同一文件名优先用
    覆盖版，覆盖层里多出来的名字（自定义动作）也算这个角色的姿势。
    出厂素材一份都不少 —— 删掉覆盖文件就自动回到原来的样子。
    """

    def __init__(self, role_dir, overlay_dir=None):
        self.poses = {}
        # 多帧动作：base pose → [帧 key]，帧 key 形如 c1、c1.1、c1.2…
        # Pet 播放时按这些 key 换图；窗口按 base 装一张合并后的 mask。
        self.frame_keys = {}
        self.anims = {}
        self.mask_images = {}
        self.mask_base = {}
        names = list(POSES)
        extras = {}
        fps_map = {}
        if overlay_dir:
            try:
                with open(os.path.join(overlay_dir, 'anims.json'),
                          'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    fps_map = data
            except (OSError, ValueError):
                fps_map = {}
            try:
                for name in sorted(os.listdir(overlay_dir)):
                    if not name.endswith('.png') or name.startswith('.'):
                        continue
                    stem = name[:-4]
                    m = FRAME_RE.match(stem)
                    if m:
                        extras.setdefault(m.group('base'), []).append(
                            (int(m.group('idx')), name))
                        continue
                    if stem not in names:
                        names.append(stem)
            except OSError:
                overlay_dir = None

        for name in names:
            paths = []
            p = None
            if overlay_dir:
                cand = os.path.join(overlay_dir, name + '.png')
                if os.path.exists(cand):
                    p = cand
            if p is None:
                p = os.path.join(role_dir, name + '.png')
            if p and os.path.exists(p):
                paths.append(p)
            # 后续帧只可能来自用户上传目录
            if overlay_dir:
                for _, fname in sorted(extras.get(name, ())):
                    paths.append(os.path.join(overlay_dir, fname))
            images = []
            for path in paths:
                try:
                    images.append(Image.open(path).convert('RGBA'))
                except OSError:
                    continue        # 半张坏图不该拖垮整个角色
            if not images:
                continue
            base = name
            self.poses[base] = images[0]
            self.mask_base[base] = base
            if len(images) > 1:
                # 帧画布必须一致：不一致时宁可按静态图放，也别让角色抖。
                size = images[0].size
                frames = [im for im in images if im.size == size]
                if len(frames) > 1:
                    keys = [base]
                    for i, im in enumerate(frames[1:], 1):
                        key = '%s.%d' % (base, i)
                        self.poses[key] = im
                        self.mask_base[key] = base
                        keys.append(key)
                    self.frame_keys[base] = keys
                    try:
                        fps = float(fps_map.get(base) or FPS)
                    except (TypeError, ValueError):
                        fps = FPS
                    fps = max(2.0, min(24.0, fps))
                    self.anims[base] = {'frames': len(keys), 'fps': fps}
                    union = _union_alpha(frames)
                    for key in keys:
                        self.mask_images[key] = union
            if base not in self.mask_images:
                self.mask_images[base] = self.poses[base]

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


def load_all(assets_dir, roles, overlay_dir=None):
    """加载指定角色。素材缺失的角色直接跳过，不让一个坏目录拖垮整个程序。"""
    out = {}
    for r in roles:
        try:
            over = os.path.join(overlay_dir, r) if overlay_dir else None
            out[r] = Sprites(os.path.join(assets_dir, 'roles', r), over)
        except (OSError, FileNotFoundError):
            pass
    return out
