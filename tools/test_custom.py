# -*- coding: utf-8 -*-
"""自定义动作的多帧存储 / 清单自测。

只碰 custom.py：用临时目录替换 DATA，不读也不写真实的 data/。
跑法：python tools/test_custom.py
"""
import os
import struct
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import custom      # noqa: E402

custom.DATA = tempfile.mkdtemp(prefix='petclub-custom-')

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + str(detail)) if (detail and not ok) else ''),
          flush=True)


def png(w, h):
    """最小合法 PNG（RGBA，全透明），只给校验和存储用。"""
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    raw = b''.join(b'\x00' + b'\x00\x00\x00\x00' * w for _ in range(h))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw))
            + chunk(b'IEND', b''))


def main():
    one = png(40, 60)
    two = png(40, 60)
    three = png(40, 60)
    odd = png(40, 64)

    print('--- 新建多帧动作 ---')
    spec, err = custom.add_action('p1', [one, two, three], '跳舞', 2.0, fps=12)
    check('多帧动作建得起来', bool(spec) and not err, err)
    if not spec:
        return 1
    key = spec.get('key')
    check('spec 记了帧数和自定义帧率',
          spec.get('frames') == 3 and spec.get('fps') == 12, spec)
    files = custom.frame_files('p1', key)
    check('三帧都落盘了', len(files) == 3, files)
    check('第一帧仍是 <pose>.png', files[0].endswith(key + '.png'), files)
    check('后续帧是 .1/.2 后缀',
          files[1].endswith(key + '.1.png')
          and files[2].endswith(key + '.2.png'), files)

    print('--- 清单 ---')
    m = custom.manifest()
    check('poses 里有这个动作', key in (m['poses'].get('p1') or {}), m)
    check('后续帧没有冒充姿势',
          key + '.1' not in (m['poses'].get('p1') or {}), m)
    anim = (m.get('anims') or {}).get('p1', {}).get(key) or {}
    check('anims 里记了帧数和帧率',
          anim.get('frames') == 3 and anim.get('fps') == 12, anim)

    print('--- 移动轨迹 ---')
    mv_spec, err = custom.add_action(
        'p1', [one, two], '御剑', 0.6, fps=10,
        move={'kind': 'line', 'dir': 'rl', 'height': 0.5, 'distance': 0.9})
    check('带轨迹的动作建得起来', bool(mv_spec) and not err, err)
    mv_key = (mv_spec or {}).get('key')
    mv = (mv_spec or {}).get('move') or {}
    check('轨迹写进 spec',
          mv.get('kind') == 'line' and mv.get('dir') == 'rl', mv)
    check('带轨迹时最小时长', (mv_spec or {}).get('dur') >= custom.MOVE_MIN_DUR,
          (mv_spec or {}).get('dur'))
    m = custom.manifest()
    check('清单把 move 原样带给客户端',
          ((m.get('actions') or {}).get(mv_key) or {}).get('move', {}).get('kind')
          == 'line', (m.get('actions') or {}).get(mv_key))
    # 不认识的模板 = 静止，不该让上传失败
    plain, err = custom.add_action('p1', [one, two], '奇怪轨迹', 2.0,
                                   move={'kind': 'teleport'})
    check('不认识的轨迹按静止处理',
          bool(plain) and not err and 'move' not in plain, plain or err)
    # 比例越界会被夹到范围里
    cl = custom.clamp_move({'kind': 'arc', 'height': 9, 'distance': 0})
    check('轨迹比例会被夹紧',
          cl and cl['height'] <= 0.8 and cl['distance'] >= 0.2, cl)
    # set_move 才动配置：不带就保持，传 None 才清掉
    out, err = custom.replace_pose('p1', mv_key, [one, two], fps=12)
    check('换图不带 move 时保持轨迹',
          bool(out) and not err
          and ((custom.actions().get(mv_key) or {}).get('move') or {})
          .get('kind') == 'line', custom.actions().get(mv_key))
    out, err = custom.replace_pose('p1', mv_key, [one, two], fps=12,
                                   move=None, set_move=True)
    check('显式传 None 能改回静止',
          bool(out) and not err
          and 'move' not in (custom.actions().get(mv_key) or {}),
          custom.actions().get(mv_key))

    print('--- 轨迹数学 ---')
    from petclub import motion
    for kind in motion.KINDS:
        cfg = {'kind': kind, 'dir': 'lr', 'height': 0.4, 'distance': 0.8}
        start = motion.offset(cfg, 0, 1000, 800, 400, 400, 500)
        end = motion.offset(cfg, 1, 1000, 800, 400, 400, 500)
        mid = motion.offset(cfg, 0.5, 1000, 800, 400, 400, 500)
        check('%s：起点和终点都回原位' % kind,
              abs(start[0]) < 1e-6 and abs(start[1]) < 1e-6
              and abs(end[0]) < 1e-6 and abs(end[1]) < 1e-6,
              (start, end))
        if kind == 'hover':
            check('hover 只上下浮', abs(mid[0]) < 1e-6 and mid[1] < 0, mid)
        elif kind == 'orbit':
            check('orbit 中段在最高处', abs(mid[0]) < 1e-6 and mid[1] < 0, mid)
        else:
            check('%s 中段横向飞出去' % kind, mid[0] > 0 and mid[1] < 0, mid)
    center = motion.offset({'kind': 'line', 'dir': 'lr', 'height': 0.4,
                            'distance': 0.8}, 0.5, 1000, 800, 500, 500, 500)
    left = motion.offset({'kind': 'line', 'dir': 'lr', 'height': 0.4,
                          'distance': 0.8}, 0.5, 1000, 800, 100, 900, 500)
    right = motion.offset({'kind': 'line', 'dir': 'lr', 'height': 0.4,
                           'distance': 0.8}, 0.5, 1000, 800, 900, 100, 500)
    check('贴左和贴右飞得一样远',
          abs(abs(left[0]) - abs(right[0])) < 1e-6
          and abs(abs(center[0]) - abs(right[0])) < 1e-6,
          (center, left, right))
    check('贴右边缘时自动掉头向左', right[0] < 0, right)
    orb = motion.offset({'kind': 'orbit', 'dir': 'lr', 'height': 0.4,
                         'distance': 0.8}, 0.75, 1000, 800, 900, 100, 500)
    check('贴边环绕也不会飞出屏幕', abs(orb[0]) <= 900 + 1e-6, orb)
    low = motion.offset({'kind': 'line', 'dir': 'lr', 'height': 0.4,
                         'distance': 0.8}, 0.5, 1000, 800, 500, 500, 500, 0)
    top = motion.offset({'kind': 'line', 'dir': 'lr', 'height': 0.4,
                         'distance': 0.8}, 0.5, 1000, 800, 500, 500, 0, 500)
    check('贴底往上、贴顶往下，高度一样',
          low[1] < 0 and top[1] > 0
          and abs(abs(low[1]) - abs(top[1])) < 1e-6, (low, top))
    hover_top = motion.offset({'kind': 'hover', 'height': 0.4},
                              0.5, 1000, 800, 500, 500, 0, 500)
    check('贴顶时悬停改成向下浮', hover_top[1] > 0, hover_top)
    orb_top = motion.offset({'kind': 'orbit', 'dir': 'lr', 'height': 0.4,
                             'distance': 0.8}, 0.5, 1000, 800, 500, 500, 0, 500)
    check('贴顶时环绕改成向下绕', orb_top[1] > 0, orb_top)

    print('--- 编辑时长 ---')
    out, err = custom.replace_pose('p1', mv_key, [one, two],
                                   dur=4.0, set_dur=True)
    check('编辑已有动作能改时长',
          bool(out) and not err
          and (custom.actions().get(mv_key) or {}).get('dur') == 4.0,
          custom.actions().get(mv_key))
    out, err = custom.replace_pose('p1', mv_key, [one, two],
                                   dur=0.2, set_dur=True)
    check('时长下限会被夹紧',
          (custom.actions().get(mv_key) or {}).get('dur') == 0.6,
          custom.actions().get(mv_key))
    custom.replace_pose('p1', mv_key, [one, two],
                        move={'kind': 'hover'}, set_move=True)
    custom.replace_pose('p1', mv_key, [one, two], dur=0.6, set_dur=True)
    check('带轨迹时时长不低于 1.2 秒',
          (custom.actions().get(mv_key) or {}).get('dur') >= custom.MOVE_MIN_DUR,
          custom.actions().get(mv_key))
    out, err = custom.replace_pose('p1', 'wave', [one], dur=5.0, set_dur=True)
    check('内置姿势不会被写进注册表', bool(out) and not err
          and 'wave' not in custom.actions(), custom.actions().get('wave'))

    print('--- 校验 ---')
    bad, err = custom.add_action('p1', [one, odd], '大小不一', 2.0)
    check('帧尺寸不一致会被拒', bad is None and '一致' in err, err)
    many = [one] * (custom.MAX_FRAMES + 1)
    bad, err = custom.add_action('p1', many, '太多帧', 2.0)
    check('帧数超限会被拒', bad is None and '最多' in err, err)

    print('--- 换图 ---')
    out, err = custom.replace_pose('p1', 'wave', [one, two])
    check('内置姿势不能多帧', out is None and '一张' in err, err)
    out, err = custom.replace_pose('p1', key, [one, two], fps=10)
    check('换图时能改帧率', bool(out) and not err
          and custom.manifest()['anims']['p1'][key]['fps'] == 10, err)
    out, err = custom.replace_pose('p1', key, [one])
    check('自定义动作可以换回单帧', bool(out) and not err, err)
    check('多余的帧被删掉', len(custom.frame_files('p1', key)) == 1)
    m = custom.manifest()
    check('单帧后 anims 里不再出现',
          key not in ((m.get('anims') or {}).get('p1') or {}), m.get('anims'))

    print('--- 删除 ---')
    check('删动作成功', custom.remove_action(key))
    check('所有帧都清掉了', not custom.frame_files('p1', key))

    print('--- 桌面端帧加载 ---')
    from petclub import sprites
    over = tempfile.mkdtemp(prefix='petclub-overlay-')
    for name, data in (('c9.png', one), ('c9.1.png', two)):
        with open(os.path.join(over, name), 'wb') as f:
            f.write(data)
    sp = sprites.Sprites(os.path.join(ROOT, 'assets', 'roles', 'p1'), over)
    check('识别出多帧顺序',
          sp.frame_keys.get('c9') == ['c9', 'c9.1'], sp.frame_keys.get('c9'))
    check('后续帧有独立贴图', 'c9.1' in sp.poses)
    check('多帧共用一张并集遮罩',
          sp.mask_base.get('c9.1') == 'c9'
          and sp.mask_images.get('c9.1').size == (40, 60))
    check('没有把 .1 当成新姿势',
          'c9.1' not in sp.anims or sp.anims['c9.1'].get('frames') == 1)

    print('--- 播放节拍 ---')
    from petclub import actors

    class FakeWin(object):
        win_w = 100.0
        win_h = 200.0
        body_w = 80.0

        def __init__(self):
            self.shown = []

        def show(self, pose, x, y):
            self.shown.append(pose)

    class FakeSp(object):
        w = 100
        h = 200
        body_w = 80
        poses = {'idle': None, 'fx': None, 'fx.1': None, 'fx.2': None}
        frame_keys = {'fx': ['fx', 'fx.1', 'fx.2']}
        mask_base = {'fx': 'fx', 'fx.1': 'fx', 'fx.2': 'fx'}
        mask_images = {}
        dx = {}

    def run(fps, step, seconds=3.0):
        """按 step 的节拍跑一段动作，返回换帧次数 / 实际耗时。"""
        sp = FakeSp()
        sp.anims = {'fx': {'frames': 3, 'fps': fps}}
        win = FakeWin()
        pet = actors.Pet(sp, win, 500.0, 800.0, drop=False)
        pet.act('fx', seconds)
        last = None
        changes = 0
        t = 0.0
        while t < seconds:
            pet.update(step)
            t += step
            pose = win.shown[-1]
            if last is not None and pose != last:
                changes += 1
            last = pose
        return changes / t, t

    # 主循环现在跟随屏幕刷新率（60Hz ≈ 0.017s）；这里分别按 60Hz / 30Hz
    # 采样，确认「配多少帧率就播多少帧」，不会被节拍带偏。
    for fps in (2, 8, 14, 24):
        for step in (0.017, 0.033):
            rate, span = run(fps, step)
            check('fps=%d 节拍 %.0fms 实测 %.1f' % (fps, step * 1000, rate),
                  abs(rate - fps) <= 0.8, rate)
    low, _ = run(6, 0.017)
    high, _ = run(20, 0.017)
    check('改帧率后播得更快', high > low * 2.5, (low, high))

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for name in FAIL:
        print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
