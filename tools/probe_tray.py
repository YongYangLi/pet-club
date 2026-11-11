# -*- coding: utf-8 -*-
"""探针：未读计数与托盘闪烁。

    py -3 tools/probe_tray.py

分两段：
  A. 真托盘 —— 真的往系统托盘挂图标，检查两张图能不能换（这个只能真跑，
     Shell_NotifyIcon 的行为没有别的办法验）；
  B. 未读与节拍 —— 用一个假的托盘接下 blink/set_tip 的调用，检查
     「谁说的话算未读」「闪的节奏对不对」「读完了会不会立刻停下」。
     App 是照常构造的（不 run，不连服务器），所以测的是真代码路径。
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, '.')

# App 起来时别真往托盘里挂第二个图标 —— 本探针自己会建一个来验。
os.environ.setdefault('PETCLUB_NO_TRAY', '1')

from petclub import tray                             # noqa: E402
from petclub.app import App                          # noqa: E402
from petclub.chat import ChatWindow                  # noqa: E402
from petclub.config import ROLES, assets_dir         # noqa: E402

FAILED = []


def check(name, ok, detail=''):
    print('%s %s' % ('[ok  ]' if ok else '[FAIL]', name)
          + ('  %s' % (detail,) if detail else ''))
    if not ok:
        FAILED.append(name)


class FakeTray:
    """接住 App 对托盘的两类调用，记下来给断言看。"""

    ok = True
    can_blink = True

    def __init__(self):
        self.blinks = []
        self.tips = []

    def blink(self, on):
        self.blinks.append(bool(on))
        return bool(on)

    def set_tip(self, text):
        self.tips.append(text)


class FakeChat:
    """只带 App 需要的三个成员：key / alive / on_new。"""

    def __init__(self, key, alive=True):
        self.key = key
        self._alive = alive
        self.new = []

    def alive(self):
        return self._alive

    def on_new(self, key):
        self.new.append(key)


def msg(who, text='在吗'):
    return {'kind': 'msg', 'who': who, 'text': text, 'ts': time.time()}


def probe_tray():
    print('---- A. 托盘图标 ----')
    t = tray.Tray(icon=os.path.join(assets_dir(), 'app.ico'),
                  tip='pet-club probe',
                  alert=os.path.join(assets_dir(), 'app_alert.ico'))
    check('托盘能挂上', t.ok, 'hwnd=%r' % (t.hwnd,))
    check('有第二张图标可以闪', t.can_blink, 'alert=%r' % (t._alert,))
    if not t.ok:
        return
    plain = t._icon

    check('切到未读图标', t.blink(True) is True and t._shown != plain,
          'shown=%r' % (t._shown,))
    alert = t._shown
    check('切回常态图标', t.blink(False) is False and t._shown == plain)
    # 同一张连着设不该再通知系统（闪烁每秒两次，重复调用是常态）
    t.blink(True)
    check('重复切同一张不重复下发', t._shown == alert)

    t.set_tip('pet-club probe · 3 条未读')
    check('悬停提示能改', '3 条未读' in str(t._nid.szTip), str(t._nid.szTip))
    t.close()
    check('撤掉之后不再算挂上', t.ok is False)


def probe_unread(app):
    print('---- B. 谁说的话算未读 ----')
    app.unread.clear()
    app.chat_log.clear()
    app.chat = None

    app._store('p2', msg('p2'))
    check('别人说的话记未读', app.unread.get('p2') == 1, repr(app.unread))

    app._store('p2', msg('p2', '再来一句'))
    check('同一个会话累加', app.unread.get('p2') == 2, repr(app.unread))

    app._store('p2', msg('me', '我自己说的'))
    check('自己说的不算', app.unread.get('p2') == 2, repr(app.unread))

    app._store('p2', {'kind': 'tip', 'who': 'p2', 'text': '拍了拍你',
                      'ts': time.time()})
    check('动作痕迹（tip）不算', app.unread.get('p2') == 2, repr(app.unread))

    # 面板开着、且选中的就是这个会话 → 已经读过了
    app.chat = FakeChat('p2')
    app._store('p2', msg('p2', '正看着呢'))
    check('正看着的会话不算未读', app.unread.get('p2') == 2, repr(app.unread))
    app._store('p3', msg('p3'))
    check('看着 p2 时 p3 来的仍算未读', app.unread.get('p3') == 1,
          repr(app.unread))

    # 面板被关掉（alive=False）→ 又该算了
    app.chat = FakeChat('p2', alive=False)
    app._store('p2', msg('p2', '面板已经关了'))
    check('面板关了之后照常记', app.unread.get('p2') == 3, repr(app.unread))

    check('未读总数', app._unread_total() == 4, repr(app.unread))

    app.chat_log['p2'].append({'kind': 'msg', 'who': 'p2', 'text': 'x',
                               'ts': 200.0})
    app.chat_log['p3'].append({'kind': 'msg', 'who': 'p3', 'text': 'y',
                               'ts': 100.0})
    check('点开时落在最新那条', app._latest_unread() == 'p2',
          repr(app._latest_unread()))


def probe_blink(app):
    print('---- C. 闪烁节拍 ----')
    fake = FakeTray()
    app.tray = fake
    app.unread.clear()
    app._tray_on = False
    app._tray_at = 0.0
    app._tray_tip = ''
    app.unread['p2'] = 2        # 两条没看的话

    app._tray_tick(10.0)
    check('有未读的第一拍就闪', fake.blinks == [True], repr(fake.blinks))
    check('悬停提示带上条数', bool(fake.tips) and '条未读' in fake.tips[-1],
          repr(fake.tips[-1:]))

    app._tray_tick(10.0 + 0.1)
    check('没到节拍不换图', len(fake.blinks) == 1, repr(fake.blinks))

    app._tray_tick(10.0 + 0.8)
    check('节拍到了换回来', fake.blinks == [True, False], repr(fake.blinks))

    app.unread.clear()          # 读完了
    app._tray_tick(11.0)
    check('读完立刻回常态', app._tray_on is False)
    check('不再继续闪', fake.blinks == [True, False], repr(fake.blinks))
    check('提示恢复原样',
          fake.tips[-1].startswith('pet-club') and '未读' not in fake.tips[-1],
          repr(fake.tips[-1:]))


def probe_shared_unread(app):
    print('---- D. 红点与托盘是同一份未读 ----')
    shared = {}
    w = ChatWindow(app.root, lambda text: '', me='p1',
                   is_online=lambda r: True, unread=shared)
    shared['p2'] = 3
    w.switch('p2')
    check('切过去红点清零，托盘那份也归零', shared.get('p2') == 0,
          repr(shared))
    w.on_new('p2')
    check('面板不再自己数未读（避免两边各加一次）', shared.get('p2') == 0,
          repr(shared))
    w.close()
    check('关掉面板不清未读（那是 App 的账）', 'p2' in shared, repr(shared))


def main():
    if sys.platform != 'win32':
        print('托盘只在 Windows 上存在，跳过')
        return 0
    probe_tray()
    app = App('127.0.0.1', 1)       # 不 run：不连服务器，只借用构造好的状态
    try:
        app.root.withdraw()
        probe_unread(app)
        probe_blink(app)
        probe_shared_unread(app)
    finally:
        app.tray = None
        try:
            app.root.destroy()
        except tk.TclError:
            pass
    print()
    if FAILED:
        print('%d 项失败：%s' % (len(FAILED), ', '.join(FAILED)))
        return 1
    print('全通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
