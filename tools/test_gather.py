# -*- coding: utf-8 -*-
"""「集合」指令的自测（纯逻辑，不开窗口）。

跑法：python tools/test_gather.py

四层：指令表有没有这条、靠拢落点算得对不对、Pet 的两帧爬行能不能收敛到位，
最后用假角色把 do_action / on_event 的收发路径跑一遍，确认
「广播 = 一屋子人爬过来、点名 = 只有那一位」在发起方和收信方都成立。

本机没装 PySide6 / Pillow 时用桩模块顶上：这里只借 App 的几个方法，
一个窗口都不造。装了真依赖就直接用真的。
"""
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class _Any(type):
    """桩模块里随便取什么名字都能当类型 / 常量用。"""

    def __getattr__(cls, name):
        return _Dummy


class _Dummy(metaclass=_Any):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Dummy()

    def __or__(self, other):
        return self

    def __ror__(self, other):
        return self


def _stub(name):
    m = types.ModuleType(name)
    m.__getattr__ = lambda attr: _Dummy
    return m


try:
    import PySide6.QtWidgets  # noqa: F401
except ImportError:
    for n in ('PySide6', 'PySide6.QtCore', 'PySide6.QtGui',
              'PySide6.QtWidgets'):
        sys.modules.setdefault(n, _stub(n))
try:
    from PIL import Image  # noqa: F401
except ImportError:
    sys.modules.setdefault('PIL', _stub('PIL'))
    sys.modules.setdefault('PIL.Image', _stub('PIL.Image'))

from petclub import actions as A                       # noqa: E402
from petclub.actors import Pet, gather_targets           # noqa: E402
from petclub.app import App                             # noqa: E402
from petclub.config import ROLES                        # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  <- ' + str(detail) if detail and not ok else '')),
          flush=True)


class FakeWin:
    win_w = 180.0
    win_h = 220.0
    body_w = 120.0             # 站着时人的宽度（集合间距按它算，不按窗口宽）

    def __init__(self):
        self.poses = []

    def show(self, pose, x, y):
        self.poses.append(pose)

    def destroy(self):
        pass


class FakePet:
    """只记调用，不造窗口：够 _gather / do_action / on_event 用。"""

    def __init__(self, x, w=180.0):
        self.x = float(x)
        self.y = 900.0
        self.win = types.SimpleNamespace(win_w=w, win_h=220.0,
                                         body_w=w * 2 / 3.0)
        self.body_w = self.win.body_w
        self.pose = 'idle'
        self.calls = []

    def act(self, pose, dur):
        self.pose = pose
        self.calls.append(('act', pose))

    def take_off(self):
        self.calls.append(('launch',))

    def start_crawl(self, x, y=None):
        self.calls.append(('crawl', x, y))

    def bubble_anchor(self):
        return self.x, 100.0


class FakeNet:
    def __init__(self):
        self.sent = []

    def send(self, **kw):
        self.sent.append(kw)
        return True


class FakeApp:
    # 借真方法、配假成员：路由逻辑一行不改就跑得起来
    _play = App._play
    _what = App.__dict__['_what']
    _gather = App._gather
    do_action = App.do_action
    on_event = App.on_event
    _conv_key = App._conv_key

    def __init__(self, me='p1'):
        self.me = {'role': me}
        self.sw = 1920.0
        # 托盘里那个「同步远程互动」开关：这里是纯路由测试，按默认（开着）
        self.remote_on = True
        # 站位按 ROLES 均分：加角色不用改这里，也保证每只都有自己的 x
        self.pets = {r: FakePet(1920.0 * (i + 1) / (len(ROLES) + 1))
                     for i, r in enumerate(ROLES)}
        self.net = FakeNet()
        self.bubbles = []
        self.tips = []

    def say_role(self, role, text):
        self.bubbles.append((role, text))

    def _log_tip(self, kind, actor, key, target=None):
        self.tips.append((kind, actor, key, target))


def crawled(app):
    return {r for r, p in app.pets.items()
            if any(c[0] == 'crawl' for c in p.calls)}


def test_catalog():
    print('--- 指令表 ---')
    spec = A.get('gather')
    chk('集合在指令表里，排在菜单最后', spec is not None
        and A.ORDER[-1] == 'gather' and A.CHAT_ORDER[-1] == 'gather',
        (A.ORDER[-1], A.CHAT_ORDER[-1]))
    chk('集合走位移而不是 mirror 姿势',
        spec['gather'] is True and spec['mirror'] is None
        and spec['pose'] == 'wave', spec)
    chk('集合有 label / hint',
        bool(spec['label']) and bool(spec['hint']))
    views = [A.tip_text('gather', '你', one=True, target='胡大神'),
             A.tip_text('gather', '你'),
             A.tip_text('gather', '胡大神', one=True),
             A.tip_text('gather', '胡大神')]
    chk('集合留痕四种视角都不雷同',
        all(views) and len(set(views)) == 4, views)
    for key in ('s.gather', 's.gather.me'):
        pools = [set(A.VOICE[r].get(key) or ()) for r in ROLES]
        chk('%s 每个身份都有台词且互不重样' % key,
            all(pools) and not any(pools[i] & pools[j]
                                  for i in range(len(pools))
                                  for j in range(i + 1, len(pools))),
            [len(p) for p in pools])


def test_targets():
    print('\n--- 靠拢落点 ---')
    w = [100.0] * 4                      # 四个人一样宽，方便按中心核对
    t = gather_targets(960, [100, 300, 1500, 1800], w, 100.0, 20.0, 50, 1870)
    chk('左右分组、离得最近的站最里',
        t == [720.0, 840.0, 1080.0, 1200.0], t)
    chk('没人叠在发起人或彼此身上',
        len(set(t)) == len(t) and all(x != 960 for x in t))
    chk('落点都在屏幕内', all(50 <= x <= 1870 for x in t))
    chk('间距是「身体之间的空隙」：中心距 = 两人半宽 + 空隙',
        sorted(t + [960.0]) == [720.0, 840.0, 960.0, 1080.0, 1200.0],
        sorted(t + [960.0]))
    chk('相邻两人（含发起人）的空隙处处相等',
        len({round(b - a, 6) for a, b in
             zip(sorted(t + [960.0]), sorted(t + [960.0])[1:])}) == 1,
        sorted(t + [960.0]))
    chk('没人参与时返回空表',
        gather_targets(960, [], [], 100.0, 20.0, 50, 1870) == [])
    chk('只有一人时也是隔一个空隙',
        gather_targets(960, [1500], [100.0], 100.0, 20.0, 50, 1870) == [1080.0])
    one_side = gather_targets(960, [1200, 1300, 1400], [100.0] * 3, 100.0,
                              20.0, 50, 1870)
    chk('全在同一侧也不重叠，而且逐格排开',
        one_side == [1080.0, 1200.0, 1320.0], one_side)
    edge = gather_targets(120, [10, 20, 30, 40], [100.0] * 4, 100.0,
                          20.0, 50, 1870)
    chk('发起人贴屏幕边：全部换到另一侧，空隙仍旧一样',
        edge == [240.0, 360.0, 480.0, 600.0], edge)

    # 屏幕比队伍窄：空隙等比收窄，但**仍然人人相等**（以前会挤成一摞）
    wide = [200.0] * 8
    many = gather_targets(960, [960 + 300 * i for i in range(1, 9)],
                          wide, 200.0, 20.0, 100, 1820)
    seq = sorted(many + [960.0])
    gaps = {round(b - a, 6) for a, b in zip(seq, seq[1:])}
    chk('一侧挤不下：换边 + 等比收窄，空隙仍然处处相等',
        len(gaps) == 1 and all(100 <= x <= 1820 for x in many),
        (many, gaps))
    chk('收窄后的空隙比原来小', gaps.pop() < 220, gaps)

    # 胖瘦不同：中心距不一样，但**身体之间的空隙**一样
    mixed = [80.0, 120.0, 80.0, 120.0]
    m = gather_targets(960, [100, 300, 1500, 1800], mixed, 100.0,
                       20.0, 50, 1870)
    edges = []
    order = sorted(range(5), key=lambda i: (m + [960.0])[i])
    xs_all = m + [960.0]
    ws_all = mixed + [100.0]
    for a, b in zip(order, order[1:]):
        edges.append(round(xs_all[b] - xs_all[a]
                           - (ws_all[a] + ws_all[b]) / 2.0, 6))
    chk('宽窄不一时，看上去的空隙依然相等', len(set(edges)) == 1, (m, edges))


def _step(pet, frames=600):
    """按 1/60 秒步进，返回 (帧数, 见过的姿势, x 轨迹)。"""
    poses, xs = set(), []
    for i in range(frames):
        pet.update(1.0 / 60.0)
        poses.add(pet.pose)
        xs.append(pet.x)
        if pet.phase == 'idle' and i > 0:
            break
    return i + 1, poses, xs


def test_crawl():
    print('\n--- 爬行物理 ---')
    w = FakeWin()
    p = Pet(None, w, 1000.0, 900.0, drop=False)
    p.start_crawl(400.0)
    chk('开始爬：phase=crawl、第一帧 crawl_1',
        p.crawling and p.phase == 'crawl' and p.pose == 'crawl_1')
    frames, poses, xs = _step(p)
    chk('两帧交替出现过', {'crawl_1', 'crawl_2'} <= poses, sorted(poses))
    chk('一路朝目标靠近，不会走反',
        all(b <= a + 1e-9 for a, b in zip(xs, xs[1:])))
    chk('到位精准停下并站回 idle',
        p.phase == 'idle' and not p.crawling
        and p.pose == 'idle' and abs(p.x - 400.0) < 1e-6,
        (frames, p.x, p.phase, p.pose))
    chk('速度按身位走，没有一帧瞬移',
        all(abs(a - b) <= 180.0 * Pet.CRAWL_BODY_PER_SEC / 60.0 + 1e-6
            for a, b in zip([1000.0] + xs[:-1], xs)))
    chk('每帧都把位置交给窗口', len(w.poses) == frames)

    q = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    q.start_crawl(400.0)
    _step(q, 10)
    q.act('wave', 1.6)
    at = q.x
    _step(q, 60)
    chk('后一个动作顶掉爬行，人不再自己跑',
        not q.crawling and q.pose == 'wave' and abs(q.x - at) < 1e-6,
        (q.phase, q.pose, q.x))

    r = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    r.start_crawl(400.0)
    r.grab()
    r.release()
    _step(r, 120)
    chk('鼠标一抓就取消爬行（含还没落地时排的队）',
        not r.crawling and abs(r.x - 1000.0) < 1e-6, r.x)

    s = Pet(None, FakeWin(), 1000.0, 900.0, drop=True)
    s.start_crawl(400.0)          # 正在从天上掉下来：先排队
    chk('空中只排队，不抢物理', s.phase == 'fall' and not s.crawling)
    frames, poses, _ = _step(s)
    chk('落地后自己出发，照样爬到集合点',
        abs(s.x - 400.0) < 1e-6 and s.phase == 'idle'
        and 'crawl_1' in poses, (frames, s.x, s.phase))

    u = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    u.start_crawl(1000.0)
    chk('本来就在旁边就不爬', not u.crawling and u.pose == 'idle')

    v = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    v.start_crawl(400.0)
    _step(v, 5)
    v.take_off()
    at = v.x
    _step(v, 600)
    chk('起飞也顶掉爬行，落地不会又跑去集合',
        v.phase == 'idle' and abs(v.x - at) < 1e-6, (v.phase, v.x))

    print('\n--- 自由摆放 / 二维逗猫棒 ---')
    d = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    d.grab()
    d.move_to(700.0, 420.0)
    d.release()
    _step(d, 20)
    chk('松手停在原地，不再自由落体',
        d.phase == 'idle' and abs(d.x - 700.0) < 1e-6
        and abs(d.y - 420.0) < 1e-6, (d.phase, d.x, d.y))

    c = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    c.chase(400.0, 500.0)
    _step(c, 600)
    chk('逗猫棒横竖都能追到光标',
        c.phase == 'idle' and abs(c.x - 400.0) < 1e-6
        and abs(c.y - 500.0) < 1e-6, (c.x, c.y))

    e = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    e.grab()
    e.move_to(900.0, 500.0)
    e.release()
    e.start_crawl(300.0)
    _step(e, 600)
    chk('不传高度时保持原来那一层（旧的横向调用）',
        abs(e.x - 300.0) < 1e-6 and abs(e.y - 500.0) < 1e-6, (e.x, e.y))

    f = Pet(None, FakeWin(), 800.0, 900.0, drop=False)
    f.grab()
    f.move_to(800.0, 420.0)
    f.release()
    f.take_off()
    _step(f, 600)
    chk('起飞后落回起飞前的高度，不掉回地面',
        f.phase == 'idle' and abs(f.y - 420.0) < 1e-6, (f.phase, f.y))

    g = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
    g.start_crawl(400.0, 520.0)
    _step(g, 600)
    chk('集合会连高度一起爬（人放在任意位置也能靠拢）',
        abs(g.x - 400.0) < 1e-6 and abs(g.y - 520.0) < 1e-6, (g.x, g.y))

    # App 这一层也要把光标的 y 传下去，否则「上下跟手」只是 Pet 自己的能力
    import petclub.app as appmod
    real_cursor = appmod.cursor_pos
    appmod.cursor_pos = lambda: (300, 500)
    try:
        toy = Pet(None, FakeWin(), 1000.0, 900.0, drop=False)
        fake = types.SimpleNamespace(
            pets={'p1': toy}, sw=1920.0, sh=1080.0, _toy_role='p1',
            _toy_until=time.monotonic() + 5.0, _toy_hop=0.0)
        appmod.App._tick_toy(fake, time.monotonic())
        _step(toy, 600)
        chk('逗猫棒在 App 层横竖都朝鼠标走',
            abs(toy.x - 300.0) < 1.0 and abs(toy.y - 500.0) < 1.0,
            (toy.x, toy.y))
    finally:
        appmod.cursor_pos = real_cursor


def test_routing():
    print('\n--- 发起 / 接收 ---')
    a = FakeApp()
    a.do_action('gather', None)
    chk('广播：event 不带 to',
        a.net.sent == [{'t': 'event', 'kind': 'gather', 'text': ''}],
        a.net.sent)
    chk('广播：除发起者外全员爬过来',
        crawled(a) == set(ROLES) - {'p1'}, crawled(a))
    chk('广播：发起者招手、说话、留一条痕',
        ('act', 'wave') in a.pets['p1'].calls
        and any(r == 'p1' for r, _ in a.bubbles)
        and len(a.tips) == 1 and a.tips[0][0] == 'gather')
    targets = [c[1] for r in ROLES[1:] for c in a.pets[r].calls
               if c[0] == 'crawl']
    chk('广播：落点互不重叠且都在屏内',
        len(set(targets)) == len(targets)
        and all(90 <= x <= 1830 for x in targets), targets)

    b = FakeApp()
    b.do_action('gather', 'p3')
    chk('点名：event 带 to，只有那一位爬',
        b.net.sent[-1]['to'] == 'p3' and crawled(b) == {'p3'},
        (b.net.sent, crawled(b)))
    chk('点名：发起者仍招手', ('act', 'wave') in b.pets['p1'].calls)

    c = FakeApp(me='p1')
    c.on_event({'t': 'event', 'kind': 'gather', 'from': 'p2',
                'text': '', 'to': None})
    chk('收到广播：除发起人外全员向 p2 靠拢',
        crawled(c) == set(ROLES) - {'p2'}, crawled(c))
    chk('收到广播：p2 招手，我用自己口吻回一句',
        ('act', 'wave') in c.pets['p2'].calls
        and any(r == 'p1' and t in A.VOICE['p1']['s.gather.me']
                for r, t in c.bubbles))

    d = FakeApp(me='p1')
    d.on_event({'t': 'event', 'kind': 'gather', 'from': 'p2',
                'text': '', 'to': 'p1'})
    chk('收到点名：只有我自己爬过去', crawled(d) == {'p1'}, crawled(d))

    e = FakeApp(me='p1')
    e.on_event({'t': 'event', 'kind': 'gather', 'from': 'p2',
                'text': '', 'to': 'p3'})
    chk('不是给我的点名：谁也不动', crawled(e) == set()
        and e.pets['p3'].pose == 'idle', crawled(e))

    # 托盘里关了「同步远程互动」：集合也不该把一屋子人叫起来，但痕迹照记
    f = FakeApp(me='p1')
    f.remote_on = False
    f.on_event({'t': 'event', 'kind': 'gather', 'from': 'p2',
                'text': '', 'to': None})
    chk('关掉远程互动：集合不会让角色爬过来', crawled(f) == set(), crawled(f))
    chk('关掉远程互动：聊天记录里仍留一条',
        [t[0] for t in f.tips] == ['gather'], f.tips)


def main():
    test_catalog()
    test_targets()
    test_crawl()
    test_routing()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - ' + n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
