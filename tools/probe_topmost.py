# -*- coding: utf-8 -*-
"""置顶与遮挡判定：桌宠必须压在所有普通窗口上方，且不能被自己的面板挡住。

这里验的是「z 序上到底谁在上面」，全靠真窗口 + 真 Win32 查询，
不看代码里的意图。需要一块真桌面。

跑法：py tools/probe_topmost.py
"""

import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ctypes import wintypes                                    # noqa: E402

from PySide6.QtCore import Qt                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget            # noqa: E402

import petclub.app as APP                                      # noqa: E402
from petclub import win32                                      # noqa: E402
from petclub.config import ROLES                                # noqa: E402

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

PASS = []
FAIL = []
SKIP = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    if detail and not isinstance(detail, str):
        detail = repr(detail)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''),
          flush=True)


def skip(name, why=''):
    SKIP.append(name)
    print('[skip] %s%s' % (name, ('  ← ' + why) if why else ''), flush=True)


def desktop_usable(qapp):
    """桌面上到底看不看得到我们自己的窗口？

    锁屏（或断开的远程桌面）时会铺一层全屏的 LockScreenBackstopFrame，
    WindowFromPoint 永远返回它 —— 「这一点上压着谁的窗口」这类断言全都没意义。
    所以先摆一个普通窗口在左上角，看系统还认不认得它；不认得就跳过，别拿
    假的 other 判失败。
    """
    probe = QWidget(None, Qt.FramelessWindowHint | Qt.Tool)
    probe.setStyleSheet('background: #ff00ff;')
    probe.setGeometry(120, 120, 80, 80)
    probe.show()
    probe.raise_()
    qapp.processEvents()
    mine = int(probe.winId())
    ok = win32.owner_at(160, 160, mine) == 'me'
    probe.hide()
    probe.deleteLater()
    qapp.processEvents()
    return ok


def is_topmost(win):
    """窗口当前是不是 WS_EX_TOPMOST —— 直接问系统，不看我们设了什么。"""
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    ex = u.GetWindowLongW(wintypes.HWND(int(win.winId())), GWL_EXSTYLE)
    return bool(ex & WS_EX_TOPMOST)


def main():
    QApplication.instance() or QApplication([])   # App 里要用

    print('--- 工作区 ---')
    left, top, right, bottom = win32.work_area()
    chk('工作区是个正经矩形', left < right and top < bottom,
        (left, top, right, bottom))
    # work_area() 给的是**物理像素**，screen.geometry() 是逻辑像素：
    # 150% 缩放下直接比会把「工作区」判成比屏幕还大。
    dpr = float(QApplication.primaryScreen().devicePixelRatio())
    screen = QApplication.primaryScreen().geometry()
    chk('工作区不比屏幕大', right - left <= int(screen.width() * dpr) + 1,
        (right - left, int(screen.width() * dpr)))

    # 不连服务器：这里只关心窗口，网络关掉更干净。
    app = APP.App('127.0.0.1', 1)
    app.players = {ROLES[0]: {'role': ROLES[0]}, ROLES[1]: {'role': ROLES[1]}}
    app.me = {'role': ROLES[0]}
    app.sync_pets()
    # 位置是 tick 里由 Pet.update 写进窗口的，而角色是「从天而降」出场的：
    # 不等它落地，命中点会落在屏幕上方 —— 那儿什么都没有。
    end = time.time() + 5.0
    while time.time() < end and any(p.phase != 'idle' for p in app.pets.values()):
        app.tick()
        app.qapp.processEvents()
        time.sleep(0.01)
    chk('角色都落地了', all(p.phase == 'idle' for p in app.pets.values()),
        [p.phase for p in app.pets.values()])
    pet = app.pets[ROLES[0]]
    other = app.pets[ROLES[1]]
    chk('两只角色都建出来了', len(app.pets) == 2, list(app.pets))

    print('\n--- 默认置顶 ---')
    chk('角色窗口默认是置顶的', is_topmost(pet.win))
    chk('另一只也是', is_topmost(other.win))

    print('\n--- 关掉置顶 ---')
    app._top_on = False
    pet.win.apply_top(False)
    app.qapp.processEvents()
    chk('关掉以后不是置顶窗口了', not is_topmost(pet.win))

    usable = desktop_usable(app.qapp)
    hit = pet.win.hit_point()

    print('\n--- 遮挡判定 ---')
    chk('角色身上有一个可用的命中点', hit is not None, hit)
    if not usable:
        skip('被自己的面板盖住 → 判成 own',
             '桌面上看不到自己的窗口（锁屏 / 远程桌面断开）')
    else:
        cover = QWidget(None, Qt.FramelessWindowHint | Qt.Tool)
        cover.setStyleSheet('background: #ffffff;')
        d = pet.win.dpr
        cover.setGeometry(int(hit[0] / d), int(hit[1] / d), 40, 40)
        cover.show()
        cover.raise_()
        app.qapp.processEvents()
        who = win32.owner_at(hit[0], hit[1], int(pet.win.winId()))
        chk('被自己的面板盖住 → 判成 own（不该抢置顶）', who == 'own', who)
        cover.hide()
        cover.deleteLater()
        app.qapp.processEvents()

    print('\n--- 开关置顶 ---')
    pet.win.apply_top(True)
    app.qapp.processEvents()
    chk('再打开又变回置顶窗口', is_topmost(pet.win))
    if not usable:
        skip('命中点上压着的就是它自己', '桌面上看不到自己的窗口')
        skip('命中点落在不透明像素上', '桌面上看不到自己的窗口')
    else:
        who = win32.owner_at(hit[0], hit[1], int(pet.win.winId()))
        chk('命中点上压着的就是它自己', who == 'me', who)
        chk('命中点落在不透明像素上（不会穿透到桌面）', who != 'other', who)

    print('\n--- 巡检：被别人盖住时插回去 ---')
    # 真开一个别的进程太贵，这里只验规则本身：把「被谁压着」的判定换成
    # other，看巡检会不会去插队、会不会被节流。
    app._top_on = True
    real_owner = APP.owner_at
    calls = []
    real_top = pet.win.reassert_top
    APP.owner_at = lambda x, y, me: 'other'
    pet.win.reassert_top = lambda: calls.append(1)
    try:
        app._top_checked = 0.0
        app.tick()
        chk('被别人的窗口盖住 → 重新插到最前面', len(calls) == 1, calls)
        app.tick()
        chk('3 秒内不重复插队（不然每秒抢两次焦点）', len(calls) == 1, calls)
        app._top_checked = 0.0
        app._top_on = False
        app.tick()
        chk('关掉「始终置顶」以后巡检什么都不做', len(calls) == 1, calls)
    finally:
        APP.owner_at = real_owner
        pet.win.reassert_top = real_top

    print('\n--- 图层开关 ---')
    app._top_on = True
    chk('菜单文案说「已勾选」', app.layer_label().startswith('✓'),
        app.layer_label())
    on = app.layer_toggle()
    chk('切一次变成不置顶', on is False, on)
    chk('两只角色都不再置顶', not is_topmost(pet.win) and not is_topmost(other.win),
        (is_topmost(pet.win), is_topmost(other.win)))
    chk('提示文案说明了现在的状态', '挡住' in app.layer_hint(), app.layer_hint())
    app.layer_toggle()
    chk('再切回来又是置顶', is_topmost(pet.win) and is_topmost(other.win))

    print('\n--- 收尾 ---')
    app.quit()
    chk('退出后窗口都不见了', not pet.win.isVisible() and not other.win.isVisible())

    print('\n通过 %d 项，失败 %d 项%s'
          % (len(PASS), len(FAIL),
             ('，跳过 %d 项' % len(SKIP)) if SKIP else ''))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
