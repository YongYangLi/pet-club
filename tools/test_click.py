# -*- coding: utf-8 -*-
"""点角色这一下的自测（不开窗口）。

跑法：python tools/test_click.py

要点只有一条：**点谁谁动，但只在本机**。
自己那只和别人的那只走同一条路，区别只在「起来动动」这个提醒属于我，
不该被别人的角色消费掉。这里没有任何 event 发送，测试里也就没有 net ——
如果哪天有人顺手加了一条 send，这个文件会立刻缺属性报错。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub.actions import VOICE           # noqa: E402
from petclub.app import App                 # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  <- ' + str(detail) if detail and not ok else '')),
          flush=True)


class FakePet:
    def __init__(self, y=1040.0):
        self.held = False
        self.pose = 'idle'
        self.x = 500.0
        self.y = y          # 脚底中心的屏幕 y（Pets 里的 y 就是这个意思）
        self.acted = []

    def act(self, pose, dur=1.0, move=None):
        self.acted.append((pose, dur))
        self.pose = pose


class FakeWin:
    """Qt 窗口的两个坐标系里，点击分区只用逻辑那套（y() / height()）。"""
    role = 'p1'
    win_h = 200.0          # 物理像素高度（App 侧用）
    top = 840.0            # 窗口顶端，逻辑像素（Qt 的 win.y()）
    h = 200                # 窗口高，逻辑像素（Qt 的 win.height()）

    def __init__(self, top=None, h=None):
        if top is not None:
            self.top = top
        if h is not None:
            self.h = h

    def y(self):
        return self.top

    def height(self):
        return self.h


class Pos:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class FakeApp:
    _role_of = staticmethod(App._role_of)
    on_pet_click = App.on_pet_click
    on_pet_drop = App.on_pet_drop
    _click_zone = staticmethod(App._click_zone)

    def __init__(self, me='p1'):
        self.me = {'role': me} if me else None
        self.pets = {'p1': FakePet(), 'p2': FakePet()}
        self._taps = {}
        self._sit_due = False
        self._drag = None
        self.said = []
        self.remembered = []

    def say_role(self, role, text, sec=None):
        self.said.append((role, text))

    def _swallow_menu_click(self):
        return False

    def _remember_spot(self, role):
        self.remembered.append(role)


def test_drop_zone():
    """按下-松开（没拖动）这条路：全局 y 要换算成「窗口内比例」。

    两个坑都在这里，而且都会让三档退化成同一档：

    1. 直接拿全局 y 除以窗口高 —— 得到远大于 1 的数，全落到「脚边」。
    2. 拿物理像素的 pet.y / win_h 去减逻辑像素的鼠标 y —— 125% 缩放下差
       1.25 倍，减出来是负数，夹到 0 之后**全变成「头顶」**（用户报的
       「摸哪里都是摸头」）。

    所以专用窗口自己的 y() / height()，两个都是逻辑像素。
    """
    print('--- 点一下：全局坐标换算成头 / 肚子 / 脚 ---')

    def tap(y, role='p1', win=None):
        a = FakeApp()
        a.pets[role].y = 1040.0              # 物理脚底（不该被用到）
        win = win or FakeWin()
        win.role = role                      # 点谁就看谁的窗口
        a._drag = {'role': role, 'x0': 500, 'y0': y, 'dx': 0, 'dy': 0,
                   'moved': False}
        a.on_pet_drop(win, Pos(500, y))
        return a.pets[role].acted

    chk('点头顶 = 摸摸头', tap(860) == [('wave', 1.2)], tap(860))
    chk('点肚子 = 吓一跳', tap(940) == [('jump', 1.0)], tap(940))
    chk('点脚边 = 拍一拍', tap(1030) == [('knock', 1.2)], tap(1030))
    # 指针甩到窗口外面（人物被标签栏盖住再松开之类）：夹到两端，不越界
    chk('点在窗口上面仍算头顶', tap(100) == [('wave', 1.2)], tap(100))
    chk('点在窗口下面仍算脚边', tap(3000) == [('knock', 1.2)], tap(3000))
    chk('点别人也一样换得对', tap(860, 'p2') == [('wave', 1.2)],
        tap(860, 'p2'))

    # 125% 缩放：物理脚底 1040 / 窗口高 250，逻辑上是 832 / 200、顶端 632。
    # 三档在逻辑坐标里分开，就说明没有把两套单位混着算。
    scaled = FakeWin(top=632.0, h=200)
    chk('缩放 125% 时头顶仍是头顶',
        tap(680, win=scaled) == [('wave', 1.2)], tap(680, win=scaled))
    chk('缩放 125% 时肚子仍是肚子',
        tap(752, win=scaled) == [('jump', 1.0)], tap(752, win=scaled))
    chk('缩放 125% 时脚边仍是脚边',
        tap(820, win=scaled) == [('knock', 1.2)], tap(820, win=scaled))


def test_click_other():
    """点别人和点自己说同一套话：他自己的台词，不加括号、不换旁白。"""
    a = FakeApp()
    a.on_pet_click(0.1, 'p2')                 # 头顶
    chk('点别人的角色他也动', a.pets['p2'].acted
        and a.pets['p2'].acted[0][0] == 'wave', a.pets['p2'].acted)
    chk('点别人不会动到自己', not a.pets['p1'].acted, a.pets['p1'].acted)
    chk('点别人说的是他自己的台词（和点自己同一套）',
        len(a.said) == 1 and a.said[0][1] in VOICE['p2']['s.pet'], a.said)
    chk('台词挂在被点的那个角色头顶', [r for r, _t in a.said] == ['p2'],
        a.said)
    chk('不再套括号旁白', a.said and not a.said[0][1].startswith('（'),
        a.said)
    chk('这条路完全不碰网络', not hasattr(a, 'net'))

    a = FakeApp()
    a.on_pet_click(0.1, 'p1')
    chk('点自己照旧用自己的声音说台词', a.said and a.said[0][0] == 'p1',
        a.said)
    chk('点自己的台词也不带括号（两端同一条路）',
        a.said and not a.said[0][1].startswith('（'), a.said)

    a = FakeApp()
    a.on_pet_click(0.9, 'p2')                 # 脚边
    chk('点别人脚边是「拍一拍」', a.pets['p2'].acted[0][0] == 'knock',
        a.pets['p2'].acted)
    chk('脚边换的是「拍一拍」那句台词',
        a.said and a.said[0][1] in VOICE['p2']['s.pat'], a.said)

    a = FakeApp()
    a.pets['p2'].pose = 'sleep'
    a.on_pet_click(0.1, 'p2')
    chk('睡着的别人也能被戳醒', a.pets['p2'].acted[0][0] == 'jump',
        a.pets['p2'].acted)
    chk('戳醒说的是「叫醒」那句',
        a.said and a.said[0][1] in VOICE['p2']['s.wake'], a.said)


def test_sit_due_only_mine():
    a = FakeApp()
    a._sit_due = True
    a.on_pet_click(0.1, 'p2')
    chk('「起来动动」不会被别人的角色吃掉',
        a._sit_due is True and a.pets['p2'].acted[0][0] == 'wave',
        (a._sit_due, a.pets['p2'].acted))

    a = FakeApp()
    a._sit_due = True
    a.on_pet_click(0.1, 'p1')
    chk('点自己才消费提醒',
        a._sit_due is False and a.pets['p1'].acted[0][0] == 'jump',
        (a._sit_due, a.pets['p1'].acted))


def test_triple_tap_other():
    a = FakeApp()
    for _ in range(3):
        a.on_pet_click(0.1, 'p2')
    pool = tuple(VOICE['p2'].get('s.pet.me') or ()) + tuple(VOICE['p2']['s.wake'])
    chk('连点别人三下也会不耐烦（动 + 一句回嘴）',
        a.pets['p2'].acted[-1][0] == 'jump'
        and [t for _r, t in a.said][-1] in pool,
        (a.pets['p2'].acted, a.said))
    chk('连点记录按角色分开',
        a._taps.get('p2') == [] and 'p1' not in a._taps, a._taps)


def test_drop_routes_any_role():
    """按下松开（没拖动）这一下必须交给 on_pet_click，不管点的是谁。"""
    for role in ('p1', 'p2'):
        a = FakeApp()
        win = FakeWin()
        win.role = role
        a._drag = {'role': role, 'x0': 0, 'y0': 0, 'dx': 0, 'dy': 0,
                   'moved': False}
        a.on_pet_drop(win, Pos(10, 40))
        chk('点 %s 有反应' % role, bool(a.pets[role].acted), a.pets[role].acted)
        chk('点 %s 不会记位置' % role, a.remembered == [], a.remembered)


def main():
    test_click_other()
    test_drop_zone()
    test_sit_due_only_mine()
    test_triple_tap_other()
    test_drop_routes_any_role()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败：' + '、'.join(FAIL))
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
