# -*- coding: utf-8 -*-
"""角色缩放系数与气泡定位的防回归检查。

覆盖两块：
  PET_SCALE 只改「看起来多大」，不改业务坐标、不改落地/拖动的物理像素；
  气泡第一次显示、以及 hide 之后复用，都必须落在角色头顶而不是屏幕中间。

跑法：py tools/test_pet_scale.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication                     # noqa: E402

from petclub import config, windows                           # noqa: E402
from petclub.actors import Pet                                # noqa: E402
from petclub.config import ROLES, assets_dir                  # noqa: E402
from petclub.sprites import load_all                          # noqa: E402

PASS = []
FAIL = []
ORIGINAL = config.PET_SCALE


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  <- ' + str(detail) if detail and not ok else '')),
          flush=True)


def near(a, b, tol=1.5):
    return abs(a - b) <= tol


def main():
    qapp = QApplication.instance() or QApplication([])
    dpr = float(qapp.primaryScreen().devicePixelRatio())
    sp = load_all(assets_dir(), ROLES)['p1']
    made = []

    def make(scale):
        config.PET_SCALE = scale
        w = windows.PetWindow(None, sp)
        made.append(w)
        return w

    print('--- 显式缩放系数 ---')
    config.PET_SCALE = 0.0
    chk('默认跟随屏幕 dpr（复现旧 Tk 观感）',
        near(config.pet_scale(1.75), 1.75), config.pet_scale(1.75))
    config.PET_SCALE = 1.0
    chk('1.0 = 一比一，不受 dpr 影响', near(config.pet_scale(1.75), 1.0))
    config.PET_SCALE = 1.75
    chk('固定 1.75 倍可显式锁定', near(config.pet_scale(1.25), 1.75))

    one = make(1.0)
    big = make(1.75)
    follow = make(0.0)
    qapp.processEvents()

    chk('一比一时窗口物理宽 = 素材宽', near(one.win_w, sp.w))
    chk('一比一时窗口物理高 = 素材高', near(one.win_h, sp.h))
    chk('放大 1.75 倍后窗口物理宽 = 素材宽 × 1.75',
        near(big.win_w, sp.w * 1.75))
    chk('放大 1.75 倍后窗口物理高 = 素材高 × 1.75',
        near(big.win_h, sp.h * 1.75))
    chk('跟随 dpr 时放大倍数就是屏幕 dpr',
        near(follow.scale, dpr), (follow.scale, dpr))

    for name, w in (('一比一', one), ('1.75 倍', big), ('跟随 dpr', follow)):
        chk('%s：窗口逻辑宽 × dpr = 屏幕物理宽' % name,
            near(w._lw * dpr, w.win_w), (w._lw * dpr, w.win_w))
        chk('%s：位图设备比 = dpr / scale' % name,
            near(w._pix['idle'].devicePixelRatio(), dpr / w.scale, 0.001),
            (w._pix['idle'].devicePixelRatio(), dpr / w.scale))
        pm = w._pix['idle']
        chk('%s：位图逻辑尺寸不超出窗口' % name,
            0 < pm.width() / w.pdpr <= w._lw + 1
            and 0 < pm.height() / w.pdpr <= w._lh + 1,
            (pm.width() / w.pdpr, pm.height() / w.pdpr, w._lw, w._lh))

    print('\n--- 业务坐标不受缩放影响 ---')
    for name, w in (('一比一', one), ('1.75 倍', big)):
        w.show('idle', 1000, 900)
        qapp.processEvents()
        chk('%s：脚底中心仍落在 (1000, 900) 物理像素' % name,
            near((w.x() + w.width() / 2.0) * dpr, 1000)
            and near((w.y() + w.height()) * dpr, 900),
            (w.x(), w.y(), w.width(), w.height()))
        hit = w.hit_point()
        chk('%s：命中点落在窗口矩形里' % name,
            hit is not None
            and 1000 - w.win_w / 2.0 - 1 <= hit[0] <= 1000 + w.win_w / 2.0 + 1
            and 900 - w.win_h - 1 <= hit[1] <= 900, hit)

    p1 = Pet(sp, one, 640, 900, drop=True)
    p2 = Pet(sp, big, 640, 900, drop=True)
    chk('放大后出场起点仍在屏幕上方',
        near(p2.y, -big.win_h - 40.0) and p2.y < p1.y, (p1.y, p2.y))
    p1.y = p2.y = 900.0
    chk('气泡锚点跟着放大后的头顶抬高',
        near(p1.bubble_anchor()[1], 900 - sp.h - 6)
        and near(p2.bubble_anchor()[1], 900 - big.win_h - 6)
        and p2.bubble_anchor()[1] < p1.bubble_anchor()[1],
        (p1.bubble_anchor(), p2.bubble_anchor()))
    chk('气泡锚点的横向坐标不变', near(p2.bubble_anchor()[0], 640.0))

    print('\n--- 气泡第一次显示就贴住人物头顶 ---')
    b = windows.Bubble(None)
    b.show_at('到位啦～', 1000, 500)
    qapp.processEvents()
    chk('首次显示时已定位（不是 Qt 给的默认位置）',
        near(b.x(), 1000 / dpr - b.width() / 2.0)
        and near(b.y(), 500 / dpr - b.height()),
        (b.x(), b.y(), 1000 / dpr - b.width() / 2.0, 500 / dpr - b.height()))
    chk('首次显示时真的可见', b.isVisible())

    b.hide()
    qapp.processEvents()
    b.show_at('再来一句', 400, 300)
    qapp.processEvents()
    chk('hide 后复用会重新定位',
        near(b.x(), 400 / dpr - b.width() / 2.0)
        and near(b.y(), 300 / dpr - b.height()), (b.x(), b.y()))
    b.follow(700, 260)
    chk('拖动时 follow 仍然跟得上',
        near(b.x(), 700 / dpr - b.width() / 2.0), b.x())

    b.hide()
    for w in made:
        w.show('idle', 0, 0)
        w.hide()
        w.setParent(None)
    config.PET_SCALE = ORIGINAL
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for name in FAIL:
        print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
