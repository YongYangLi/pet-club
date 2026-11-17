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

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for name in FAIL:
        print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
