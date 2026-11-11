# -*- coding: utf-8 -*-
"""生成「有未读」的托盘图标：assets/app_alert.ico。

    py -3 tools/make_alert_icon.py

托盘闪烁靠两张图标交替，所以得有第二张。不手绘：直接拿 app.ico 的 256 帧，
在右上角压一个红点（颜色和聊天列表里的未读红点同一个），再存成多尺寸 ico。

红点位置和大小是按「16×16 显示时还能看出是个点」反推的：
256 画布上直径 104（半径 52），缩到 16 还剩 6.5 像素，够。
白色描边不是为了好看 —— 图标底色深浅不定，没有描边红点会糊进背景。

改图标（换角色、换配色）之后重跑一次就行，不用改代码。
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'assets', 'app.ico')
DST = os.path.join(ROOT, 'assets', 'app_alert.ico')

BADGE = '#f0445a'          # 与 chat.py 里未读红点同一个颜色
RING = '#ffffff'
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def build(size=256):
    im = Image.open(SRC)
    im.size          # 触发解码
    im.load()
    im = im.convert('RGBA')
    if im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)

    d = ImageDraw.Draw(im)
    cx, cy, r = size * 0.815, size * 0.175, size * 0.15
    w = max(2, int(size * 0.028))
    # 先描一圈白，再填红：顺序反了就变成红底白圈，看着像个靶子
    d.ellipse([cx - r - w, cy - r - w, cx + r + w, cy + r + w], fill=RING)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BADGE)
    return im


def main():
    big = build(256)
    big.save(DST, format='ICO', sizes=SIZES)
    print('已生成 %s（%d 字节）' % (DST, os.path.getsize(DST)))


if __name__ == '__main__':
    main()
