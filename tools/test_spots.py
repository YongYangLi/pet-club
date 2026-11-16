# -*- coding: utf-8 -*-
"""角色位置记忆的自测（纯逻辑，不开窗口）。

跑法：python tools/test_spots.py

三件事：
  1. 比例存 / 取（换分辨率还在原来那个相对位置）
  2. 位置被占时往旁边挪，而且不挪出屏幕
  3. `_spawn` 真的用上了记住的位置 —— 这条最要紧：前面两条都只是零件，
     接线接错了照样每次弹回中间。

本机没装 PySide6 / Pillow 时用桩模块顶上，和 test_gather 同一套办法。
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class _Any(type):
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

from petclub import app as appmod                     # noqa: E402
from petclub.app import App, GROUND_MARGIN            # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  <- ' + str(detail) if detail and not ok else '')),
          flush=True)


class FakeSp:
    body_w = 80.0
    w = 100
    h = 220


class FakeSprites(dict):
    def get(self, role, default=None):
        return FakeSp()


class FakeWin:
    """Pet 只读 win_h / win_w，不碰任何 Qt 东西。"""

    def __init__(self):
        self.win_w = 100.0
        self.win_h = 220.0


class FakeApp:
    _spot_of = App._spot_of
    _remember_spot = App._remember_spot
    _floor_y = App._floor_y
    _free_x = App._free_x
    _clamp = App._clamp
    _spawn = App._spawn

    def __init__(self, prefs=None, sw=1920.0, sh=1080.0, ox=0.0, oy=0.0):
        self._prefs = dict(prefs or {})
        self.ox, self.oy = float(ox), float(oy)
        self.sw = sw
        self.sh = sh
        self.sprites = FakeSprites()
        self.pets = {}
        self.saved = []

    # 别真去写 prefs.json
    def save_prefs(self, prefs):
        self.saved.append(dict(prefs))


def patch_petwindow():
    """_spawn 会自己造窗口，测试里换成假的。"""
    real = appmod.windows.PetWindow
    appmod.windows.PetWindow = lambda app, sp: FakeWin()
    return real


def restore_petwindow(real):
    appmod.windows.PetWindow = real


def patch_save_prefs():
    real = appmod.config.save_prefs

    def fake(prefs):
        FakeApp.saved_prefs = dict(prefs)

    appmod.config.save_prefs = fake
    return real


def test_ratio():
    print('--- 位置存取 ---')
    # 新版：{x: 屏幕宽比例, up: 离地面多高（工作区高比例）}
    a = FakeApp({'spots': {'p3': {'x': 0.25, 'up': 0.1}}})
    chk('记得住上次的位置',
        App._spot_of(a, 'p3') == (0.25 * 1920, 1080 - GROUND_MARGIN - 0.1 * 1080),
        App._spot_of(a, 'p3'))
    chk('没记过的角色返回 None', App._spot_of(a, 'p1') is None)
    chk('存坏了不炸（字符串）',
        App._spot_of(FakeApp({'spots': {'p1': 'x'}}), 'p1') is None)
    chk('存坏了不炸（长度不够）',
        App._spot_of(FakeApp({'spots': {'p1': [0.1]}}), 'p1') is None)
    chk('存坏了不炸（缺 x）',
        App._spot_of(FakeApp({'spots': {'p1': {'up': 0.2}}}), 'p1') is None)
    chk('spots 不是 dict 也不炸',
        App._spot_of(FakeApp({'spots': 3}), 'p1') is None)

    # 旧格式 [x, y] 都是整屏比例：照旧读成绝对位置，但不许沉到地面以下
    b = FakeApp({'spots': {'p5': [0.25, 0.5]}})
    chk('旧格式照样读得出来', App._spot_of(b, 'p5') == (480.0, 540.0),
        App._spot_of(b, 'p5'))
    c = FakeApp({'spots': {'p5': [0.5, 1.0]}})
    chk('旧格式贴着下沿的夹到地面',
        App._spot_of(c, 'p5') == (960.0, 1080 - GROUND_MARGIN),
        App._spot_of(c, 'p5'))

    d = FakeApp()
    d.pets['p2'] = types.SimpleNamespace(x=480.0, y=1080 - GROUND_MARGIN)
    App._remember_spot(d, 'p2')
    chk('站在地面上时 up 是 0',
        d._prefs['spots']['p2'] == {'x': 0.25, 'up': 0.0}, d._prefs)

    e = FakeApp()
    e.pets['p2'] = types.SimpleNamespace(x=480.0, y=540.0)
    App._remember_spot(e, 'p2')
    chk('摆在半空记的是离地面多高',
        e._prefs['spots']['p2']['up'] == round(
            (1080 - GROUND_MARGIN - 540) / 1080, 5), e._prefs)


def sitting(x, y=900.0, body_w=100.0, h=220.0):
    """在场的一只角色：脚底 (x, y) + 身体尺寸。"""
    sp = types.SimpleNamespace(body_w=body_w, h=h)
    return types.SimpleNamespace(x=float(x), y=float(y), sp=sp)


def test_free_x():
    print('--- 位置被占了 ---')
    a = FakeApp()
    a.pets['p1'] = sitting(500.0)
    x = App._free_x(a, 'p2', 500.0, 900.0, FakeSp())
    chk('正撞上就往旁边挪', abs(x - 500.0) >= 56.0, x)
    chk('挪了但不是乱飞', abs(x - 500.0) <= 160.0, x)

    b = FakeApp()
    b.pets['p1'] = sitting(500.0)
    chk('离得远就原样不动',
        App._free_x(b, 'p2', 900.0, 900.0, FakeSp()) == 900.0)

    c = FakeApp()
    c.pets['p1'] = sitting(500.0)
    x = App._free_x(c, 'p2', -80.0, 900.0, FakeSp())
    chk('不会挪到屏幕外（左边）', x >= 12.0, x)
    x = App._free_x(c, 'p2', 5000.0, 900.0, FakeSp())
    chk('不会挪到屏幕外（右边）', x <= c.sw - 12.0, x)

    d = FakeApp()
    for i, r in enumerate(('p1', 'p2', 'p3', 'p4')):
        d.pets[r] = sitting(900.0 + i * 90.0)
    x = App._free_x(d, 'p5', 900.0, 900.0, FakeSp())
    chk('连续几个位置都被占时还能找到空位',
        all(abs(p.x - x) >= 56.0 for p in d.pets.values()), x)

    # ---- 二维判定：一上一下不挡着，就不该被推开 ----
    # 这正是用户看到的「重启后位置全变了」：三个人分三层站在右侧，
    # 旧的判定只看 x，把中间和下面那两个人一路推了 90 / 270 像素。
    e = FakeApp(sw=2560.0, sh=1380.0)
    e.pets['p2'] = sitting(2459.0, 1340.0)
    e.pets['p5'] = sitting(2445.0, 718.0)
    x = App._free_x(e, 'p6', 2447.0, 1036.0, FakeSp())
    chk('上下分层站着的人不会被推开', abs(x - 2447.0) < 1.0, x)

    f = FakeApp()
    f.pets['p1'] = sitting(500.0, 900.0)
    chk('同一层、x 又撞上，才让位',
        abs(App._free_x(f, 'p2', 500.0, 900.0, FakeSp()) - 500.0) >= 56.0)

    # 同时上场：两个人都在天上掉，y 都是负数。要按落点判，否则会叠住
    g = FakeApp()
    falling = sitting(500.0, 900.0)
    falling.y = -320.0
    falling.ground = 900.0
    falling.land_target = 900.0
    falling.airborne = True
    g.pets['p1'] = falling
    chk('还在下落的人按落点让位',
        abs(App._free_x(g, 'p2', 500.0, 900.0, FakeSp()) - 500.0) >= 56.0)

    # 落点在半空（记住的那一层）时，要按那一层判，不能当地面
    h = FakeApp()
    high = sitting(500.0, 900.0)
    high.y = -320.0
    high.ground = 900.0          # 地面
    high.land_target = 500.0     # 但这次停在半空那一层
    high.airborne = True
    h.pets['p1'] = high
    chk('落点在半空的人按那一层判（和地面上的不撞）',
        abs(App._free_x(h, 'p2', 500.0, 900.0, FakeSp()) - 500.0) < 1.0)


def test_spawn():
    print('--- 出生就用记住的位置 ---')
    real_win = patch_petwindow()
    real_save = patch_save_prefs()
    try:
        a = FakeApp({'spots': {'p3': {'x': 0.3, 'up': 0.25}}})
        App._spawn(a, 'p3')
        pet = a.pets.get('p3')
        chk('有记录的角色落在记录的位置',
            pet is not None and abs(pet.x - 0.3 * 1920) < 1
            and abs(pet.land_y - (1080 - GROUND_MARGIN - 0.25 * 1080)) < 1,
            pet and (pet.x, pet.land_y))
        chk('脚底仍是真实地面，不是记住的那一层',
            pet is not None and abs(pet.ground - (1080 - GROUND_MARGIN)) < 1,
            pet and pet.ground)

        b = FakeApp()
        App._spawn(b, 'p2')          # p2 是第 2 个身份，默认均分
        pet = b.pets.get('p2')
        chk('没记录的角色还是均分站位',
            pet is not None and abs(pet.x - 1920 * 2 / 7) < 1,
            pet and pet.x)
        chk('没记录时落在地面',
            abs(pet.ground - (1080 - GROUND_MARGIN)) < 1, pet.ground)
        chk('没记录时落点也是地面',
            abs(pet.land_y - (1080 - GROUND_MARGIN)) < 1, pet.land_y)

        c = FakeApp({'spots': {'p2': {'x': 0.5, 'up': 0.0}}})
        c.pets['p1'] = sitting(0.5 * 1920, 1080 - GROUND_MARGIN)
        App._spawn(c, 'p2')
        pet = c.pets.get('p2')
        chk('记录的位置被别人占了就自动挪开',
            abs(pet.x - 0.5 * 1920) >= 56.0, pet.x)

        d = FakeApp({'spots': {'p1': {'x': 0.1, 'up': 0.0}}})
        d.pets['p1'] = sitting(0.1 * 1920, 1080 - GROUND_MARGIN)
        App._remember_spot(d, 'p1')
        chk('松手后回写，再读出来一致',
            App._spot_of(d, 'p1') == (0.1 * 1920, 1080 - GROUND_MARGIN),
            d._prefs)
    finally:
        restore_petwindow(real_win)
        appmod.config.save_prefs = real_save


def test_origin_and_bottom():
    """工作区原点 / 底部边距：位置存进去再取出来要一模一样。"""
    print('--- 任务栏在左边 / 画面贴底 ---')
    real = patch_petwindow()
    floor = 40 + 1080 - GROUND_MARGIN
    try:
        a = FakeApp(sw=1920.0, sh=1080.0, ox=120.0, oy=40.0)
        App._spawn(a, 'p2')
        a.pets['p2'].x = 120.0 + 0.3 * 1920
        a.pets['p2'].y = 40.0 + 0.5 * 1080
        App._remember_spot(a, 'p2')
        got = App._spot_of(a, 'p2')
        chk('扣掉原点再存',
            got is not None and abs(got[0] - (120 + 0.3 * 1920)) < 1
            and abs(got[1] - (40 + 0.5 * 1080)) < 1, got)

        b = FakeApp(prefs={'spots': {'p2': {'x': 0.3, 'up': 0.5}}},
                    sw=1920.0, sh=1080.0, ox=120.0, oy=40.0)
        App._spawn(b, 'p2')
        pet = b.pets['p2']
        chk('恢复时加回原点', abs(pet.x - (120 + 0.3 * 1920)) < 1, pet.x)
        chk('y 也一样（离地面半屏）',
            abs(pet.land_y - (floor - 0.5 * 1080)) < 1, pet.land_y)
        chk('脚底还是在工作区下沿',
            abs(pet.ground - floor) < 1, pet.ground)

        c = FakeApp(prefs={'spots': {'p1': [0.5, 1.0]}})
        App._spawn(c, 'p1')
        chk('比例贴着 1.0 也不会沉到屏幕下沿',
            c.pets['p1'].land_y <= 1080 - GROUND_MARGIN + 0.5,
            c.pets['p1'].land_y)
        chk('旧数据的脚底仍是地面',
            abs(c.pets['p1'].ground - (1080 - GROUND_MARGIN)) < 1,
            c.pets['p1'].ground)
    finally:
        restore_petwindow(real)


def test_floor_moves():
    """地面变了（工作区高度不同 / 输入框变高）：站在地面上的人跟着走。"""
    print('--- 换个工作区高度还在同一层 ---')
    real = patch_petwindow()
    try:
        # 摆在半空（离地面 300/1080）的人：工作区从 1080 变成 900，
        # up 是「工作区高的比例」，于是仍然是同一层（离地面 250）。
        a = FakeApp(prefs={'spots': {'p2': {'x': 0.5, 'up': 300 / 1080}}})
        App._spawn(a, 'p2')
        chk('1080 高时离地面 300',
            abs((1080 - GROUND_MARGIN) - a.pets['p2'].land_y - 300) < 1,
            a.pets['p2'].land_y)

        b = FakeApp(prefs={'spots': {'p2': {'x': 0.5, 'up': 300 / 1080}}},
                    sh=900.0)
        App._spawn(b, 'p2')
        chk('900 高时还落在同一层（按工作区高的比例）',
            abs((900 - GROUND_MARGIN) - b.pets['p2'].land_y - 300 * 900 / 1080) < 1,
            b.pets['p2'].land_y)

        # 站在地面上的人（up=0）换了高度必须贴新地面，不能悬着
        c = FakeApp(prefs={'spots': {'p2': {'x': 0.5, 'up': 0.0}}}, sh=900.0)
        App._spawn(c, 'p2')
        chk('站在地面上的人换了高度仍贴着新地面',
            abs(c.pets['p2'].land_y - (900 - GROUND_MARGIN)) < 1,
            c.pets['p2'].land_y)

        # 半空的人拖得下去：上限是地面，不是他站的那一层
        pet = c.pets['p2']
        _, y = App._clamp(c, types.SimpleNamespace(win=FakeWin()), 500.0, 5000.0)
        chk('拖动上限是地面（记住的那一层封不住人）',
            abs(y - (900 - GROUND_MARGIN)) < 1, y)
        chk('那个人这次就落在新地面上',
            abs(pet.land_y - (900 - GROUND_MARGIN)) < 1, pet.land_y)
    finally:
        restore_petwindow(real)


def test_drop_and_drag():
    """换图不掉落 + 拖动丢事件时自己收尾。"""
    print('--- 掉落与拖动兜底 ---')
    seen = {}

    class FakePet:
        def __init__(self, sp, win, x, ground_y, drop=True, land_y=None):
            seen['drop'] = drop
            self.x = float(x)
            self.ground = float(ground_y)
            self.land_y = float(ground_y if land_y is None else land_y)
            self.y = self.land_y

    real_win = patch_petwindow()
    real_pet = appmod.Pet
    real_qapp = appmod.QApplication
    appmod.Pet = FakePet
    try:
        a = FakeApp({'spots': {'p2': [0.3, 0.9]}})
        App._spawn(a, 'p2')
        chk('正常上场是掉下来的', seen.get('drop') is True, seen)
        App._spawn(a, 'p1', drop=False)
        chk('换图重建不掉落', seen.get('drop') is False, seen)

        # ---- 拖动兜底：左键已经松开却没收到 mouseRelease ----
        class Buttons:
            down = False

            @classmethod
            def mouseButtons(cls):
                return 1 if cls.down else 0

        real_qt = appmod.Qt
        appmod.QApplication = Buttons
        appmod.Qt = types.SimpleNamespace(LeftButton=1)

        class Held:
            held = True
            x = 768.0
            y = 972.0
            released = False

            def release(self):
                self.released = True
                self.held = False

        b = FakeApp()
        pet = Held()
        b.pets['p2'] = pet
        b._drag = {'role': 'p2', 'moved': True, 'x0': 0, 'y0': 0,
                   'dx': 0, 'dy': 0}
        Buttons.down = True
        App._tick_drag(b)
        chk('左键还按着就不动它', not pet.released and b._drag is not None)

        Buttons.down = False
        App._tick_drag(b)
        chk('左键松了就收掉拖动', pet.released)
        chk('收掉后不再有残留的拖动态', b._drag is None)
        chk('松手处被记下来', b._prefs.get('spots', {}).get('p2') is not None,
            b._prefs)

        # 拖到一半那只角色没了：不能抛
        c = FakeApp()
        c._drag = {'role': 'p9', 'moved': True}
        try:
            App._tick_drag(c)
            ok = True
        except Exception as e:      # noqa: BLE001
            ok = False
            c.err = e
        chk('角色已经不在也不炸', ok and c._drag is None)
    finally:
        restore_petwindow(real_win)
        appmod.Pet = real_pet
        appmod.QApplication = real_qapp
        appmod.Qt = real_qt


def main():
    test_ratio()
    test_free_x()
    test_spawn()
    test_origin_and_bottom()
    test_floor_moves()
    test_drop_and_drag()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败：' + '、'.join(FAIL))
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
