# -*- coding: utf-8 -*-
"""托盘的测试：图标与图层开关。

    py tools/test_tray.py

分两部分：
  · 能在这里验的 —— 结构体大小对不对、消息映射对不对、图层开关切了以后
    窗口属性变没变。
  · 验不了的 —— 「图标在右下角长得对不对」「鼠标点上去手感如何」。
    那需要一双眼睛，这里不假装能测。

不起 App：托盘不依赖网络和身份，单独拉起来更快、更少干扰。
点左键开聊天、右键菜单里写了什么，都在 probe_live.py 里验（那边有 App）。
"""

import ctypes
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub import petwin, tray                         # noqa: E402
from petclub.surface import IS_WIN, work_area            # noqa: E402

_fail = []
_n = [0]


def check(name, ok, detail=''):
    _n[0] += 1
    print('[%s] %s%s' % ('ok  ' if ok else 'FAIL', name,
                         ('  ' + str(detail)) if detail else ''))
    if not ok:
        _fail.append(name)


def main():
    print('--- 结构体与常量 ---')
    if IS_WIN:
        # cbSize 传错一个字节，Shell_NotifyIcon 就静默失败（图标不出来、也没有报错），
        # 所以这个数字得钉死。Vista 之后是 976 字节，32 位系统上会小一些。
        size = ctypes.sizeof(tray.NOTIFYICONDATA)
        check('NOTIFYICONDATA 布局是 64 位的 976 字节',
              size == 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else size > 0, size)
    check('回调消息号在 WM_APP 之后（不会撞系统消息）',
          tray.WM_TRAYICON > tray.WM_APP > 0x7FFF, tray.WM_TRAYICON)
    check('提示气泡最多 127 个字（数组 128 要留结尾）', tray.TIP_MAX == 127,
          tray.TIP_MAX)

    print('\n--- 图标 ---')
    icon = os.path.join(ROOT, 'assets', 'app.ico')
    events = []
    icon_obj = tray.Tray(icon=icon, tip='pet-club test',
                         on_left=lambda x, y: events.append(('L', x, y)),
                         on_right=lambda x, y: events.append(('R', x, y)))
    if IS_WIN:
        check('图标挂上了', icon_obj.ok is True, icon_obj.ok)
        check('拿到一个隐藏窗口句柄', bool(icon_obj.hwnd), icon_obj.hwnd)
        # 没给 alert 图（这次就是）不该假装能闪，否则 App 会一直白调 blink
        check('没给第二张图时说不能闪', icon_obj.can_blink is False)
        check('不能闪的时候 blink 不炸也不换图',
              icon_obj.blink(True) is False and icon_obj._shown == icon_obj._icon)

        # 模拟系统投递：和 Shell_NotifyIcon 一样，是 POST 到那个窗口的
        tray.user32.PostMessageW(icon_obj.hwnd, tray.WM_TRAYICON, 0,
                                 tray.WM_LBUTTONUP)
        tray.user32.PostMessageW(icon_obj.hwnd, tray.WM_TRAYICON, 0,
                                 tray.WM_CONTEXTMENU)
        tray.user32.PostMessageW(icon_obj.hwnd, tray.WM_TRAYICON, 0, 0x0200)
        for _ in range(20):
            icon_obj.poll()
            if len(events) >= 2:
                break
            time.sleep(0.02)
        check('左键点击会回调 on_left', events and events[0][0] == 'L', events)
        check('右键点击会回调 on_right', len(events) > 1 and events[1][0] == 'R',
              events)
        check('只认按下/抬起，别的鼠标消息不回调', len(events) == 2, events)

        pos = tray.cursor_pos()
        check('能取到鼠标位置', isinstance(pos, tuple) and len(pos) == 2, pos)

    icon_obj.close()
    check('摘掉之后 ok 是 False', icon_obj.ok is False, icon_obj.ok)
    icon_obj.close()                # 再摘一次不该炸
    icon_obj.poll()                 # 没有窗口了也不该炸
    check('重复摘、重复轮询都不报错', True)

    print('\n--- 图层 ---')
    root = tk.Tk()                  # 图层是窗口属性，得有个真窗口才验得了
    root.withdraw()
    check('默认是始终置顶', petwin.TOP_ON['on'] is True, petwin.TOP_ON)
    win = tk.Toplevel(root)
    win.withdraw()
    petwin.apply_top(win)
    check('新窗口跟着当前设置走（置顶）',
          bool(int(win.attributes('-topmost'))) is True, win.attributes('-topmost'))

    petwin.set_layer(False)
    petwin.apply_top(win)
    check('切到「会被盖住」之后，窗口不再是 topmost',
          bool(int(win.attributes('-topmost'))) is False,
          win.attributes('-topmost'))
    check('状态跟着变', petwin.TOP_ON['on'] is False, petwin.TOP_ON)

    petwin.set_layer(True)
    check('切回置顶', petwin.TOP_ON['on'] is True, petwin.TOP_ON)
    win.destroy()

    root.destroy()

    print('\n通过 %d 项，失败 %d 项' % (_n[0] - len(_fail), len(_fail)))
    for name in _fail:
        print('  - %s' % name)
    return 1 if _fail else 0


if __name__ == '__main__':
    sys.exit(main())
