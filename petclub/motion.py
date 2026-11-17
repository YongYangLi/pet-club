# -*- coding: utf-8 -*-
"""自定义动作的移动轨迹。

纯粹一道数学题：给模板（line / arc / orbit / hover）、参数、进度 t 和
角色周围还剩多少空间，返回相对起点的 (dx, dy)。

为什么不把坐标存在动作里：每个客户端的屏幕大小、角色站位都不一样。
只存比例，播放时按自己屏幕算，两端看起来才是同一件事。

网页端 `web/js/motion.js` 是这份实现的等价版本 —— 改公式必须两边一起改，
否则同一个动作在桌面和网页上飞得不一样。
"""

import math

KINDS = ('line', 'arc', 'orbit', 'hover')


def _smooth(u):
    """smoothstep：起步和收尾都慢一点，比线性自然。"""
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def offset(move, t, width, height, room_left, room_right, rise, drop=0.0):
    """返回这一帧相对起点的偏移。t=0 和 t=1 都必然回到 (0, 0)。

    width / height 是屏幕尺寸，用来把比例换成像素；
    room_left / room_right / rise / drop 是起点上下左右还剩多少空间。
    放不下时自动缩短轨迹、必要时掉头（贴右边往左飞、贴顶部往下飞），
    角色不会飞出屏幕。
    """
    if not isinstance(move, dict):
        return 0.0, 0.0
    kind = move.get('kind')
    if kind not in KINDS:
        return 0.0, 0.0
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = 0.0
    t = min(1.0, max(0.0, t))

    try:
        height_pct = float(move.get('height'))
    except (TypeError, ValueError):
        height_pct = 0.35
    try:
        dist_pct = float(move.get('distance'))
    except (TypeError, ValueError):
        dist_pct = 0.8

    rise = max(0.0, float(rise or 0.0))
    fall = max(0.0, float(drop or 0.0)) if drop is not None else 0.0
    # 垂直方向默认往上飞；顶部放不下时就掉头向下，幅度仍然按屏幕高度算，
    # 所以贴顶和贴底看到的高度变化一样大。
    wanted = max(0.0, height_pct) * max(0.0, float(height))
    if rise >= wanted:
        up, amp = True, wanted
    elif fall >= wanted:
        up, amp = False, wanted
    elif rise >= fall:
        up, amp = True, rise
    else:
        up, amp = False, fall
    vdir = -1.0 if up else 1.0

    if kind == 'hover':
        # 原地上下浮一次，t=0/1 都在原位。
        return 0.0, vdir * amp * 0.5 * (1.0 - math.cos(2.0 * math.pi * t))

    left_room = max(0.0, float(room_left or 0.0)) if room_left is not None else 0.0
    right_room = max(0.0, float(room_right or 0.0)) if room_right is not None else 0.0
    # 幅度固定成「跨度比例的一半屏宽」：站在正中间还是贴着边，飞出去
    # 的距离都一样。以前是「到边的剩余空间」，贴右边缘时右边没地方，
    # 于是几乎不动 —— 这就是用户看到的「越靠边效果越弱」。
    amp_x = max(0.0, dist_pct) * max(0.0, float(width)) / 2.0
    prefer_right = move.get('dir') != 'rl'
    room, sign = (right_room, 1.0) if prefer_right else (left_room, -1.0)
    other = left_room if prefer_right else right_room
    if room < amp_x and other > room:
        # 首选方向放不下这一整段，就掉头往空间大的那边飞。
        room, sign = other, -sign
    reach = min(amp_x, room)

    if kind == 'orbit':
        # 绕起点画一个竖椭圆。贴边时整圈会有一半出屏：把左右半径分开，
        # 空间小的一侧压扁，路径仍然从原点出发、回到原点。
        rx_r = min(amp_x, right_room)
        rx_l = min(amp_x, left_room)
        ry = amp / 2.0
        ang = 2.0 * math.pi * t * sign
        s = math.sin(ang)
        return (rx_r * s if s > 0 else rx_l * s,
                vdir * (ry - ry * math.cos(ang)))

    # line / arc 都是「飞出去再飞回来」，区别只在高度曲线：
    # line 快速升空后保持高度，arc 是一整条平滑的抛物线。
    out = math.sin(math.pi * t)
    if kind == 'line':
        lift = _smooth(min(t / 0.18, (1.0 - t) / 0.18))
    else:
        lift = out
    return sign * reach * out, vdir * amp * lift
