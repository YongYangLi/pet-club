# -*- coding: utf-8 -*-
"""身份状态与角色上下场的自测（纯逻辑，不开窗口）。

跑法：python tools/test_state.py

针对的是「快速刷新 / 换身份之后，桌面端的人物偶尔消失且不再出现」：

  1. 服务端在连接建立的那一刻会先推一条 state，那时 join 还没处理 ——
     这条不能被当成「我的身份被释放了」。
  2. 过了宽限仍然没有我，也要先向服务端复查一次再下结论。
  3. 真的被释放时才清场、弹认领窗。
  4. 无论从哪条路径漏掉了角色，`_heal_pets` 一秒内都要补回来。
  5. 换贴图失败不能把人清掉（旧的 `reload_sprites` 是先清后加载）。
"""

import os
import sys
import time
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
    def __init__(self):
        self.win_w = 100.0
        self.win_h = 220.0


class FakePet:
    def __init__(self, sp, win, x, y, drop=True, land_y=None):
        self.x = float(x)
        self.y = float(y)
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class FakeNet:
    def __init__(self):
        self.sent = []

    def send(self, **kw):
        self.sent.append(kw)
        return True


class FakeApp(appmod.App):
    """只装状态机需要的那几个字段，Qt 那套全部绕开。"""

    def __init__(self, me=None, players=None):
        self.me = {'role': me} if me else None
        self.players = dict(players or {})
        self.pets = {}
        self.sprites = FakeSprites()
        self.ox, self.oy = 0.0, 0.0
        self.sw, self.sh = 1920.0, 1080.0
        self._prefs = {}
        self._me_since = time.monotonic()
        self._orphan_probe = False
        self._heal_at = 0.0
        self._state_seen = False
        self._claim_dlg = None
        self._claim_open = False
        self._pending_role = None
        self._pending_sent = False
        self.chat = None
        self.net = FakeNet()
        self.claims = []

    def _claim_soon(self, note=''):
        self.claims.append(note)

    def _clear_pets(self):
        for p in self.pets.values():
            p.destroy()
        self.pets.clear()


def patch_pet():
    real_win = appmod.windows.PetWindow
    real_pet = appmod.Pet
    appmod.windows.PetWindow = lambda app, sp: FakeWin()
    appmod.Pet = FakePet
    return real_win, real_pet


def restore_pet(real):
    appmod.windows.PetWindow, appmod.Pet = real


def test_stale_state():
    print('--- 刚认领时那条「谁都不在」的 state ---')
    real = patch_pet()
    try:
        a = FakeApp()
        appmod.App.on_joined(a, {'role': 'p1'})
        chk('认领后角色上场', 'p1' in a.pets, list(a.pets))
        chk('认领时刻被记下来', a._me_since > 0)

        # 服务端在连接建立时推的那条：players 里没有我
        appmod.App.on_state(a, {'t': 'state', 'players': {}})
        chk('这条旧 state 不会清掉角色', 'p1' in a.pets, list(a.pets))
        chk('不会误判成「被释放」', a.me == {'role': 'p1'} and not a.claims,
            (a.me, a.claims))
    finally:
        restore_pet(real)


def test_probe_then_release():
    print('--- 过了宽限才复查，复查后仍没我才释放 ---')
    real = patch_pet()
    try:
        a = FakeApp(me='p1', players={'p1': 'm1'})
        appmod.App.on_joined(a, {'role': 'p1'})
        a._me_since = time.monotonic() - 10          # 跳过宽限

        appmod.App.on_state(a, {'t': 'state', 'players': {'p2': 'm2'}})
        chk('第一次只发 query，不动角色', 'p1' in a.pets and a.me is not None,
            list(a.pets))
        chk('query 发出去了',
            any(m.get('t') == 'query' for m in a.net.sent), a.net.sent)
        chk('这条 state 先把 players 更新了', a.players == {'p2': 'm2'}, a.players)
        chk('复查期间不弹认领窗', not a.claims, a.claims)

        appmod.App.on_state(a, {'t': 'state', 'players': {'p2': 'm2'}})
        chk('复查后仍没我才释放', a.me is None and 'p1' not in a.pets,
            (a.me, list(a.pets), len(a.pets) >= 0))
        chk('释放后弹认领窗', bool(a.claims), a.claims)
        chk('释放时不留下半只角色', not a.pets, list(a.pets))
    finally:
        restore_pet(real)


def test_state_with_me_clears_probe():
    print('--- 自己回来了就把复查标记清掉 ---')
    real = patch_pet()
    try:
        a = FakeApp(me='p1', players={'p1': 'm1'})
        a._orphan_probe = True
        appmod.App.on_state(a, {'t': 'state', 'players': {'p1': 'm1'}})
        chk('收到含自己的 state 就清 probe', a._orphan_probe is False)
        chk('身份还在', a.me == {'role': 'p1'})
        chk('角色在场', 'p1' in a.pets, list(a.pets))
    finally:
        restore_pet(real)


def test_heal():
    print('--- 漏掉的角色一秒内补回来 ---')
    real = patch_pet()
    try:
        a = FakeApp(me='p1', players={'p1': 'm1', 'p3': 'm3'})
        a.pets['p1'] = FakePet(FakeSp(), FakeWin(), 100, 900)
        a._heal_at = 0.0

        appmod.App._heal_pets(a, time.monotonic())
        chk('在线却缺席的角色被补上', 'p3' in a.pets, list(a.pets))
        chk('已经在场的不重复建', len(a.pets) == 2, list(a.pets))

        # 节流：一秒内不重复跑（否则每帧都在扫）
        a.pets.pop('p3')
        appmod.App._heal_pets(a, time.monotonic())
        chk('一秒内不重复补', 'p3' not in a.pets)

        # players 里没有我自己：快照不可信，不动
        b = FakeApp(me='p1', players={'p3': 'm3'})
        b._heal_at = 0.0
        appmod.App._heal_pets(b, time.monotonic())
        chk('快照里没我时不去补别人', not b.pets, list(b.pets))
    finally:
        restore_pet(real)


def test_reload_keeps_pets():
    print('--- 换贴图不该把人弄没 ---')
    real = patch_pet()
    real_load = appmod.load_all
    try:
        a = FakeApp(me='p1', players={})     # state 还没到，快照里没有自己
        a.pets['p1'] = FakePet(FakeSp(), FakeWin(), 300, 900)

        appmod.load_all = lambda *args, **kw: FakeSprites()
        appmod.App.reload_sprites(a)
        chk('快照里没我也不会把自己清掉', 'p1' in a.pets, list(a.pets))

        appmod.load_all = lambda *args, **kw: (_ for _ in ()).throw(
            OSError('坏图'))
        appmod.App.reload_sprites(a)
        chk('读图失败时角色还在', 'p1' in a.pets, list(a.pets))
    finally:
        appmod.load_all = real_load
        restore_pet(real)


def main():
    test_stale_state()
    test_probe_then_release()
    test_state_with_me_clears_probe()
    test_heal()
    test_reload_keeps_pets()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
