# -*- coding: utf-8 -*-
"""生成表情包素材：assets/stickers/<角色>/<姿势>.png

表情包不用去网上找，也不用画：四个角色本来就有十一张姿势图，挑八张，
裁成 256×256 的卡片，底下配一句话，就是一套自己的表情包了。
好处是风格和桌面上那几只完全一致 —— 发出去的「比心」就是他自己在比心。

    py tools/make_stickers.py          # 全量重做
    py tools/make_stickers.py p4       # 只做某一个角色

卡片长这样：
    ┌──────────────┐
    │              │
    │   立绘（等比缩进 190×172 的框，切着底边放） │
    │              │
    │     「吨吨吨」  │
    └──────────────┘
白底圆角卡片是因为它要贴在灰色的消息区上：透明底的表情包在灰底上边缘会糊，
加一圈极浅的描边就干净了。
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, 'assets')

SIZE = 256                  # 卡片边长（发出去会缩到 128，留一倍余量）
SS = 2                      # 超采样倍数：先按 512 画再缩回 256，圆角和文字才不毛边
PAD = 16                    # 立绘离卡片左右边的空隙
ART_H = 196                 # 立绘可用高度
CAP_H = 40                  # 底部文字区高度

# 表情包顺序 = petclub/media.STICKER_POSES
POSES = ('idle', 'wave', 'drink', 'heart', 'jump', 'sleep', 'knock', 'drag')

# 每句话都要合角色的口吻（p1 松散、p2 干练、p3 温和、p4 撒娇）
CAPTION = {
    'p1': {'idle': '摸鱼中', 'wave': '嗨呀', 'drink': '吨吨吨', 'heart': '比心',
           'jump': '冲鸭', 'sleep': '躺平', 'knock': '咚咚咚', 'drag': '我先溜'},
    'p2': {'idle': '在忙', 'wave': '来了', 'drink': '喝一口', 'heart': '收下',
           'jump': '走起', 'sleep': '勿扰', 'knock': '开门', 'drag': '让让'},
    'p3': {'idle': '我在', 'wave': '慢走', 'drink': '喝茶', 'heart': '送你',
           'jump': '动一动', 'sleep': '静心', 'knock': '打扰了', 'drag': '随缘'},
    'p4': {'idle': '在呢', 'wave': '嗨嗨', 'drink': '咕嘟', 'heart': '贴贴',
           'jump': '蹦~', 'sleep': '困了', 'knock': '咚咚', 'drag': '别拽'},
    'p5': {'idle': '挂机中', 'wave': '来了', 'drink': '补水', 'heart': '接住',
           'jump': '动一下', 'sleep': '编译中', 'knock': '叩叩', 'drag': '拎走'},
}

CARD_BG = (255, 255, 255, 255)
CARD_EDGE = (232, 234, 240, 255)

# 文字颜色跟头像色块同一套，五个角色一眼分得开
INK = {'p1': (91, 91, 240), 'p2': (15, 157, 118),
       'p3': (224, 140, 42), 'p4': (214, 69, 93),
       'p5': (43, 143, 214)}

FONT_CANDIDATES = ('C:/Windows/Fonts/msyhbd.ttc', 'C:/Windows/Fonts/msyh.ttc',
                   'C:/Windows/Fonts/simhei.ttf', '/System/Library/Fonts/PingFang.ttc')


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_card(role, pose):
    src = os.path.join(ASSETS, 'roles', role, pose + '.png')
    if not os.path.exists(src):
        return None
    art = Image.open(src).convert('RGBA')
    box = art.split()[3].getbbox()
    if box:
        art = art.crop(box)

    # 所有尺寸先乘 SS 再算：卡片是 512 画的，立绘也得按 512 的标准去缩，
    # 否则缩回去只有一半大（第一版就是这么错的：图小、字大、四周一圈空白）。
    cw = SIZE * SS
    k = min((cw - 2 * PAD * SS) / float(art.width),
            ART_H * SS / float(art.height))
    w = max(1, int(round(art.width * k)))
    h = max(1, int(round(art.height * k)))
    art = art.resize((w, h), Image.LANCZOS)

    card = Image.new('RGBA', (cw, cw), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, cw - 1, cw - 1), radius=44,
                        fill=CARD_BG, outline=CARD_EDGE, width=3)

    ax = (cw - w) // 2
    ay = cw - CAP_H * SS - h - 6 * SS      # 立绘切着文字区上沿站好
    card.alpha_composite(art, (ax, ay))

    f = font(SIZE // 9 * SS)
    text = CAPTION.get(role, {}).get(pose, '')
    if text:
        tw = d.textlength(text, font=f)
        d.text(((cw - tw) / 2.0, cw - CAP_H * SS + 6 * SS),
               text, font=f, fill=INK.get(role, (60, 60, 60)) + (255,))

    return card.resize((SIZE, SIZE), Image.LANCZOS)


def main(argv):
    roles = argv[1:] or ['p1', 'p2', 'p3', 'p4']
    total = 0
    for role in roles:
        out_dir = os.path.join(ASSETS, 'stickers', role)
        os.makedirs(out_dir, exist_ok=True)
        for pose in POSES:
            card = make_card(role, pose)
            if card is None:
                print('  跳过 %s/%s（没有这张立绘）' % (role, pose))
                continue
            out = os.path.join(out_dir, pose + '.png')
            card.save(out, 'PNG', optimize=True)
            total += 1
            print('  %s  %s' % (os.path.relpath(out, ROOT),
                                '%.1f KB' % (os.path.getsize(out) / 1024.0)))
    print('\n共 %d 张表情包' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
