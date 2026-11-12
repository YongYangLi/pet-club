# -*- coding: utf-8 -*-
"""媒体编解码测试：图片、文件、表情。

    py tools/test_media.py

这里测的是「发出去的那一坨字节」和「屏幕上那张图」，不是聊天面板。
面板的行为在 probe_live.py 里，那边要开真窗口。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image                                    # noqa: E402

from petclub import media                                # noqa: E402
from petclub.config import ROLES, assets_dir             # noqa: E402

TMP = os.path.join(HERE, '..', '.tmp', 'media-test')
_fail = []
_n = [0]


def check(name, ok, detail=''):
    _n[0] += 1
    print('[%s] %s%s' % ('ok  ' if ok else 'FAIL', name,
                         ('  ' + str(detail)) if detail else ''))
    if not ok:
        _fail.append(name)


def sheet(path, size, color=(230, 120, 90)):
    im = Image.new('RGB', size, color)
    im.save(path)
    return path


def main():
    os.makedirs(TMP, exist_ok=True)

    print('--- 图片：缩放与编码 ---')
    big = sheet(os.path.join(TMP, 'big.png'), (1600, 1200))
    b64, w, h = media.encode_image(big)
    check('编码结果能解回一张图', bool(b64) and len(b64) > 100, '%d 字符' % len(b64))
    im = media.decode(b64)
    check('解出来的图不长于 480', im is not None and max(im.size) <= media.MAX_SIDE,
          im.size if im else None)
    check('宽高比没变形', im is not None and abs(w / float(h) - 1600 / 1200.0) < 0.05,
          '%.2f vs 1.33' % (w / float(h)))
    check('base64 只有合法字符',
          all(c.isalnum() or c in '+/=' for c in b64))

    small = sheet(os.path.join(TMP, 'small.png'), (120, 90))
    _b2, w2, h2 = media.encode_image(small)
    check('小图不放大', (w2, h2) == (120, 90), (w2, h2))

    # 一张纯噪声的图最坏：PNG/JPEG 都压不下去，得靠继续缩尺寸
    noise = Image.frombytes('RGB', (1600, 1600), os.urandom(1600 * 1600 * 3))
    np_path = os.path.join(TMP, 'noise.png')
    noise.save(np_path)
    nb, nw, nh = media.encode_image(np_path)
    # 上限说的是「编码后的二进制」；nb 是 base64，字符数会大 4/3
    check('大噪声图也压到上限以内', len(nb) * 3 // 4 <= media.MAX_BYTES,
          '%.0f KB' % (len(nb) * 3 / 4096.0))
    # 上限真的会逼着它继续缩尺寸：把上限临时调到 20KB，再看输出的长边
    old_cap = media.MAX_BYTES
    media.MAX_BYTES = 20 * 1024
    try:
        sb, sw_, sh_ = media.encode_image(np_path)
        check('压不下去时会自动缩尺寸', len(sb) <= media.MAX_BYTES
              and max(sw_, sh_) < media.MAX_SIDE,
              '%dx%d %.0fKB' % (sw_, sh_, len(sb) / 1024.0))
    finally:
        media.MAX_BYTES = old_cap

    print('\n--- 图片：坏输入 ---')
    bad = os.path.join(TMP, 'not-image.txt')
    with open(bad, 'w', encoding='utf-8') as f:
        f.write('这不是图片')
    try:
        media.encode_image(bad)
        check('不是图片会报错', False)
    except ValueError as e:
        check('不是图片会报错', True, str(e))
    try:
        media.encode_image(os.path.join(TMP, 'missing.png'))
        check('文件不存在会报错', False)
    except ValueError:
        check('文件不存在会报错', True)
    check('乱码 base64 解码返回 None', media.decode('这不是base64') is None)
    check('空 base64 解码返回 None', media.decode('') is None)

    print('\n--- 文件 ---')
    f = os.path.join(TMP, 'note.txt')
    with open(f, 'wb') as fh:
        fh.write(b'hello pet-club')
    fb, size = media.encode_file(f)
    check('文件按原样编码', size == 14, size)
    check('文件能解回原文', b'hello pet-club' ==
          __import__('base64').b64decode(fb))

    empty = os.path.join(TMP, 'empty.txt')
    open(empty, 'wb').close()
    try:
        media.encode_file(empty)
        check('空文件会被拒', False)
    except ValueError as e:
        check('空文件会被拒', True, str(e))

    huge = os.path.join(TMP, 'huge.bin')
    with open(huge, 'wb') as fh:
        fh.write(b'\0' * (media.MAX_FILE + 10))
    try:
        media.encode_file(huge)
        check('超过 2MB 会被拒', False)
    except ValueError as e:
        check('超过 2MB 会被拒', True, str(e))

    print('\n--- 缩略图缓存 ---')
    t1 = media.thumb(b64, limit=220)
    check('能从 base64 出缩略图', t1 is not None, t1.size if t1 else None)
    check('缩略图不超过 limit 宽', t1 is not None and t1.width <= 220, t1.width if t1 else 0)
    t2 = media.thumb(b64, limit=220)
    check('同一张图第二次直接命中缓存', t2 is t1)
    t3 = media.thumb(b64, limit=120)
    check('换了尺寸就是新的一份', t3 is not t1 and t3.width <= 120,
          t3.width if t3 else 0)
    t4 = media.thumb('', small, 220)
    check('能从本地路径出缩略图', t4 is not None and t4.width == 120,
          t4.size if t4 else None)
    check('坏的 base64 返回 None', media.thumb('这不是base64', '', 220) is None)
    check('路径不存在返回 None', media.thumb('', os.path.join(TMP, 'nope.png'), 220) is None)
    check('两个都空返回 None', media.thumb('', '', 220) is None)

    print('\n--- 表情字符表 ---')
    check('表情不少于 12 个（够用但不啰嗦）', len(media.EMOJI_CHARS) >= 12,
          len(media.EMOJI_CHARS))
    check('表情没有重复', len(set(media.EMOJI_CHARS)) == len(media.EMOJI_CHARS))

    # 表情页第一格那张自定义贴图是**可选**的：有就要能正常显示（尺寸 / 透明底），
    # 没有也不算错 —— 面板会直接少一格，不留空格子。
    doge = media.doge_path(assets_dir())
    check('贴图路径约定在 assets/emoji/doge.png',
          doge.replace('\\', '/').endswith('assets/emoji/doge.png'), doge)
    if os.path.exists(doge):
        im = Image.open(doge)
        check('贴图是正方形、边长够显示', im.width == im.height and im.width >= 64,
              im.size)
        check('贴图带透明通道（不是白底方块）',
              im.mode == 'RGBA' and im.getextrema()[3][0] == 0, im.mode)

    print('\n--- 角色表情包素材 ---')
    for role in ROLES:
        paths = media.sticker_paths(assets_dir(), role)
        check('%s 有 8 张' % role, len(paths) == 8, len(paths))
    check('不存在的角色返回空表',
          media.sticker_paths(assets_dir(), 'p9') == [])

    print('\n通过 %d 项，失败 %d 项' % (_n[0] - len(_fail), len(_fail)))
    return 1 if _fail else 0


if __name__ == '__main__':
    sys.exit(main())
