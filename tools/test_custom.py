# -*- coding: utf-8 -*-
"""自定义动作 + 远程素材同步的自测（纯逻辑，不开窗口）。

跑法：python tools/test_custom.py

覆盖三件事：
  1. 存储层：PNG 校验、发号、覆盖、删除、清单
  2. 桌面端：覆盖层加载、清单签名、缓存清理
  3. 接进 App：动作解析、右键菜单合并、聊天痕迹

真窗口 / 真网络那一层在 .tmp_e2e（浏览器）和 probe_* 里测。
"""

import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import custom, sprite_sync, sprites   # noqa: E402
from petclub.actions import ORDER                  # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def png(w, h, alpha=True):
    """手搓一张最小 PNG：8 字节签名 + IHDR。校验只读头，不需要真能解码。"""
    import struct
    import zlib
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6 if alpha else 2, 0, 0, 0)
    chunk = struct.pack('>I', len(ihdr)) + b'IHDR' + ihdr
    chunk += struct.pack('>I', zlib.crc32(b'IHDR' + ihdr) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk + b'\x00' * 8


def main():
    original = custom.DATA
    tmp = tempfile.mkdtemp(prefix='petclub-custom-')
    custom.DATA = os.path.join(tmp, 'custom')
    try:
        run()
    finally:
        custom.DATA = original
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败：' + '、'.join(FAIL))
    return 0 if not FAIL else 1


def test_shared_names():
    print('--- 同名动作：各演各的那一份 ---')
    from petclub.app import App
    from petclub.config import ROLES

    class Pet:
        def __init__(self):
            self.calls = []

        def act(self, pose, dur=None):
            self.calls.append(pose)

        def take_off(self):
            self.calls.append('launch')

        def bubble_anchor(self):
            return (0.0, 0.0)

    class Net:
        def __init__(self):
            self.sent = []

        def send(self, **kw):
            self.sent.append(kw)
            return True

    class Fake:
        # p1 有「跳舞」「比耶」，p2 有「转圈」「比耶」，p3 一个都没有
        _custom = {
            'c1': {'label': '跳舞', 'role': 'p1', 'pose': 'c1', 'dur': 2.0,
                   'custom': True},
            'c3': {'label': '比耶', 'role': 'p1', 'pose': 'c3', 'dur': 2.0,
                   'custom': True},
            'c2': {'label': '转圈', 'role': 'p2', 'pose': 'c2', 'dur': 2.0,
                   'custom': True},
            'c4': {'label': '比耶', 'role': 'p2', 'pose': 'c4', 'dur': 2.5,
                   'custom': True},
        }
        _play = App._play
        _what = App.__dict__['_what']
        _custom_like = App._custom_like
        _perform_custom = App._perform_custom
        _spec = App._spec
        _conv_key = App._conv_key
        do_action = App.do_action
        on_event = App.on_event

        def __init__(self, me):
            self.me = {'role': me}
            self.pets = {r: Pet() for r in ROLES}
            self.net = Net()
            self.remote_on = True
            self.tips = []

        def say_role(self, role, text, sec=None):
            pass

        def _log_tip(self, kind, actor, key=None, target=None, label=''):
            self.tips.append((kind, actor, label))

    def played(app):
        return {r: p.calls for r, p in app.pets.items() if p.calls}

    check('按名字找到自己的那份', App._custom_like(Fake, 'p2', '比耶')['key'] == 'c4')
    check('名字两边空白不影响配对', App._custom_like(Fake, 'p1', ' 比耶 ')['key'] == 'c3')
    check('自己没有就是 None', App._custom_like(Fake, 'p3', '比耶') is None)

    # 对全员：我做我的比耶，p2 跟着比他的，p3 没有就站着
    a = Fake('p1')
    a.do_action('c3', None)
    check('对全员：有同名动作的各演各的',
          played(a) == {'p1': ['c3'], 'p2': ['c4']}, str(played(a)))
    check('事件带上了动作名（不是各人的 key）',
          a.net.sent and a.net.sent[0].get('an') == '比耶'
          and 'to' not in a.net.sent[0], str(a.net.sent))

    # 点名 p2：我和他一起做
    b = Fake('p1')
    b.do_action('c3', 'p2')
    check('点名：我和被点的人一起做',
          played(b) == {'p1': ['c3'], 'p2': ['c4']}, str(played(b)))
    check('点名时 to 照旧', b.net.sent[0].get('to') == 'p2', str(b.net.sent))

    # 收信端（p3 的屏幕）：看得见 p1 演 c3、p2 演 c4
    c = Fake('p3')
    c.on_event({'kind': 'c3', 'from': 'p1', 'an': '比耶'})
    check('收信端：发起人演他的、在场同名的演自己的',
          played(c) == {'p1': ['c3'], 'p2': ['c4']}, str(played(c)))
    check('收信端留下一条带名字的提示',
          c.tips and c.tips[-1][2] == '比耶', str(c.tips))

    # 点到我头上但我没有这个动作：只有发起人演
    d = Fake('p3')
    d.on_event({'kind': 'c3', 'from': 'p1', 'to': 'p3', 'an': '比耶'})
    check('点名一个没有该动作的人：他不动',
          played(d) == {'p1': ['c3']}, str(played(d)))

    # 老客户端不发 an：靠清单里的 label 兜底
    e = Fake('p1')
    e.on_event({'kind': 'c2', 'from': 'p2'})
    check('没有 an 也能按清单里的名字配对',
          played(e) == {'p2': ['c2']}, str(played(e)))

    # 内置动作不受影响：mirror 照旧
    f = Fake('p1')
    f.on_event({'kind': 'wave', 'from': 'p2'})
    check('内置动作仍由发起人演、mirror 照旧',
          set(played(f)) == set(ROLES) and f.pets['p2'].calls == ['wave'],
          str(played(f)))

    print('--- 动作名不能重复 ---')
    one, err = custom.add_action('p1', png(10, 10), '比耶')
    check('第一次建这个动作没问题', one is not None and err == '', str(err))
    two, err = custom.add_action('p1', png(10, 10), '比耶')
    check('同一个人不能建两个同名动作', two is None and '已经有' in err, str(err))
    three, err = custom.add_action('p1', png(10, 10), '  比耶 ')
    check('首尾空格不算不同名字', three is None and '已经有' in err, str(err))
    four, err = custom.add_action('p1', png(10, 10), '比 耶')
    check('中间加空格是另一个名字', four is not None and err == '', str(err))
    five, err = custom.add_action('p1', png(10, 10), '打招呼')
    check('不能占用内置动作的名字', five is None and '内置' in err, str(err))
    other, err = custom.add_action('p3', png(10, 10), '比耶')
    check('别人可以有同名动作（各演各的前提）', other is not None and err == '',
          str(err))


def run():
    test_validate()
    test_store()
    test_sprites_overlay()
    test_sync_prune()
    test_app_wiring()
    test_shared_names()


def test_validate():
    print('--- PNG 校验 ---')
    check('正常 PNG 通过', custom.check_png(png(100, 200))[1] == '')
    check('非 PNG 被拒', custom.check_png(b'GIF89a' + b'0' * 40)[1] == '不是有效的 PNG')
    check('空数据被拒', custom.check_png(b'')[1] == '图片是空的')
    check('超宽被拒', '超过上限' in custom.check_png(png(400, 200))[1])
    check('超高被拒', '超过上限' in custom.check_png(png(100, 500))[1])
    check('零宽高被拒', custom.check_png(
        b'\x89PNG\r\n\x1a\n' + b'\x00' * 4 + b'IHDR' + b'\x00' * 4 + b'\x00' * 16)[1]
        == '不是有效的 PNG')
    check('路径穿越的名字不合法', not custom.valid_name('../etc'))
    check('中文名不合法（key 只发号）', not custom.valid_name('跳舞'))
    check('c1 合法', custom.valid_name('c1'))


def test_store():
    print('--- 存储层 ---')
    spec, err = custom.add_action('p1', png(100, 200), '跳舞', 3.0)
    check('新建动作成功', err == '' and spec and spec['key'] == 'c1', err)
    check('动作挂在身份上', spec['role'] == 'p1')
    check('姿势名 = key', spec['pose'] == 'c1')
    check('不镜像（别人没这张图）', 'mirror' not in spec)
    check('图片落盘', os.path.exists(custom.image_path('p1', 'c1')))
    check('rev 是 8 位', len(custom.rev('p1', 'c1')) == 8)

    check('空名字被拒', custom.add_action('p1', png(10, 10), '  ')[1] == '动作名不能为空')
    check('时长被夹到 8 秒',
          custom.add_action('p1', png(10, 10), '久', 99)[0]['dur'] == 8.0)
    check('时长被抬到 0.6 秒',
          custom.add_action('p1', png(10, 10), '短', 0)[0]['dur'] == 0.6)

    before = custom.rev('p1', 'wave')
    out, err = custom.replace_pose('p1', 'wave', png(80, 210))
    check('换图成功', err == '' and out['pose'] == 'wave', err)
    check('rev 变了', custom.rev('p1', 'wave') != before)

    m = custom.manifest()
    check('清单有动作', 'c1' in m['actions'])
    check('清单有覆盖图', m['poses']['p1']['wave'] == custom.rev('p1', 'wave'))
    check('清单不含不存在的角色', set(m['poses']) <= {'p1'})

    check('恢复默认删掉覆盖图', custom.restore_pose('p1', 'wave')
          and not os.path.exists(custom.image_path('p1', 'wave')))
    check('恢复后清单里没有了', 'wave' not in custom.manifest()['poses'].get('p1', {}))

    check('删动作成功', custom.remove_action('c1'))
    check('删完注册表也空了', 'c1' not in custom.manifest()['actions'])
    check('删完图片也没了', not os.path.exists(custom.image_path('p1', 'c1')))
    check('删不存在的返回 False', not custom.remove_action('nope'))


def test_sprites_overlay():
    print('--- 覆盖层加载 ---')
    overlay = custom.data_dir() + '/p1'
    os.makedirs(overlay, exist_ok=True)
    # 造两张真的能解码的图：overlay 里一个覆盖 wave、一个全新姿势
    from PIL import Image
    Image.new('RGBA', (80, 210), (255, 0, 0, 255)).save(
        os.path.join(overlay, 'wave.png'))
    Image.new('RGBA', (90, 200), (0, 255, 0, 255)).save(
        os.path.join(overlay, 'c9.png'))

    bundled = os.path.join(ROOT, 'assets', 'roles', 'p1')
    plain = sprites.Sprites(bundled)
    over = sprites.Sprites(bundled, overlay)

    check('不传覆盖层时没有 c9', 'c9' not in plain.poses)
    check('传了覆盖层就有 c9', 'c9' in over.poses)
    check('同名文件被覆盖版替换', over.poses['wave'].size == (80, 210),
          str(over.poses['wave'].size))
    check('出厂姿势一个不少',
          set(sprites.POSES) <= set(over.poses), str(sorted(over.poses)))
    # 真正的硬约束是「新图不能比现有包围盒更大」—— 那是上传时裁剪保证的
    # （web/js/upload.js 用 loadRole 的 w/h 当上限），服务端还有 200×240 兜底。
    # 换掉一张大图（wave 234 高）反而会让窗口变小，这是正常的。
    check('只有新姿势时窗口不变大',
          over.w <= max(plain.w, 200) and over.h <= max(plain.h, 240),
          '%s/%s vs %s/%s' % (over.w, over.h, plain.w, plain.h))
    check('body_w 仍取 idle', over.body_w == over.poses['idle'].width)

    check('角色缺 idle 仍然抛错',
          _raises(FileNotFoundError, sprites.Sprites, os.path.join(ROOT, 'nope')))


def _raises(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return True
    except Exception:
        return False
    return False


def test_sync_prune():
    print('--- 同步收尾 ---')
    cache = tempfile.mkdtemp(prefix='petclub-cache-')
    try:
        os.makedirs(os.path.join(cache, 'p1'), exist_ok=True)
        for name in ('c1', 'c2', 'wave'):
            with open(os.path.join(cache, 'p1', name + '.png'), 'wb') as f:
                f.write(png(10, 10))
        sprite_sync._prune(cache, {'p1': {'c1': 'x', 'wave': 'y'}})
        left = sorted(os.listdir(os.path.join(cache, 'p1')))
        check('远程删掉的图被清掉', 'c2.png' not in left, str(left))
        check('远程还留着的图没动', 'c1.png' in left and 'wave.png' in left)
    finally:
        shutil.rmtree(cache, ignore_errors=True)


def test_app_wiring():
    print('--- 接进 App ---')
    from petclub.app import App

    class Stub:
        _custom = {
            'c1': {'label': '跳舞', 'role': 'p1', 'pose': 'c1', 'dur': 3.0,
                   'custom': True, 'key': 'c1'},
            'c2': {'label': '转圈', 'role': 'p2', 'pose': 'c2', 'dur': 2.0,
                   'custom': True, 'key': 'c2'},
        }
        me = {'role': 'p1'}
        tips = []

        def _store(self, key, entry):
            self.tips.append((key, entry))

    stub = Stub()
    check('内置动作照常解析', App._spec(stub, 'wave')['label'] == '打招呼')
    check('自定义动作能解析', App._spec(stub, 'c1')['label'] == '跳舞')
    check('没有的动作返回 None', App._spec(stub, 'zzz') is None)

    class Sig:
        def connect(self, fn):
            pass

    class Act:
        def __init__(self, label):
            self.label = label
            self.triggered = Sig()

        def setToolTip(self, t):
            self.tip = t

    class Menu:
        def __init__(self):
            self.actions = []

        def addAction(self, label):
            a = Act(label)
            self.actions.append(a)
            return a

        def addSeparator(self):
            self.actions.append(Act('---'))

    menu = Menu()
    App._fill_actions(stub, menu, None)          # 右键自己 → 对全员
    App._fill_custom(stub, menu, None)
    labels = [a.label for a in menu.actions]
    builtin = __import__('petclub.actions', fromlist=['ACTIONS']).ACTIONS
    check('内置动作都在菜单里',
          all(builtin[k]['label'] in labels for k in ORDER), str(labels))
    check('自己的自定义动作在菜单里', '跳舞' in labels, str(labels))
    check('别人的自定义动作不在', '转圈' not in labels, str(labels))

    menu2 = Menu()
    App._fill_actions(stub, menu2, 'p1')
    App._fill_custom(stub, menu2, 'p1')
    check('右键别人时列他的动作', '跳舞' in [a.label for a in menu2.actions])

    # 菜单内容不随右键对象变：动作是「我做」的，列别人的点了也没图可演
    menu3 = Menu()
    App._fill_actions(stub, menu3, 'p2')
    App._fill_custom(stub, menu3, 'p2')
    labels3 = [a.label for a in menu3.actions]
    check('右键 p2 仍然列我自己的动作', '跳舞' in labels3, str(labels3))
    check('右键 p2 不列 p2 的动作', '转圈' not in labels3, str(labels3))

    stub.tips = []
    App._log_tip(stub, 'c1', '猥琐', 'p1')
    check('自定义动作留聊天痕迹', len(stub.tips) == 1
          and '跳舞' in stub.tips[0][1]['text'],
          str(stub.tips))

    stub.tips = []
    App._log_tip(stub, 'say', '猥琐', 'p1')      # say 不在 TIP 里
    check('没有 TIP 的内置动作仍不留痕（行为没变）', stub.tips == [])

    # ---- 素材变更的实时链路 ----
    # 桌面端原来要等 SPRITE_SYNC_SEC（60 秒）才看到别人换的图。
    sent = []

    class Net:
        def send(self, **kw):
            sent.append(kw)

    quiet = types.SimpleNamespace(
        net=Net(),
        _apply_sprites=lambda m, n: None,
        say_role=lambda r, t: None)
    App._after_sprite_post(quiet, 'p1', {'manifest': {}}, '动作加好了')
    check('传完素材会通知全房',
          any(m.get('t') == 'sprites' for m in sent), str(sent))

    stub2 = types.SimpleNamespace(_sprite_next=999.0)
    App.on_msg(stub2, {'t': 'sprites'})
    check('收到别人的变更通知就立刻同步',
          stub2._sprite_next == 0.0, str(stub2._sprite_next))

    test_self_menu_target()


def test_self_menu_target():
    """右键自己点自定义动作，发出去的目标必须是「全员」。

    曾经这里传的是自己的角色名，被 do_action 当成「只发给我自己」：
    本机照样演（do_action 自己会演一遍），别人那边一条消息都收不到 ——
    表现就是「桌面端下发指令，网页端没反应，反过来却正常」。
    这条只测「谁拿到这条指令」，不碰网络和窗口之外的任何东西。
    """
    print('--- 右键自己：自定义动作发给谁 ---')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from petclub.app import App

    qapp = QApplication.instance() or QApplication([])

    class Stub:
        me = {'role': 'p1'}
        offline = False
        _custom = {
            'c1': {'label': '跳舞', 'role': 'p1', 'pose': 'c1', 'dur': 3.0,
                   'custom': True, 'key': 'c1'},
        }
        calls = []

        # 菜单的填充逻辑用真的：这条要验的正是「真菜单点下去传给 do_action 的 to」
        _fill_actions = App._fill_actions
        _fill_custom = App._fill_custom
        _toy_actions = App._toy_actions

        def do_action(self, key, to=None):
            self.calls.append((key, to))

        def do_leave(self):
            pass

        def quit(self):
            pass

        def say_role(self, role, text):
            pass

    stub = Stub()
    menu = App.build_self_menu(stub)
    acts = {a.text(): a for a in menu.actions()}
    check('自己菜单里能看到我的自定义动作', '跳舞' in acts, str(list(acts)))
    if '跳舞' in acts:
        acts['跳舞'].trigger()
        check('点它是「对全员」（to=None）', stub.calls[-1] == ('c1', None),
              str(stub.calls))
    builtin = [t for t in acts if t == '打招呼']
    if builtin:
        acts['打招呼'].trigger()
        check('内置动作也是「对全员」', stub.calls[-1] == ('wave', None),
              str(stub.calls[-1:]))
    menu.close()

    # 右键别人仍然是点名：两条路不能一起改坏。
    stub2 = Stub()
    menu2 = App.build_other_menu(stub2, 'p2')
    acts2 = {a.text(): a for a in menu2.actions()}
    if '打招呼' in acts2:
        acts2['打招呼'].trigger()
        check('右键别人还是「只对他」', stub2.calls[-1] == ('wave', 'p2'),
              str(stub2.calls[-1:]))
    menu2.close()
    qapp.processEvents()


if __name__ == '__main__':
    sys.exit(main())
