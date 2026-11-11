# -*- coding: utf-8 -*-
"""生成 macOS 用的图标：assets/app.icns + 菜单栏用的两张 PNG。

    py -3 tools/make_mac_icons.py

三份产物、两种用途：

  app.icns             .app 的图标，Finder / Dock / 访达里显示
  tray_mac.png         菜单栏图标（常态）
  tray_mac_alert.png   菜单栏图标（有未读，右上角压一个红点）

**为什么不用 iconutil**：那是 macOS 自带的命令行工具。这个脚本要在 Windows 上
也能跑（发版的人可能就在 Windows 打包），所以 .icns 由 Pillow 直接写 —— 它产出
的是标准 ICNS 容器，里面 ic07~ic14 八个尺寸各一份 PNG，macOS 会按位置自己挑。

尺寸上的实话：素材原图（roles/p1/idle.png）只有 81×210，512 和 1024 那两档
是放大的，近看会糊。Dock 里实际用 128/256，那两档是清楚的，所以不额外处理。
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

SRC = os.path.join(ROOT, 'assets', 'app.ico')
ICNS = os.path.join(ROOT, 'assets', 'app.icns')
TRAY = os.path.join(ROOT, 'assets', 'tray_mac.png')
TRAY_ALERT = os.path.join(ROOT, 'assets', 'tray_mac_alert.png')

# 菜单栏图标 22pt；@2x 就是 44px。给 44 让 Retina 上是清楚的，
# 1x 外接屏由 Qt 缩到 22，锯齿可控。
TRAY_PX = 44
TRAY_MARGIN = 0.08


def _biggest(path):
    """从多帧 ico 里挑最大的一帧。"""
    im = Image.open(path)
    im.size
    im.load()
    return im.convert('RGBA')


def _fit(im, px, margin):
    """裁紧内容 → 等比放进 px 见方的透明画布（留 margin 的呼吸位）。"""
    box = im.split()[3].getbbox() or (0, 0, im.width, im.height)
    im = im.crop(box)
    inner = int(px * (1 - 2 * margin))
    scale = min(inner / float(im.width), inner / float(im.height))
    im = im.resize((max(1, int(im.width * scale)),
                    max(1, int(im.height * scale))), Image.LANCZOS)
    canvas = Image.new('RGBA', (px, px), (0, 0, 0, 0))
    canvas.paste(im, ((px - im.width) // 2, (px - im.height) // 2), im)
    return canvas


def main():
    if not os.path.exists(SRC):
        print('先跑 python tools/make_icon.py')
        return 1
    base = _biggest(SRC)

    # .icns：Pillow 会写 TOC + ic07(128) / ic08(256) / ic09(512) / ic10(1024)
    #                + ic11(32@2x) / ic12(64) / ic13(256@2x) / ic14(512@2x)
    base.save(ICNS, format='ICNS')
    print('已生成 %s（%d 字节）' % (ICNS, os.path.getsize(ICNS)))

    _fit(base, TRAY_PX, TRAY_MARGIN).save(TRAY, format='PNG')
    print('已生成 %s（%d 字节）' % (TRAY, os.path.getsize(TRAY)))

    # 带红点那张复用 Windows 的同一套画法，保证两边「未读」长得一样
    from make_alert_icon import build
    alert = build(256)
    _fit(alert, TRAY_PX, TRAY_MARGIN).save(TRAY_ALERT, format='PNG')
    print('已生成 %s（%d 字节）' % (TRAY_ALERT, os.path.getsize(TRAY_ALERT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
