# -*- coding: utf-8 -*-
"""生成 exe 用的图标文件 assets/app.ico。

用角色本人的立绘当图标 —— 桌面挂件的图标就该是它自己，不该是个抽象 logo。
难点只有排版：立绘是 81x210 的细长条，小尺寸下直接被拉成一根面条。所以先把
人物按内容裁紧，等比放进正方形画布（留一圈呼吸位），再交给 Pillow 出多尺寸
ICO；Windows 会按显示位置（任务栏 / 标题栏 / 桌面）自己挑一张。

跑法：python tools/make_icon.py
"""

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SRC = os.path.join(ROOT, 'assets', 'roles', 'p1', 'idle.png')
OUT = os.path.join(ROOT, 'assets', 'app.ico')

SIZE = 256
MARGIN = 0.06               # 四周留 6% 的空白：贴边的图标在任务栏里显得很挤
SIZES = [16, 24, 32, 48, 64, 128, 256]


def square(im):
    """裁紧内容 → 等比缩放 → 居中贴到透明正方形上。"""
    box = im.split()[3].getbbox() or (0, 0, im.width, im.height)
    im = im.crop(box)
    inner = int(SIZE * (1 - 2 * MARGIN))
    scale = min(inner / im.width, inner / im.height)
    im = im.resize((max(1, int(im.width * scale)),
                    max(1, int(im.height * scale))), Image.LANCZOS)
    canvas = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(im, ((SIZE - im.width) // 2, (SIZE - im.height) // 2), im)
    return canvas


def main():
    if not os.path.exists(SRC):
        print('缺少素材：%s' % SRC)
        return 1
    im = square(Image.open(SRC).convert('RGBA'))
    im.save(OUT, format='ICO', sizes=[(s, s) for s in SIZES])
    print('已生成 %s（%s）' % (OUT, ', '.join(str(s) for s in SIZES)))

    pv = os.path.join(ROOT, 'assets', '_gen', 'app_icon_preview.png')
    os.makedirs(os.path.dirname(pv), exist_ok=True)
    im.save(pv)
    print('预览 %s' % pv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
