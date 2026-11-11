# -*- coding: utf-8 -*-
"""生成界面预览图（真 Tk 渲染 + 截图）。

改完界面想看看效果、又不想每次手动开程序时就跑它：
    python tools/shot_ui.py
图存到 docs/preview/。
"""

import ctypes
import os
import sys
import time
import tkinter as tk
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ⚠️ 必须在 tk.Tk() 之前调用：进程若不做 DPI 感知，Tk 报的是逻辑坐标、
# 截出来的图会整体错位（第一版就是这样偏左的）。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)      # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PIL import Image                            # noqa: E402
from petclub import media                        # noqa: E402
from petclub.app import App                      # noqa: E402
from petclub.chat import ChatWindow              # noqa: E402
from petclub.ui import ClaimDialog               # noqa: E402

OUT = os.path.join(ROOT, 'docs', 'preview')

_u = ctypes.windll.user32
_g = ctypes.windll.gdi32
_u.GetWindowDC.argtypes = [wintypes.HWND]
_u.GetWindowDC.restype = wintypes.HDC
_u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_u.PrintWindow.restype = wintypes.BOOL
_g.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g.CreateCompatibleDC.restype = wintypes.HDC
_g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_g.SelectObject.restype = wintypes.HGDIOBJ
_g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                         wintypes.UINT, wintypes.LPVOID, wintypes.LPVOID,
                         wintypes.UINT]
_g.GetDIBits.restype = ctypes.c_int
_g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g.DeleteDC.argtypes = [wintypes.HDC]


class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


def settle(root, n=40, delay=0.02):
    """多刷几帧，等窗口真的画出来再截。"""
    for _ in range(n):
        root.update()
        time.sleep(delay)


def window_rect(widget):
    """窗口在屏幕上的物理矩形（含标题栏与边框）。"""
    hwnd = _u.GetAncestor(widget.winfo_id(), 2)     # GA_ROOT
    if not hwnd:
        hwnd = widget.winfo_id()
    r = wintypes.RECT()
    if _u.GetWindowRect(hwnd, ctypes.byref(r)):
        return r.left, r.top, r.right, r.bottom
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    return x, y, x + widget.winfo_width(), y + widget.winfo_height()


def print_window(widget):
    """让窗口把自己画到内存里，然后读出来。

    **不用 ImageGrab 抓屏幕**：那抓的是「屏幕上此刻的像素」—— 窗口被别的窗口
    盖住、或者跑在没有真桌面的会话里（远程桌面断开、CI），抓到的就是桌面壁纸。
    第一版这么写，结果一整批预览图全变成了壁纸。
    PrintWindow 是让窗口自己往内存 DC 上重画一遍，不依赖合成器，也不怕被挡住。
    """
    hwnd = _u.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()
    left, top, right, bottom = window_rect(widget)
    w, h = max(1, right - left), max(1, bottom - top)

    hdc = _u.GetWindowDC(hwnd)
    mdc = _g.CreateCompatibleDC(hdc)
    bmp = _g.CreateCompatibleBitmap(hdc, w, h)
    old = _g.SelectObject(mdc, bmp)
    try:
        # 2 = PW_RENDERFULLCONTENT：让 DWM 把内容也算进去
        if not _u.PrintWindow(hwnd, mdc, 2):
            _u.PrintWindow(hwnd, mdc, 0)
        bi = _BMIH()
        bi.biSize = ctypes.sizeof(_BMIH)
        bi.biWidth = w
        bi.biHeight = -h            # 负数 = 自上而下，省一次翻转
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0        # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not _g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            raise RuntimeError('GetDIBits 失败')
        return Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1)
    finally:
        _g.SelectObject(mdc, old)
        _g.DeleteObject(bmp)
        _g.DeleteDC(mdc)
        _u.ReleaseDC(hwnd, hdc)


def shot(root, win, name):
    try:
        win.update_idletasks()
    except tk.TclError:
        print('%s  窗口已经收起来了，跳过' % name)
        return None
    settle(root)
    try:
        win.lift()
    except tk.TclError:                 # 表情面板这类会自己收起来的窗口
        print('%s  窗口已经收起来了，跳过' % name)
        return None
    settle(root, 10)
    try:
        im = print_window(win)
    except Exception as e:              # 抓不到不该让整批图停在半路
        print('%s  抓不到（%r）' % (name, e))
        return None
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    im.save(p)
    print('%s  (%dx%d)' % (p, im.width, im.height))
    return im


PLAYERS = {'p1': {'role': 'p1', 'name': '猥琐'}, 'p2': None, 'p3': None,
           'p4': None, 'p5': None}


def shot_menu(app, root):
    """自己的菜单：指令 + 服务器成员 + 一条冷却中的指令。"""
    app.me = {'role': 'p1'}
    app.players = {'p1': {'role': 'p1'}, 'p2': {'role': 'p2'}, 'p3': None,
                   'p4': None}
    app.cd.mark(app._cd_key('drink', None), time.monotonic(), 4.0)
    m = app.build_self_menu()
    m.popup(260, 160)
    shot(root, m.top, 'menu.png')
    m.close()
    app.cd.reset()


def shot_chat(root):
    """聊天面板：四种消息都要出现（文字 / 表情 / 图片 / 文件）+ 互动系统提示。"""
    now = time.time()

    def msg(who, text, ago):
        return {'kind': 'msg', 'who': who, 'text': text, 'ts': now - ago}

    def tip(text, ago):
        return {'kind': 'tip', 'who': '', 'text': text, 'ts': now - ago}

    def sticker(who, role, pose, ago):
        b64, _w, _h = media.encode_image(
            os.path.join(ROOT, 'assets', 'stickers', role, pose + '.png'),
            side=media.MAX_SIDE, png=True)
        return {'kind': 'msg', 'mt': 'sticker', 'who': who, 'text': '[表情]',
                'img': b64, 'ts': now - ago}

    def image(who, path, ago):
        b64, _w, _h = media.encode_image(path)
        return {'kind': 'msg', 'mt': 'img', 'who': who, 'text': '[图片]',
                'img': b64, 'ts': now - ago}

    def filemsg(who, name, size, ago):
        return {'kind': 'msg', 'mt': 'file', 'who': who, 'text': '[文件] ' + name,
                'name': name, 'size': size, 'path': '', 'ts': now - ago}

    def emoji(who, ch, ago):
        return {'kind': 'msg', 'mt': 'emoji', 'who': who, 'text': '[表情]',
                'char': ch, 'ts': now - ago}

    log = {
        'all': [
            msg('p2', '晚上都在干嘛', 760),
            msg('me', '在写代码，别提了', 740),
            tip('胡大神 提醒大家喝水', 700),
            emoji('p4', '😄', 690),
            sticker('p4', 'p4', 'drink', 660),
            image('me', os.path.join(ROOT, 'assets', 'roles', 'p4', 'idle.png'), 420),
            tip('杨大师 向大家打了个招呼', 300),
            filemsg('p2', '周报-11月.xlsx', 48213, 120),
        ],
        'p2': [
            msg('me', '喝口水啊兄弟', 900),
            msg('p2', '在呢，这就去倒', 880),
            tip('胡大神 拍了拍你', 700),
            msg('me', '顺便帮我带一杯', 200),
        ],
        'p3': [
            tip('你 跟杨大师打了个招呼', 1800),
            msg('p3', '记得饮一杯温水', 240),
        ],
        'p4': [],
    }
    chat = ChatWindow(root, on_send=lambda text: '', me='p1',
                      is_online=lambda r: r in ('p2', 'p4'), log=log)
    chat.show()
    chat.switch('all')
    chat.unread['p3'] = 2
    chat.refresh_list()
    shot(root, chat.top, 'chat.png')

    # 再截一张顶部的：消息一多就会自动滚到底，预览图里得能看到时间行和最早那几句
    chat.msgs.yview_moveto(0.0)
    root.update()
    shot(root, chat.top, 'chat_top.png')

    # 表情面板单独来两张：两页各一张。每次重新开一个 ——
    # 连着用同一个 popup 截两张时，第二次 lift 会撞上「窗口已经没了」，
    # 这是截图工具自己的时序问题，和程序里点它没关系。
    for index, name in ((0, 'chat_stickers.png'), (1, 'chat_cards.png'),
                        (2, 'chat_others.png')):
        chat._pop_closed()
        chat._toggle_stickers()
        if chat._pop is None:
            break
        chat._pop.select(index)
        shot(root, chat._pop, name)
    chat.close()


def shot_tray(app, root):
    """托盘右键菜单。

    面板已经去掉了（左键直接开聊天），菜单是托盘上唯一的界面，所以要留一张。
    """
    app.me = {'role': 'p3'}
    app.players = {'p1': {'role': 'p1'}, 'p2': None, 'p3': {'role': 'p3'},
                   'p4': {'role': 'p4'}}
    m = app.tray_menu()
    m.popup(900, 700)
    shot(root, m.top, 'tray_menu.png')
    m.close()
    app.me = None
    app.players = {}


def main():
    # 不调 run() 就不会去连服务器 —— 这里只是借它的菜单构建和素材
    app = App('127.0.0.1', 9)
    root = app.root

    claim = ClaimDialog(root, PLAYERS)
    claim._pick('p3')
    shot(root, claim.top, 'claim.png')
    claim.cancel()

    claim2 = ClaimDialog(root, PLAYERS, note='「胡大神」已经被认领了')
    shot(root, claim2.top, 'claim_rejected.png')
    claim2.cancel()

    shot_menu(app, root)
    shot_tray(app, root)
    shot_chat(root)

    app.quit()


if __name__ == '__main__':
    main()
