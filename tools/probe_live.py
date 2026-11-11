# -*- coding: utf-8 -*-
"""真 Tk + 真分层窗口的端到端探针。

沙箱里看不到桌面，但能验证整条链路：
  连服务器 → 认领身份 → 桌面上长出自己那只 →
  别人认领时他的角色从天而降 → 收互动事件 → 他离开后角色消失 → 主循环持续不崩

跑法：python tools/probe_live.py
"""

import os
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import server as S                      # noqa: E402
from test_server import C               # noqa: E402
from petclub import actions as A         # noqa: E402
from petclub import config, media, petwin, update      # noqa: E402
from petclub.app import App, endpoint, OFFLINE_NOTE, tray_wanted    # noqa: E402
from petclub.chat import CONV_ALL     # noqa: E402
from petclub.config import files_dir as config_files_dir   # noqa: E402
from petclub.surface import work_area    # noqa: E402
from petclub.ui import CARD_W, EDGE_W, PAD_X, ClaimDialog   # noqa: E402

RESULTS = []


class Ev:
    """假鼠标事件 —— 拖动逻辑只用到 x_root / y_root。"""

    def __init__(self, x, y):
        self.x_root = int(x)
        self.y_root = int(y)


def chk(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    # detail 可能是列表/元组（打印出来更好认），但拼字符串只认 str
    if detail and not isinstance(detail, str):
        detail = repr(detail)
    print('%s %s%s' % ('[ok]  ' if cond else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not cond) else ''), flush=True)


def main():
    S.HEARTBEAT_TIMEOUT = 30.0
    S.REAP_INTERVAL = 0.5
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=S.reaper_loop, daemon=True).start()

    errors = []
    app = App('127.0.0.1', port)
    app.root.report_callback_exception = lambda *a: errors.append(a)

    # 认领窗是模态的，探针里不能真弹（会卡住 update 循环）：换成自动选 p1 的桩。
    claims = []

    def fake_claim():
        app._claim_open = False
        claims.append('p1')
        app._pending_role = 'p1'
        app._pending_sent = False

    app._open_claim = fake_claim
    app.begin = lambda note='': chk('正常流程不该重开服务器框', False, note)

    def unstick():
        """探针跑在真桌面上：真实鼠标可能正按在某个角色身上。

        被抓着的角色不接任何动作，一次真实点击就能让后面几条断言
        莫名其妙地看到 drag 姿势（不是代码坏了，是那只手插了一脚）。
        所以每一帧把还在「手里」的角色放掉，让探针回到干净状态。
        """
        if not app.pets or not any(p.held for p in app.pets.values()):
            return
        app._drag = None
        for p in app.pets.values():
            if p.held:
                p.release()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.root.update()
            unstick()
            time.sleep(0.02)

    app.set_server('127.0.0.1', port)
    app._schedule()

    # ---- 1. 认领身份 ----
    end = time.time() + 8
    while time.time() < end and app.me is None:
        app.root.update()
        time.sleep(0.02)
    chk('客户端成功认领身份', app.me == {'role': 'p1'}, repr(app.me))
    chk('自己的角色出现在桌面上', 'p1' in app.pets)
    chk('认领走的是认领窗', claims == ['p1'], repr(claims))
    chk('素材里确实有下落姿势（不是拿 idle 顶替）',
        'fall' in app.sprites.get('p1').poses, repr(list(app.sprites.get('p1').poses)))

    # ---- 2. 自己是「从天而降」出场的 ----
    me = app.pets.get('p1')
    chk('自己也是从天上掉下来的', me is not None and me.phase == 'fall')
    if me:
        y0 = me.y
        pump(0.12)
        chk('下落过程中位置在变（真的在动）', me.y > y0,
            '%s → %s' % (y0, me.y))
    pump(1.2)
    chk('落地后转入待机', me is not None and me.landed and me.pose == 'idle',
        repr(me and (me.phase, me.pose)))

    # ---- 3. 别人认领 → 他的角色从天而降 ----
    peer = C(port)
    peer.wait('state')
    peer.send(t='join', role='p2')
    chk('队友认领 p2 成功', peer.wait('joined') is not None)
    pump(0.12)
    chk('队友的角色出现在我桌面上', 'p2' in app.pets)
    p2 = app.pets.get('p2')
    chk('队友的角色是掉下来的', p2 is not None and p2.phase == 'fall',
        repr(p2 and p2.phase))
    if p2:
        yy = p2.y
        pump(0.12)
        chk('队友的角色在下落', p2.y > yy, '%s → %s' % (yy, p2.y))
    pump(1.2)
    chk('队友的角色落地站好', p2 is not None and p2.landed, repr(p2 and p2.phase))

    # ---- 4. 只显示已认领的身份 ----
    chk('没被认领的 p3 不会出现在桌面上', 'p3' not in app.pets, repr(list(app.pets)))
    chk('两个已认领的身份都显示着', sorted(app.pets) == ['p1', 'p2'], repr(list(app.pets)))

    # ---- 5. 互动事件作用在他的角色上 ----
    peer.send(t='event', kind='wave', text='')
    pump(0.4)
    chk('收到打招呼 → 他的角色挥手', app.pets['p2'].pose == 'wave',
        app.pets['p2'].pose)

    # ---- 6. 他离开 → 角色从桌面消失 ----
    peer.send(t='leave')
    peer.wait('left')
    pump(1.0)
    chk('队友离开后角色消失', 'p2' not in app.pets, repr(list(app.pets)))
    chk('自己的角色还在', 'p1' in app.pets)

    # ---- 7. 换人认领同一个身份 ----
    peer2 = C(port)
    peer2.wait('state')
    peer2.send(t='join', role='p2')
    chk('空出来的身份可被别人认领', peer2.wait('joined') is not None)
    pump(1.5)
    chk('新队友的角色也落到桌面上', 'p2' in app.pets and app.pets['p2'].landed)

    # ---- 8. 长跑稳定性 ----
    pump(3.0)
    chk('tick 回调全程无异常', not errors,
        repr(errors[0][1] if errors and len(errors[0]) > 1 else errors[:1]))
    chk('长跑后客户端仍存活', 'p1' in app.pets and not app._closing)

    # ---- 9. 右键谁，指令就作用在谁身上 ----
    sent = []
    real_send = app.net.send

    def spy(**kw):
        sent.append(kw)
        return real_send(**kw)

    def settle_pose():
        for p in app.pets.values():
            p.act('idle', 0.01)
        pump(0.15)

    def events():
        return [x for x in sent if x.get('t') == 'event']

    app.net.send = spy
    app.cd.reset()
    sent.clear()
    settle_pose()
    peer2.drain('event')
    app.do_action('drink', to='p2')
    # 立刻断言：探针真的会在桌面上开窗口，隔了 tick 就可能被真实的鼠标点击插一脚
    chk('对他下指令，我的角色也做', app.pets['p1'].pose == 'drink',
        app.pets['p1'].pose)
    chk('指令只发给他', events() and events()[-1].get('kind') == 'drink'
        and events()[-1].get('to') == 'p2', repr(sent))
    got = peer2.wait('event')
    chk('他真的收到了', got and got.get('kind') == 'drink'
        and got.get('from') == 'p1', repr(got))

    app.cd.reset()
    sent.clear()
    settle_pose()
    app.do_action('drink')                  # 右键自己 = 对全员
    chk('对全员下达 = 广播（不带 to）',
        events() and 'to' not in events()[-1], repr(sent))

    # 不出门的指令：作用在他身上，但他那边一点动静都没有
    app.cd.reset()
    sent.clear()
    settle_pose()
    app.do_action('sleep', to='p2')
    chk('不出门的指令：他的角色在我屏幕上睡了',
        app.pets['p2'].pose == 'sleep', app.pets['p2'].pose)
    chk('不出门的指令不影响我自己', app.pets['p1'].pose == 'idle',
        app.pets['p1'].pose)
    pump(0.2)
    chk('不出门的指令一个字节都不发', not sent, repr(sent))
    app.net.send = real_send

    # ---- 10. 收到互动：他的角色做，我的角色跟着一起做 ----
    settle_pose()
    peer2.send(t='event', kind='heart')
    pump(0.3)
    chk('收到比心 → 他的角色比心', app.pets['p2'].pose == 'heart',
        app.pets['p2'].pose)
    chk('收到比心 → 我的角色也一起比心', app.pets['p1'].pose == 'heart',
        app.pets['p1'].pose)
    chk('两边各冒一个气泡（互不覆盖）',
        'p1' in app.bubbles and 'p2' in app.bubbles, repr(list(app.bubbles)))

    # ---- 11. 单向互动不带动我 ----
    settle_pose()
    peer2.send(t='event', kind='knock')
    pump(0.3)
    chk('敲窗 → 他的角色在敲', app.pets['p2'].pose == 'knock',
        app.pets['p2'].pose)
    chk('敲窗不带动我的角色', app.pets['p1'].pose == 'idle', app.pets['p1'].pose)

    # ---- 12. 两个菜单一模一样，只有「对谁」不同 ----
    app.cd.reset()

    def labels(menu):
        return menu.labels()

    def actions_of(menu):
        """菜单里属于指令表的那几条，按出现顺序。"""
        return [x for x in labels(menu) if x in EXPECTED]

    EXPECTED = [A.get(k)['label'] for k in A.ORDER]
    app.net.send = spy
    sent.clear()
    settle_pose()

    other = app.build_other_menu('p2')
    chk('别人的菜单就是把指令表原样铺开', actions_of(other) == EXPECTED,
        repr(actions_of(other)))
    chk('别人的菜单标题说明只对他', labels(other)[0] == '胡大神 → 只对他',
        repr(labels(other)[:2]))

    mine = app.build_self_menu()
    chk('自己的菜单和别人的一模一样（界面不再区分）',
        actions_of(mine) == EXPECTED, repr(actions_of(mine)))
    chk('自己的菜单标题说明对全员',
        '全员' in labels(mine)[0], repr(labels(mine)[:2]))
    chk('没有「只有我看得到」这种分组了',
        not any('只有我看得到' in x for x in labels(mine)), repr(labels(mine)))
    chk('没有「对全员」子菜单了',
        not any('对全员' in x for x in labels(mine)), repr(labels(mine)))

    other.invoke('喝口水')
    pump(0.15)
    chk('在别人的菜单里点 = 只发给他',
        events() and events()[-1].get('kind') == 'drink'
        and events()[-1].get('to') == 'p2', repr(events()))

    app.cd.reset()
    sent.clear()
    settle_pose()
    mine.invoke('打招呼')
    pump(0.15)
    chk('在自己的菜单里点 = 广播给所有人',
        events() and events()[-1].get('kind') == 'wave'
        and 'to' not in events()[-1], repr(events()))

    app.cd.reset()
    sent.clear()
    settle_pose()
    mine.invoke('发个呆')
    pump(0.15)
    chk('在自己身上点不出门的指令 = 作用在自己身上',
        app.pets['p1'].pose == 'idle' and not events(), repr(events()))

    # ---- 14. 冷却：连点只算一次 ----
    app.net.send = spy              # 上一节末尾已经把探针撤了，这里重新挂上
    app.cd.reset()
    sent.clear()
    settle_pose()
    app.do_action('drink', to='p2')
    app.do_action('drink', to='p2')
    pump(0.15)
    ev = [s for s in sent if s.get('t') == 'event']
    chk('对同一个人同一个动作，2 秒内只发一次', len(ev) == 1, repr(ev))
    cold = app.build_other_menu('p2')
    state = dict(cold.items())
    chk('冷却中菜单直接写还要等几秒',
        '喝口水（2 秒）' in state and state['喝口水（2 秒）'] is False,
        repr(cold.items()))

    app.cd.reset()
    sent.clear()
    settle_pose()
    app.do_action('wave', to='p2')
    app.do_action('drink', to='p2')
    pump(0.15)
    ev = [s for s in sent if s.get('t') == 'event']
    chk('不同动作互不牵连（打完招呼还能喝口水）', len(ev) == 2, repr(ev))

    app.cd.reset()
    sent.clear()
    app.do_action('wave')
    app.do_action('drink')           # 换个动作也不能绕过全员冷却
    pump(0.15)
    ev = [s for s in sent if s.get('t') == 'event']
    chk('全员 5 秒内只允许一次（换动作也不行）', len(ev) == 1, repr(ev))

    app.cd.reset()
    settle_pose()
    app.do_action('jump')
    chk('不出门的指令也进冷却（防手抖连点）',
        not app.cd.ready('local:jump', time.monotonic()))

    # ---- 14b. 倒计时自己往下走（tk.Menu 做不到的那件事） ----
    # Windows 上原生菜单弹出会占住事件循环，after 一个都不触发 —— 这就是原来
    # 「（8 秒）」一动不动的原因。现在菜单是自己画的，弹着的时候照常刷新。
    app.cd.reset()
    app.cd.mark(app._cd_key('drink', 'p2'), time.monotonic(), 1.4)
    live = app.build_other_menu('p2')
    app._popup(live, Ev(240, 240))
    pump(0.3)
    first = [t for t in live.rendered() if t.startswith('喝口水')]
    chk('菜单里写着还要等几秒', first and first[0] != '喝口水', repr(first))
    chk('冷却没过的行是灰的',
        any(t.startswith('喝口水') and not on for t, on in live.items()),
        repr(live.items()))

    pump(0.7)                       # 冷却在走，菜单不该动一下才变
    later = [t for t in live.rendered() if t.startswith('喝口水')]
    chk('倒计时自己往下走（不用关掉重开）',
        bool(first) and bool(later) and later[0] != first[0],
        '%r → %r' % (first, later))

    pump(0.9)                       # 冷却彻底过去
    chk('冷却一过，同一份菜单里那一行变成可点',
        dict(live.items()).get('喝口水') is True, repr(live.items()))
    sent.clear()
    chk('也点得动了', live.invoke('喝口水'), repr(live.items()))
    pump(0.15)
    chk('点下去真的把动作发出去了',
        events() and events()[-1].get('kind') == 'drink', repr(events()))
    chk('点完菜单自己收起来', not live.is_open)

    # 菜单开着时点角色：这一下只用来收菜单，不能顺手摸一下宠物
    app.cd.reset()
    both = app.build_self_menu()
    app._popup(both, Ev(300, 300))
    pump(0.2)
    was_open = both.is_open
    app.on_pet_press('p1', Ev(300, 300))
    chk('菜单开着时点角色 = 只收菜单，不顺手摸一下',
        was_open and not both.is_open and app._drag is None,
        'open=%r menu=%r drag=%r' % (was_open, both.is_open, app._drag))

    # ---- 14d. 菜单不盖住角色 ----
    # 右键的时候鼠标正点在角色身上，菜单又高又宽：照鼠标位置弹就是把角色糊掉。
    def menu_rect(m):
        t = m.top
        return (t.winfo_rootx(), t.winfo_rooty(),
                t.winfo_rootx() + t.winfo_width(),
                t.winfo_rooty() + t.winfo_height())

    def hits(a, b):
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    def in_work_area(r):
        l, t, rr, bb = work_area()
        return r[0] >= l and r[1] >= t and r[2] <= rr and r[3] <= bb

    app.cd.reset()
    pet = app.pets['p2']
    app.on_other_menu('p2', Ev(pet.x, pet.y - 40))
    pump(0.25)
    m = app._menu
    chk('右键别人：菜单不压在角色身上',
        m is not None and not hits(menu_rect(m), app._pet_rect('p2')),
        repr(menu_rect(m) if m else None))
    chk('菜单整块留在屏幕工作区里', m is not None and in_work_area(menu_rect(m)),
        repr(menu_rect(m) if m else None))
    if m is not None:
        m.close()

    # 角色贴着右边缘：右边放不下，得自动改摆左边
    # （move_to 只在被抓起时生效，所以走一遍抓紧→挪→撒手的正常路径）
    def park(pet, x):
        pet.grab()
        pet.move_to(x, pet.ground)
        pet.release()
        pump(0.45)

    app.cd.reset()
    park(pet, app.sw - 60)
    app.on_other_menu('p2', Ev(pet.x, pet.y - 40))
    pump(0.25)
    m = app._menu
    rect = app._pet_rect('p2')
    chk('角色贴右边时，菜单改摆到左边',
        m is not None and menu_rect(m)[2] <= rect[0] and not hits(menu_rect(m), rect),
        '%r vs 角色 %r' % (menu_rect(m) if m else None, rect))
    if m is not None:
        m.close()
    park(pet, app.sw * 2.0 / 3)

    # ---- 14c. 聊天面板：一个群聊 + 每人一个单聊 ----
    app.cd.reset()
    sent.clear()
    settle_pose()
    app.do_action('say', to='p2')
    pump(0.2)
    chat = app.chat
    chk('传话打开的是一个常驻面板', chat is not None and chat.alive(), repr(chat))
    keys = [k for k, _ in chat.convs()]
    # 角色是可以加的（p5 就是这么来的），所以这里不能写死名字
    chk('会话是「群聊 + 我以外的每个人」',
        keys == [CONV_ALL] + [r for r in config.ROLES if r != chat.me],
        repr(keys))
    chk('从菜单点「胡大神」直接切到他的会话', chat.key == 'p2', repr(chat.key))
    chk('标题写着当前会话', '胡大神' in chat.title.cget('text'),
        chat.title.cget('text'))

    t = chat.top
    t.update_idletasks()
    cx = t.winfo_rootx() + t.winfo_width() // 2
    cy = t.winfo_rooty() + t.winfo_height() // 2
    chk('面板开在屏幕正中',
        abs(cx - t.winfo_screenwidth() // 2) <= 2
        and abs(cy - t.winfo_screenheight() // 2) <= 2,
        '窗口中心 (%d,%d)，屏幕 (%d,%d)'
        % (cx, cy, t.winfo_screenwidth(), t.winfo_screenheight()))

    chat.field.entry.insert(0, '喝口水')
    chat.send()
    pump(0.2)
    ev = [x for x in sent if x.get('t') == 'event']
    chk('按发送 = 发一条带文字的 say',
        bool(ev) and ev[-1].get('kind') == 'say' and ev[-1].get('text') == '喝口水'
        and ev[-1].get('to') == 'p2', repr(ev))
    chk('发完面板还开着（可以接着说话）',
        app.chat is not None and app.chat.alive())
    chk('自己说的话留在当前会话里', '喝口水' in chat.history_text('p2'),
        chat.history_text('p2'))
    chk('单聊的话不会跑进群聊', '喝口水' not in chat.history_text(CONV_ALL),
        chat.history_text(CONV_ALL))

    chat.field.entry.insert(0, '再一句')
    chat.send()
    pump(0.15)
    ev2 = [x for x in sent if x.get('t') == 'event']
    chk('冷却里第二条不发出去', len(ev2) == len(ev), repr(ev2))
    chk('但面板里说清还要等几秒', '还要等' in chat.note_text(), chat.note_text())

    app.cd.reset()
    peer2.send(t='event', kind='say', text='在呢，这就去倒', to='p1')
    pump(0.3)
    chk('对方单聊说的话落进他的会话', '这就去倒' in chat.history_text('p2'),
        chat.history_text('p2'))
    chk('单聊的消息不会串到群聊', '这就去倒' not in chat.history_text(CONV_ALL),
        chat.history_text(CONV_ALL))

    peer2.send(t='event', kind='say', text='大家好', to=None)
    pump(0.3)
    chk('群发的话进群聊', '大家好' in chat.history_text(CONV_ALL),
        chat.history_text(CONV_ALL))

    # ---- 14d. 互动痕迹：居中灰字，不是气泡 ----
    app.cd.reset()
    peer2.send(t='event', kind='knock', to='p1')
    pump(0.3)
    tips = [e for e in chat.entries('p2') if e['kind'] == 'tip']
    chk('别人敲窗在单聊里留一条提示',
        bool(tips) and '敲了敲你的窗' in tips[-1]['text'], repr(tips[-1:]))
    chk('提示和普通消息是两种东西', chat.kinds('p2')[-1] == 'tip',
        repr(chat.kinds('p2')))

    app.cd.reset()
    peer2.send(t='event', kind='heart', to=None)
    pump(0.3)
    tips = [e for e in chat.entries(CONV_ALL) if e['kind'] == 'tip']
    chk('群发的互动记进群聊',
        bool(tips) and '比了个心' in tips[-1]['text'], repr(tips[-1:]))

    app.cd.reset()
    app.do_action('pat', to='p2')
    pump(0.25)
    tips = [e for e in chat.entries('p2') if e['kind'] == 'tip']
    chk('自己点别人的动作也留痕',
        bool(tips) and tips[-1]['text'].startswith('你 ')
        and '拍了拍' in tips[-1]['text'], repr(tips[-1:]))
    tips = [e for e in chat.entries(CONV_ALL) if e['kind'] == 'tip']
    chk('自己点别人不会记到群聊里',
        all('拍了拍胡大神' not in e['text'] for e in tips), repr(tips[-2:]))

    # ---- 14e. 切会话：未读、群聊里发、对方不在线 ----
    chat.switch(CONV_ALL)
    chk('能切回群聊', chat.key == CONV_ALL, repr(chat.key))
    app.cd.reset()
    # 动作痕迹（挥手）只留一行灰字，不算「有话说」—— 否则别人点一下头，
    # 托盘就闪一下，那红点很快就不值钱了。
    peer2.send(t='event', kind='wave', to='p1')
    pump(0.3)
    chk('动作痕迹不算未读', chat.unread_of('p2') == 0, repr(chat.unread))
    peer2.send(t='event', kind='say', text='在吗', to='p1')
    pump(0.3)
    chk('不在当前会话的消息记未读', chat.unread_of('p2') >= 1, repr(chat.unread))
    chat.switch('p2')
    chk('切过去红点就清了', chat.unread_of('p2') == 0, repr(chat.unread))

    # 真的点一下左边那一行 —— 切会话是鼠标点的，不是代码叫的。
    # when='now' 是必须的：排队投递的话，中间可能来一条新消息重建了这些行，
    # 事件就落到一个已经销毁的控件上了。
    pump(0.15)          # 等新建的行真的映射出来：未映射的控件收不到鼠标事件
    chat._rows['p3'].event_generate('<Button-1>', x=10, y=10, when='now')
    chk('点左边那一行就切过去了', chat.key == 'p3', repr(chat.key))
    chat.switch('p2')

    app.cd.reset()
    chat.switch(CONV_ALL)
    chat.field.entry.insert(0, '大家晚上好')
    chat.send()
    pump(0.2)
    ev3 = [x for x in sent if x.get('t') == 'event']
    chk('群聊里发的话不带 to',
        bool(ev3) and ev3[-1].get('kind') == 'say'
        and ev3[-1].get('to') is None, repr(ev3[-1:]))
    chk('群聊的话留在群聊里', '大家晚上好' in chat.history_text(CONV_ALL),
        chat.history_text(CONV_ALL))

    app.cd.reset()
    chat.switch('p3')                    # p3 没人在
    n_before = len([x for x in sent if x.get('t') == 'event'])
    chat.field.entry.insert(0, '在吗')
    chat.send()
    pump(0.2)
    n_after = len([x for x in sent if x.get('t') == 'event'])
    chk('对不在线的人说话，当场说清发不出去', '不在线' in chat.note_text(),
        chat.note_text())
    chk('而且真的没发出去', n_after == n_before, '%d → %d' % (n_before, n_after))

    chat.close()
    chk('关掉面板', app.chat is None)
    app.cd.reset()
    app.open_chat('p2')
    pump(0.2)
    chk('重新打开，历史还在',
        '喝口水' in app.chat.history_text('p2')
        and '这就去倒' in app.chat.history_text('p2'),
        app.chat.history_text('p2'))
    chk('重开时停在刚才那个会话', app.chat.key == 'p2', repr(app.chat.key))
    app.chat.close()

    # ---- 14f. 发图片、发表情、发文件 ----
    app.cd.reset()
    app.open_chat('p2')
    pump(0.2)
    chat = app.chat
    pic = os.path.join(ROOT, 'assets', 'roles', 'p1', 'wave.png')

    app.cd.reset()
    sent.clear()
    why = app.send_media('img', pic)
    pump(0.25)
    ev = sent[-1] if sent else {}
    chk('发图片：发出去了', why == '' and ev.get('mt') == 'img', repr(why))
    chk('图片的消息类型是 img', ev.get('kind') == 'say' and bool(ev.get('img')),
        '%s 字符' % len(ev.get('img') or ''))
    chk('图片跟着会话走（单聊带 to）', ev.get('to') == 'p2', repr(ev.get('to')))
    chk('聊天记录里记成一条图片', chat.types('p2')[-1] == 'img',
        repr(chat.types('p2')[-1:]))
    chk('会话栏预览写的是「[图片]」', chat.preview_of('p2') == '[图片]',
        chat.preview_of('p2'))
    chat.render()
    pump(0.15)
    n_img = len([i for i in chat.msgs.find_all() if chat.msgs.type(i) == 'image'])
    chk('画布上真的画出了那张图', n_img >= 1, '%d 张 image' % n_img)

    app.cd.reset()
    peer2.send(t='event', kind='say', to='p1',
               mt='img', img=ev.get('img'))
    pump(0.3)
    chk('收到的图片进聊天记录', chat.types('p2')[-1] == 'img',
        repr(chat.types('p2')[-1:]))
    chk('收到的图片带原始数据', bool(chat.entries('p2')[-1].get('img')))

    app.cd.reset()
    sent.clear()
    why = app.send_media('emoji', '😄')
    pump(0.25)
    ev = sent[-1] if sent else {}
    chk('发表情：只发一个字符', ev.get('mt') == 'emoji'
        and ev.get('text') == '😄', repr(ev))
    chk('表情不带图片数据（不浪费流量）', 'img' not in ev, sorted(ev))
    chk('记录里留下那个字符', chat.entries('p2')[-1].get('char') == '😄',
        repr(chat.entries('p2')[-1].get('char')))
    chk('预览写的是「[表情]」', chat.preview_of('p2') == '[表情]',
        chat.preview_of('p2'))

    app.cd.reset()
    peer2.send(t='event', kind='say', to='p1', mt='emoji', text='👍')
    pump(0.3)
    chk('收到的表情也记下字符', chat.entries('p2')[-1].get('char') == '👍',
        repr(chat.entries('p2')[-1].get('char')))

    # 表情面板：两页，第一页是 emoji，第二页是自己的卡片表情
    chat._toggle_stickers()
    pump(0.2)
    chk('表情面板打开了', chat._pop is not None)
    groups = chat.picker_groups()
    chk('表情面板分了三组', len(groups) == 3, [g[0] for g in groups])
    chk('第一组是 emoji', groups[0][0] == '表情' and len(groups[0][1]) >= 12,
        len(groups[0][1]))
    chk('第二组是我自己的表情包', groups[1][0] == '我的'
        and len(groups[1][1]) == 8, len(groups[1][1]))
    # 他人：另外三个角色 × 8 张，而且一张都不该是我自己的
    others = groups[2][1]
    chk('第三组是其他人的表情包（每人 8 张）',
        groups[2][0] == '他人'
        and len(others) == 8 * (len(config.ROLES) - 1), len(others))
    mine_dir = os.sep.join(('stickers', chat.me, ''))
    chk('他人那页里没有我自己的卡片',
        not any(mine_dir in p for _m, p, _ph in others),
        [p.split('stickers')[-1] for _m, p, _ph in others][:3])
    chk('他人那页全是表情包（发出去是图片，不是文字）',
        all(m == 'sticker' for m, _p, _ph in others), '')
    chat._pop_closed()
    chk('点一下别处就收起来', chat._pop is None)

    # 文件：发出去、收下来、能点开
    app.cd.reset()
    sent.clear()
    src = os.path.join(ROOT, 'requirements.txt')
    why = app.send_media('file', src)
    pump(0.25)
    ev = sent[-1] if sent else {}
    chk('发文件：发出去了', why == '' and ev.get('mt') == 'file', repr(why))
    chk('文件名和大小一起发', ev.get('name') == 'requirements.txt'
        and ev.get('size') == os.path.getsize(src), (ev.get('name'),
                                                     ev.get('size')))
    chk('预览写的是「[文件] 名字」',
        chat.preview_of('p2').startswith('[文件] requirements'),
        chat.preview_of('p2'))

    app.cd.reset()
    peer2.send(t='event', kind='say', to='p1', mt='file',
               img=ev.get('img'), name='周报.xlsx', size=ev.get('size'))
    pump(0.4)
    got = chat.entries('p2')[-1]
    chk('收到的文件存到本地了',
        bool(got.get('path')) and os.path.exists(got.get('path')),
        got.get('path'))
    chk('存下来的字节和发出来的一样',
        open(got['path'], 'rb').read() == open(src, 'rb').read())
    chk('文件条目不带 base64（不占内存）', not got.get('img'))

    app.cd.reset()
    peer2.send(t='event', kind='say', to='p1', mt='file',
               img=ev.get('img'), name='../../evil.txt', size=10)
    pump(0.4)
    evil = chat.entries('p2')[-1].get('path') or ''
    chk('文件名里的路径被吃掉，不会写到别处',
        evil and os.path.dirname(os.path.abspath(evil))
        == os.path.abspath(os.path.dirname(evil)), evil)
    chk('落到附件目录里', os.path.dirname(os.path.abspath(evil)).lower()
        == os.path.abspath(config_files_dir()).lower(), evil)

    # 太大的文件当场拒绝，别发出去一半
    app.cd.reset()
    big = os.path.join(ROOT, '.tmp', 'too-big.bin')
    with open(big, 'wb') as f:
        f.write(b'\0' * (2 * 1024 * 1024 + 1))
    sent.clear()
    why = app.send_media('file', big)
    pump(0.15)
    chk('超过 2MB 的文件当场说清', '太大' in why, why)
    chk('而且一个字节都没发', not sent, len(sent))

    # Ctrl+V：剪贴板里是一张图 —— 没有文件名，只有一张在内存里的 PIL 图。
    # 这条路和「选文件发送」不一样：不用落盘，也不该逼用户先存成文件。
    from PIL import Image as PILImage
    clip = PILImage.new('RGB', (900, 600), (90, 140, 220))
    app.cd.reset()
    sent.clear()
    why = app.send_media('img', clip)
    pump(0.3)
    ev = sent[-1] if sent else {}
    chk('剪贴板里的图能直接发（没有文件名）',
        why == '' and ev.get('mt') == 'img' and bool(ev.get('img')), repr(why))
    got_im = media.decode(ev.get('img'))
    chk('内存里的图也缩过再发（900×600 不会原样塞进去）',
        got_im is not None and max(got_im.size) <= media.MAX_SIDE,
        got_im.size if got_im else None)

    # on_paste 的三条规矩：文字交回系统、图自己吃掉、一次最多三张
    real_clip = media.clipboard_items
    try:
        media.clipboard_items = lambda limit=0: []
        chk('剪贴板里是文字时，Ctrl+V 交回系统默认粘贴',
            chat.on_paste() is None, '')

        media.clipboard_items = lambda limit=0: [('img', clip)]
        app.cd.reset()
        sent.clear()
        ret = chat.on_paste()
        pump(0.3)
        chk('贴一张图：这一下自己吃掉', ret == 'break', repr(ret))
        chk('而且真的发出去了',
            any(e.get('mt') == 'img' for e in sent), len(sent))

        media.clipboard_items = lambda limit=0: [('img', clip)] * 4
        app.cd.reset()
        sent.clear()
        chat.set_note('')
        ret = chat.on_paste()
        pump(0.5)
        n = len([e for e in sent if e.get('mt') == 'img'])
        chk('一次最多贴三张', n == 3, '%d 张 / %d 条消息' % (n, len(sent)))
        chk('多出来的在提示行说一声', '最多' in chat.note_text(),
            chat.note_text())
    finally:
        media.clipboard_items = real_clip
        chat.set_note('')

    # 搜索框：按名字/内容过滤会话
    chat.search.insert(0, '周报')
    chat._on_search()
    pump(0.15)
    chk('搜索能过滤会话', [k for k, _ in chat.visible_convs()] == ['p2'],
        [k for k, _ in chat.visible_convs()])
    chat.search.delete(0, 'end')
    chat._on_search()
    chk('清空搜索就恢复全部',
        len([k for k, _ in chat.visible_convs()]) == len(config.ROLES))

    app.chat.close()

    # ---- 15. 台词按角色分 ----
    app.cd.reset()
    settle_pose()
    app.do_action('pet')
    chk('不出门的指令用的是被作用角色的口吻',
        app.bubbles.get('p1') is not None
        and app.bubbles['p1'].text in A.VOICE['p1']['l.pet'],
        repr(app.bubbles['p1'].text if 'p1' in app.bubbles else None))

    settle_pose()
    peer2.send(t='event', kind='heart')
    pump(0.35)
    chk('对方说的话是那个人的口气',
        app.bubbles.get('p2') is not None
        and app.bubbles['p2'].text in A.VOICE['p2']['s.heart'],
        repr(app.bubbles['p2'].text if 'p2' in app.bubbles else None))
    chk('我的回应是我这个角色的口气',
        app.bubbles.get('p1') is not None
        and app.bubbles['p1'].text in A.VOICE['p1']['s.heart.me'],
        repr(app.bubbles['p1'].text if 'p1' in app.bubbles else None))

    # ---- 16. 拖动：只有自己看得见的玩法 ----
    app.cd.reset()
    sent.clear()
    settle_pose()
    pet = app.pets['p2']            # 别人的角色在我屏幕上也能拖
    x0, y0 = pet.x, pet.y
    app.on_pet_press('p2', Ev(x0 + 6, y0 - 20))
    app.on_pet_drag('p2', Ev(x0 + 8, y0 - 22))
    chk('抖两像素不算拖动', not pet.held, pet.phase)
    app.on_pet_drag('p2', Ev(x0 + 90, y0 - 130))
    chk('拖过阈值就抓起来', pet.held, pet.phase)
    chk('抓起来换成 drag 姿势（素材里那套提溜着）', pet.pose == 'drag', pet.pose)
    chk('角色跟着鼠标走', pet.x > x0 + 40 and pet.y < y0 - 60,
        '%.0f,%.0f' % (pet.x, pet.y))
    app.on_pet_drag('p2', Ev(-5000, -5000))
    chk('拖不出屏幕（否则再也抓不回来）',
        pet.x >= pet.win.win_w / 2.0 and pet.y >= pet.win.win_h,
        '%.0f,%.0f' % (pet.x, pet.y))
    app.on_pet_drop('p2', Ev(pet.x, pet.y))
    chk('松手就往下掉', pet.phase == 'fall' and pet.pose == 'fall',
        repr((pet.phase, pet.pose)))
    pump(1.4)
    chk('掉回地面站好', abs(pet.y - pet.ground) < 1 and pet.pose == 'idle',
        repr((pet.y, pet.ground, pet.pose)))
    # 心跳是后台保活，每 5 秒一次，跟拖动没关系 —— 只排除它，别的一律不许有
    other = [s for s in sent if s.get('t') != 'ping']
    chk('拖动 / 掉落全程不发任何网络消息', not other, repr(other))
    chk('拖动后的位置不再回到默认站位', pet.x != x0, '%.0f vs %.0f' % (pet.x, x0))

    app.cd.reset()
    settle_pose()
    app.on_pet_press('p1', Ev(400, 300))
    app.on_pet_drop('p1', Ev(401, 301))
    chk('没拖动的单击仍然 = 摸摸头', app.pets['p1'].pose == 'jump',
        app.pets['p1'].pose)
    app.net.send = real_send

    # ---- 17. 桌面只有角色，没有角落入口 ----
    chk('右下角的三点菜单已经去掉', not hasattr(app, 'dot'))
    chk('也没有遗留的回调', not hasattr(app, 'on_dot_menu'))
    import petclub
    dock_py = os.path.join(os.path.dirname(petclub.__file__), 'dock.py')
    chk('dock 模块整个删了', not os.path.exists(dock_py), dock_py)

    # ---- 18. 地址是内置的，改不了 ----
    chk('不传参时连的是内置服务器',
        endpoint() == ('47.94.21.34', 8765), repr(endpoint()))
    chk('探针能覆盖地址（测试口子还在）',
        endpoint('127.0.0.1', 9) == ('127.0.0.1', 9))
    chk('菜单里没有「服务器设置」了',
        '服务器设置…' not in labels(app.build_self_menu()),
        repr(labels(app.build_self_menu())))

    def leaks(texts):
        return [x for x in texts
                if '47.94.21.34' in x or '8765' in x or ':' in x]

    # 菜单已经不再有子菜单（服务器成员摊平成一节），所以「所有文案」就是全部
    chk('整个菜单都不写服务器地址',
        not leaks(labels(app.build_self_menu())
                  + labels(app.build_other_menu('p2'))),
        repr(leaks(labels(app.build_self_menu()))))
    chk('离线提示里也不写地址',
        not leaks([app.offline_note()]), repr(app.offline_note()))

    app.offline = True              # 离线时菜单要说出原因（但同样不许带地址）
    off_texts = labels(app.build_self_menu())
    chk('离线时菜单会说明连不上，且照样不写地址',
        any('连不上服务器' in x for x in off_texts) and not leaks(off_texts),
        repr(off_texts))
    app.offline = False

    # ---- 10. 界面结构：认领窗 ----
    dlg = ClaimDialog(app.root, players={
        'p1': {'role': 'p1', 'name': '猥琐'}, 'p2': None, 'p3': None,
        'p4': None})
    chk('每张身份卡片都在', set(dlg.cards) == set(config.ROLES),
        repr(dlg.cards))
    chk('已被认领的卡片标为不可选', dlg.cards['p1'].taken is True)
    chk('没认领的卡片可选', dlg.cards['p2'].taken is False)
    chk('一开始不能提交', dlg.ok_btn._enabled is False)
    dlg._pick('p2')
    chk('选中后可以提交', dlg.ok_btn._enabled is True)
    dlg.ok()
    chk('认领窗返回选中的身份', dlg.result == 'p2', repr(dlg.result))
    dlg.cancel()

    dlg2 = ClaimDialog(app.root, players={
        r: {'role': r} for r in config.ROLES})
    chk('全部被认领时不可提交', dlg2.ok_btn._enabled is False)
    dlg2._pick('p1')
    chk('点已认领的卡片不会选中', getattr(dlg2, '_selected', None) is None)
    dlg2.cancel()

    # ---- 11. 认领窗开着时，占用变化要能当场反映 ----
    dlg3 = ClaimDialog(app.root, players={
        'p1': None, 'p2': None, 'p3': None, 'p4': None})
    dlg3._pick('p2')
    dlg3.set_players({'p1': None, 'p2': None, 'p3': None, 'p4': None})
    chk('状态刷新不会把已选中的清掉', dlg3._selected == 'p2' and dlg3.ok_btn._enabled)
    dlg3.set_players({'p1': None, 'p2': {'role': 'p2'}, 'p3': None, 'p4': None})
    chk('选中的身份被别人抢走 → 立刻取消选中',
        dlg3._selected is None and dlg3.ok_btn._enabled is False)
    chk('提示里说清是被抢了', '刚被别人认领' in dlg3.note.cget('text'),
        dlg3.note.cget('text'))
    dlg3.set_players({'p1': None, 'p2': None, 'p3': None, 'p4': None})
    chk('别人让位后卡片重新可选', dlg3.cards['p2'].taken is False)
    dlg3.cancel()

    # ---- 12. 认领窗的左右边距 ----
    # 卡片是 grid 并排的固定宽度，窗口若按「卡片总宽」算就会把最后一张顶到
    # 边上：末张卡贴边、左边却留着空 —— 这里量真实几何，不看算式。
    # 边距要量到窗口边缘（body 的 1px 描边 + content 的 padx），content 内部本来
    # 就是被卡片占满的，量它只会得到 0。
    dlg4 = ClaimDialog(app.root, players={
        'p1': None, 'p2': None, 'p3': None, 'p4': None})
    dlg4.top.update()
    wx, ww = dlg4.top.winfo_rootx(), dlg4.top.winfo_width()
    c1, c3 = dlg4.cards['p1'], dlg4.cards[config.ROLES[-1]]
    left = c1.winfo_rootx() - wx
    right = (wx + ww) - (c3.winfo_rootx() + c3.winfo_width())
    chk('最后一张卡没被窗口右缘切掉', c3.winfo_width() == CARD_W,
        '%d vs %d' % (c3.winfo_width(), CARD_W))
    chk('左右边距一致', abs(left - right) <= 1, 'left=%d right=%d' % (left, right))
    chk('边距就是设计里的描边 + 内边距', left == right == PAD_X + EDGE_W,
        'left=%d right=%d 期望 %d' % (left, right, PAD_X + EDGE_W))
    dlg4.cancel()

    # ---- 19. 认领窗里坐一会，不该被服务端当僵尸踢 ----
    # 服务端给每条连接设了 IDLE_TIMEOUT（90 秒不吭声就断）。客户端从前只在
    # 「已经有身份」时才发心跳 —— 还没认领的人坐在认领窗里一句话不说，
    # 90 秒后必掉线：界面跳「连不上服务器，正在重试」，重连、再掉，一直循环。
    # 而认领本身仍然能成功（重连后补发 join），所以极容易被当成网络问题。
    sent = []
    real_send = app.net.send

    def spy(**kw):
        sent.append(kw.get('t'))
        return real_send(**kw)

    app.net.send = spy
    app.me, app._pending_role, app._pending_sent = None, 'p1', True
    app._last_hb = 0.0
    pump(config.HEARTBEAT_SEC + 0.4)
    chk('还没认领身份时也发心跳', 'ping' in sent, repr(sent))
    app.me = {'role': 'p1'}
    app._pending_role, app._pending_sent = None, False
    app.net.send = real_send

    # 那句「正在重试」得自己走。从前只写不清，连回来了还挂在那儿，
    # 人看着以为一直断着。
    dlg5 = ClaimDialog(app.root, players={
        'p1': None, 'p2': None, 'p3': None, 'p4': None})
    app._claim_dlg = dlg5
    app._set_offline(True)
    chk('断线时认领窗会说连不上', dlg5.note_text() == OFFLINE_NOTE,
        repr(dlg5.note_text()))
    app._set_offline(False)
    chk('连回来了那句话自己消失', dlg5.note_text().strip() == '',
        repr(dlg5.note_text()))
    # 同一行也用来报「刚被别人认领了」。重连时只许擦自己写的那句，
    # 不能把人家的话一起抹掉。
    dlg5.set_note('「猥琐」刚被别人认领了，换一个吧')
    app._set_offline(False)
    chk('只擦自己写的那句，别人的提示不动',
        '刚被别人认领' in dlg5.note_text(), repr(dlg5.note_text()))
    app._claim_dlg = None
    dlg5.cancel()

    # ---- 20. 自动更新：搬到了托盘的菜单里 ----
    # 源码模式下 update.enabled() 一定是 False（不打包就没法换 exe），
    # 所以这里临时把它当成「已经打包」，只验文案这一层。
    real_frozen = update.is_frozen
    old_url = config.UPDATE_URL
    update.is_frozen = lambda: True
    try:
        config.UPDATE_URL = ''
        app.upd_state = 'idle'
        labels = app.tray_menu().labels()
        chk('没配更新地址时，托盘里写着「未配置」',
            any('检查更新 · 未配置' in l for l in labels), ' / '.join(labels))
        pet_labels = app.build_self_menu().labels()
        chk('宠物右键菜单里已经没有「检查更新」',
            not any('更新' in l for l in pet_labels), ' / '.join(pet_labels[-3:]))

        config.UPDATE_URL = 'http://127.0.0.1:1/latest.json'
        labels = app.tray_menu().labels()
        chk('配了地址就出现「检查更新」', '检查更新' in labels, ' / '.join(labels))

        app.upd_state = 'checking'
        labels = app.tray_menu().labels()
        chk('检查中说「正在检查更新…」',
            any('正在检查' in l for l in labels), ' / '.join(labels))

        app.upd_state = 'downloading'
        app.upd_info = {'version': '1.2.0'}
        app.upd_pct = 0.42
        labels = app.tray_menu().labels()
        chk('下载中显示版本和百分比',
            any('1.2.0' in l and '42%' in l for l in labels), ' / '.join(labels))

        app.upd_state = 'ready'
        labels = app.tray_menu().labels()
        chk('下好了写「更新到 1.2.0 · 重启」',
            any('更新到 1.2.0' in l for l in labels), ' / '.join(labels))

        # 源码模式下真去换会被拒 —— 恰好可以验证失败路径不会静默
        app.upd_state = 'ready'
        app.update_action()
        pump(0.15)
        chk('换不了的时候会说明原因', app.upd_state == 'error'
            and bool(app.upd_note.strip()),
            '%s / %s' % (app.upd_state, app.upd_note))
        chk('失败后托盘里不再假装能点「重启」',
            not any('重启' in l for l in app.tray_menu().labels()),
            ' / '.join(app.tray_menu().labels()))
    finally:
        update.is_frozen = real_frozen
        config.UPDATE_URL = old_url
        app.upd_state = 'idle'
        app.upd_info = None
        app.upd_note = ''

    # ---- 21. 托盘：左键开聊天 / 右键菜单 / 图层开关 ----
    # 图标本身要在真桌面里看，这儿验的是「点了以后会发生什么」那一层：
    # 左键到底开的是聊天还是别的、菜单里写了什么、图层开关切完是什么状态。
    app.me = {'role': 'p1'}
    app.players = {'p1': {'role': 'p1'}, 'p2': None, 'p3': {'role': 'p3'},
                   'p4': None}
    app.chat.close() if app.chat is not None else None
    app.chat = None

    app.tray_left(900, 700)
    chk('左键点托盘 = 直接开聊天', app.chat is not None and app.chat.alive(),
        repr(app.chat))
    chk('开出来停在全体群聊', app.chat is not None and app.chat.key == CONV_ALL,
        repr(app.chat.key if app.chat else None))
    app.chat.close()

    menu = app.tray_menu()
    names = [t for t, _on in menu.items()]
    chk('右键菜单最后一行是「退出」', names and names[-1] == '退出', names)
    chk('右键菜单里有图层开关', any('始终置顶' in t for t in names), names)
    chk('右键菜单里有检查更新', any('检查更新' in t for t in names), names)
    chk('有身份时右键菜单能换身份', '换一个身份' in names, names)
    chk('托盘不再有「打开面板」那一项', '打开面板' not in names, names)
    heads = menu.labels()
    chk('灰字说明了左键干什么', any('左键' in t for t in heads), heads)

    # 图层：描述必须说清「现在是什么、点完变成什么」
    chk('默认是始终置顶（带勾）', petwin.TOP_ON['on'] is True
        and app.layer_label().startswith('✓'), app.layer_label())
    chk('说明写了现在的状态和点击后的结果',
        '当前' in app.layer_hint() and '点击' in app.layer_hint(),
        app.layer_hint())
    on_hint = app.layer_hint()
    app.layer_toggle()
    chk('切一下变成不带勾', petwin.TOP_ON['on'] is False
        and not app.layer_label().startswith('✓'), app.layer_label())
    chk('说明跟着换了一版（点完会变成什么）',
        app.layer_hint() != on_hint and '当前' in app.layer_hint(),
        app.layer_hint())
    app.layer_toggle()
    chk('再切回始终置顶', petwin.TOP_ON['on'] is True, app.layer_label())

    # 菜单开着的时候切图层：菜单项文案要能跟着变（probe 每 200ms 重算一次）
    live_menu = app.tray_menu()
    before = [t for t in live_menu.labels() if '始终置顶' in t]
    app.layer_toggle()
    app.root.update()
    after = [t for t in live_menu.labels() if '始终置顶' in t]
    chk('菜单里的图层行会跟着状态变', before != after,
        '%s → %s' % (before, after))
    app.layer_toggle()

    # 托盘能不能挂，取决于环境变量（探针自己会关掉，免得右下角闪图标）
    old_no = os.environ.get('PETCLUB_NO_TRAY')
    os.environ['PETCLUB_NO_TRAY'] = '1'
    chk('PETCLUB_NO_TRAY=1 时不挂图标', tray_wanted() is False, '')
    os.environ.pop('PETCLUB_NO_TRAY', None)
    chk('默认是要挂的', tray_wanted() is True, '')
    if old_no is None:
        os.environ.pop('PETCLUB_NO_TRAY', None)
    else:
        os.environ['PETCLUB_NO_TRAY'] = old_no

    peer2.close()
    peer.close()
    try:
        app.quit()
    except Exception as e:
        print('退出时异常：%r' % (e,))
    srv.shutdown()
    fail = [n for n, ok in RESULTS if not ok]
    print('\n通过 %d 项，失败 %d 项' % (len(RESULTS) - len(fail), len(fail)))
    for n in fail:
        print('  - %s' % n)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
