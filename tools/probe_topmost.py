# -*- coding: utf-8 -*-
"""探针：置顶巡检、聊天面板的层级、群聊在线人数。

这几个都不是「看一眼代码就知道对不对」的东西 ——
置顶要靠真实的 z 序去验（还得有一个**别的进程**来压它），
聊天面板要让位也得有别人来压，在线人数要靠真实的 ChatWindow 去验。
所以单独做个能跑一遍的探针：

    py -3 tools/probe_topmost.py

会临时起一个独立的 Python 进程，开一个置顶窗口压住宠物，检查
App._keep_top 有没有把它重新插回最前面；再用一个别的进程的普通窗口验
「点微信时聊天面板会不会让位、点回来会不会重新置顶」；
最后开一个聊天窗，检查「n/m 在线」是不是把自己算进去了。
"""
import ctypes
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, '.')

from petclub import chat as chatmod                  # noqa: E402
from petclub import config, media, petwin, surface   # noqa: E402
from petclub.app import App                          # noqa: E402
from petclub.chat import ChatWindow                  # noqa: E402
from petclub.sprites import load_all                 # noqa: E402

FAILED = []


def reset_image_caches():
    """一个进程里连着开好几个 Tk 根时，必须清一次图片缓存。

    上一个根销毁会把它的 PhotoImage 一起带走，而 media / chat 的头像缓存是
    全局的 —— 不清就会拿到「image pyimage1 does not exist」。这是探针自己的
    毛病（真实程序只有一个根），但不清就没法连着跑几个场景。
    """
    media.clear_cache()
    chatmod._PHOTO_CACHE.clear()


def wait_until(cond, timeout=2.0, step=0.05):
    """反复问一个条件，成立就返回 True。z 序变化是异步的，只能这么等。"""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def check(name, ok, detail=''):
    print('%s %s' % ('[ok  ]' if ok else '[FAIL]', name)
          + ('  %s' % (detail,) if detail else ''))
    if not ok:
        FAILED.append(name)


class FakePet:
    """只带巡检需要的那个成员：win.hit_point / win.reassert_top。"""

    def __init__(self, win):
        self.win = win
        self.calls = 0


class CountingWin:

    def __init__(self, win):
        self._win = win
        self.calls = 0

    def hit_point(self):
        return self._win.hit_point()

    @property
    def surface(self):
        return self._win.surface

    def reassert_top(self):
        self.calls += 1
        return self._win.reassert_top()


def keeper(app, pet):
    app.pets = {'p1': pet}
    app._last_fg = None                     # 强制当成「刚切过前台」
    app._top_checked = 0.0
    App._keep_top(app, time.monotonic())


def probe_top():
    root = tk.Tk()
    root.withdraw()
    sp = load_all(config.assets_dir(), ['p1'])['p1']
    win = petwin.PetWindow(root, sp)
    win.show('idle', 520, 760)
    root.update()
    time.sleep(0.3)
    root.update()

    pt = win.hit_point()
    hwnd = win.surface.hwnd
    check('角色身上那个巡检点确实落在自己身上',
          surface.owner_at(pt[0], pt[1], hwnd) == 'me', str(pt))

    # 起一个别的进程的置顶窗口，正好盖住角色
    code = (
        "import tkinter as tk\n"
        "r = tk.Tk()\n"
        "r.overrideredirect(True)\n"
        "r.geometry('420x420+%d+%d')\n"
        "r.attributes('-topmost', True)\n"
        "r.configure(bg='#333333')\n"
        "r.mainloop()\n" % (pt[0] - 210, pt[1] - 210)
    )
    other = subprocess.Popen([sys.executable, '-c', code])
    try:
        time.sleep(2.0)
        root.update()
        check('别的进程压上来之后，命中测试认得出来',
              surface.owner_at(pt[0], pt[1], hwnd) == 'other')

        app = App.__new__(App)
        win_counter = CountingWin(win)
        pet = FakePet(win)
        pet.win = win_counter

        petwin.TOP_ON['on'] = True
        keeper(app, pet)
        time.sleep(0.8)
        root.update()
        check('巡检会把角色重新插回最前面', pet.win.calls == 1,
              '调用 %d 次' % pet.win.calls)
        check('插队之后命中测试又回到自己身上',
              surface.owner_at(pt[0], pt[1], hwnd) == 'me')

        # 关掉「始终置顶」时一次都不许抢
        petwin.TOP_ON['on'] = False
        pet.win.calls = 0
        keeper(app, pet)
        check('关掉置顶后巡检不动手', pet.win.calls == 0,
              '调用 %d 次' % pet.win.calls)
    finally:
        petwin.TOP_ON['on'] = True
        other.terminate()
        win.destroy()
        root.destroy()


def _foreign_window(x, y, w, h):
    """在**另一个进程**里开一个普通窗口，摆在 (x, y)。返回 (进程, 顶层句柄)。

    为什么非要另一个进程：同一个进程的窗口在 z 序上是一伙的，用一个进程里的
    两个窗口验「谁盖住谁」验不出东西来。
    """
    code = (
        "import tkinter as tk\n"
        "r = tk.Tk()\n"
        "r.overrideredirect(True)\n"
        "r.geometry('%dx%d+%d+%d')\n"
        "r.configure(bg='#3a3a3a')\n"
        "r.update()\n"
        "print(r.winfo_id(), flush=True)\n"
        "r.mainloop()\n" % (w, h, x, y))
    proc = subprocess.Popen([sys.executable, '-c', code],
                            stdout=subprocess.PIPE, text=True)
    line = (proc.stdout.readline() or '').strip()
    hwnd = surface.root_of(int(line)) if line.isdigit() else 0
    return proc, hwnd


def _raise_window(hwnd):
    """把别人那个窗口顶到普通层最前面（不抢焦点、不挪位置）。"""
    u = ctypes.WinDLL('user32')
    u.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(0), 0, 0, 0, 0,
                   0x0001 | 0x0002 | 0x0010)


def probe_chat_top():
    """聊天面板不许「永远置顶」：用户切到微信，它必须让位。

    以前 CardWindow 一上来就 `-topmost True`，结果面板压在所有程序上面，
    点微信也翻不上来（用户实测反馈）。现在的规矩是「只有自己在前台才置顶」，
    这里用真窗口验三件事：认得出前台不是自己、让位之后真的被盖住、
    点回面板又回到最前面。
    """
    root = tk.Tk()
    root.withdraw()
    # 一个进程里连着开好几个 Tk 根，上一个根销毁时会把它的 PhotoImage 一起带走，
    # 而 media 的缓存是全局的 —— 不清就会拿到「image pyimage1 does not exist」。
    # 这是探针自己的毛病（真实程序只有一个根），但不清就没法连着跑。
    reset_image_caches()
    w = ChatWindow(root, on_send=lambda *a: '', me='p1',
                   is_online=lambda r: True)
    w.show()
    root.update()
    time.sleep(0.3)
    root.update()
    check('面板起来时是置顶的', int(w.top.attributes('-topmost')) == 1,
          str(w.top.attributes('-topmost')))

    cx = w.top.winfo_rootx() + w.top.winfo_width() // 2
    cy = w.top.winfo_rooty() + 140
    other, ohwnd = _foreign_window(cx - 160, cy - 110, 320, 220)
    try:
        time.sleep(1.5)
        root.update()
        check('面板认得出「前台是我自己」', w.owns(w.hwnd()), hex(w.hwnd()))
        check('面板认得出「前台是别的程序」',
              ohwnd != 0 and not w.owns(ohwnd), hex(ohwnd))

        # 模拟用户点微信：前台换成别人 → 让位；再把对方顶到最前，它就盖住面板了
        w._pop = tk.Toplevel(w.top)          # 假装表情浮层还开着
        pop = w._pop
        check('切走之后面板让位（退回普通层）', w.follow(ohwnd) is False)
        check('-topmost 真的被摘掉了', int(w.top.attributes('-topmost')) == 0,
              str(w.top.attributes('-topmost')))
        check('表情浮层跟着一起收', w._pop is None and not pop.winfo_exists())
        _raise_window(ohwnd)
        # z 序变化是异步的（窗口管理器那边走消息），给一段时间反复问，
        # 别用「睡 0.4 秒问一次」—— 那玩意儿在慢机器上会偶发红。
        covered = wait_until(lambda: surface.owner_at(cx, cy, w.hwnd()) == 'other')
        check('别的程序确实压在面板上面', covered)

        # 点回面板：必须立刻插回置顶带，不然会被一直置顶的桌宠盖住
        check('点回面板又置顶了', w.follow(w.hwnd()) is True)
        check('-topmost 又设回来了', int(w.top.attributes('-topmost')) == 1)
        back = wait_until(lambda: surface.owner_at(cx, cy, w.hwnd()) == 'me')
        check('面板重新压过那个窗口', back)
    finally:
        other.terminate()
        w.top.destroy()
        root.destroy()


def probe_online():
    root = tk.Tk()
    root.withdraw()
    reset_image_caches()         # 理由同 probe_chat_top
    players = {'p1': True, 'p2': True}
    w = ChatWindow(root, on_send=lambda *a: '', me='p1',
                   is_online=lambda r: bool(players.get(r)))
    check('两人在线时显示 2/5（自己算一个）',
          w.state_lb.cget('text').startswith('2/5'), w.state_lb.cget('text'))

    players['p3'] = True
    w.refresh_state()
    check('有人进来后当场变成 3/5',
          w.state_lb.cget('text').startswith('3/5'), w.state_lb.cget('text'))
    w.top.destroy()
    root.destroy()


if __name__ == '__main__':
    probe_top()
    probe_chat_top()
    probe_online()
    print()
    if FAILED:
        print('失败 %d 项：%s' % (len(FAILED), '、'.join(FAILED)))
        sys.exit(1)
    print('全部通过')
