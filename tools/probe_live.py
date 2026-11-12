# -*- coding: utf-8 -*-
"""端到端探针：真 Qt 窗口 + 真本地服务器。

验证整条链路，不看单个函数的返回值：

  连服务器 → 认领身份 → 桌面上长出自己那只 → 别人认领时他的角色从天而降 →
  收互动事件（动作 + 气泡 + 聊天记录 + 集合爬行）→ 他离开后角色消失 →
  聊天面板 / 未读 / 托盘闪烁 / 媒体收发 / 拖动 / 认领窗

驱动方式是手摇的：不启动 Qt 的定时器，每一轮自己调一次 `app.tick()`。
这样每一步都可复现，不会因为真实鼠标、真实时钟插一脚而偶发失败。

跑法：py tools/probe_live.py
"""

import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from PIL import Image                                   # noqa: E402
from PySide6.QtCore import QPoint                       # noqa: E402

import server as S                                      # noqa: E402
from test_server import C                               # noqa: E402
from petclub import actions as A                        # noqa: E402
from petclub import media                              # noqa: E402
from petclub.app import App, GATHER_GAP               # noqa: E402
from petclub.chat import CONV_ALL                       # noqa: E402
from petclub.claim import ClaimDialog                   # noqa: E402
from petclub.config import (ROLES, ROLE_NAME, assets_dir,  # noqa: E402
                            load_prefs)

TMP = os.path.join(HERE, '..', '.tmp', 'probe-live')

PASS = []
FAIL = []
ERRORS = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    if detail and not isinstance(detail, str):
        detail = repr(detail)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''),
          flush=True)


def hook(tp, val, tb):
    """未捕获异常既记账也照常打出来 —— 只记账的话，探针会静默退出，
    屏幕上只剩一句「失败 1 项」，看不出是哪一行。"""
    text = ''.join(traceback.format_exception(tp, val, tb))
    ERRORS.append(text)
    sys.stderr.write(text)


sys.excepthook = hook


class FakeTray:
    """替掉真托盘：探针只关心「该不该闪、闪没闪、提示写对没有」。

    真 QSystemTrayIcon 在这台机器上闪不闪得出来，是眼睛的事，测不了。
    """

    def __init__(self):
        self.ok = True
        self.can_blink = True
        self.frames = []
        self.tips = []
        self.on = False

    def blink(self, on):
        self.frames.append(bool(on))
        self.on = bool(on)
        return self.on

    def set_tip(self, text):
        self.tips.append(text)

    def close(self):
        self.ok = False


def main():
    os.makedirs(TMP, exist_ok=True)
    S.HEARTBEAT_TIMEOUT = 30.0
    S.REAP_INTERVAL = 0.5
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=S.reaper_loop, daemon=True).start()

    app = App('127.0.0.1', port)
    app.tray = FakeTray()
    app._tray_tip = ''

    # 认领窗是真模态（exec()），探针里弹出来会卡住手摇循环：换成自动选 p1。
    claims = []

    def fake_claim():
        app._claim_open = False
        claims.append('p1')
        app._pending_role = 'p1'
        app._pending_sent = False

    app._open_claim = fake_claim

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            app.qapp.processEvents()
            app.tick()
            time.sleep(0.005)
        app.qapp.processEvents()

    def settle(least=0.0):
        """等所有角色站稳（phase 回 idle）。

        不能看 Pet.landed：那是「落过地」的历史标记，被重新抓起来扔掉也不会
        清零，拿它判断会当场返回、下一步就看到一只半空中的角色。
        """
        pump(least)
        end = time.time() + 4.0
        while time.time() < end:
            pump(0.05)
            if all(p.phase == 'idle' for p in app.pets.values()):
                return True
        return False

    app.connect()
    pump(0.5)

    # ---- 1. 认领身份 ----
    print('--- 身份 ---')
    chk('自动弹出认领（还是没人认领状态）', claims == ['p1'] or app.me, claims)
    chk('认领后 me 是 p1', (app.me or {}).get('role') == 'p1', app.me)
    chk('桌面上只有自己这一只', list(app.pets) == ['p1'], list(app.pets))

    # ---- 2. 自己是「从天而降」出场的 ----
    pet = app.pets.get('p1')
    pump(0.1)
    chk('刚出来时在半空中（y 在地面之上）', pet.y < pet.ground, (pet.y, pet.ground))
    chk('还没落地就不接动作（避免半空中挥手）',
        not pet.landed and pet.phase == 'fall', (pet.phase, pet.landed))
    chk('等它落地', settle(), app.pets['p1'].phase)
    chk('落地后 y == 地面', abs(pet.y - pet.ground) < 0.5, (pet.y, pet.ground))
    chk('落地后回到 idle', pet.pose == 'idle', pet.pose)

    sw = app.sw
    want_x = sw * 1.0 / (len(ROLES) + 1)
    chk('横向位置按「第几个身份」等分', abs(pet.x - want_x) <= 2.0,
        '%r vs %r' % (pet.x, want_x))

    # 出场那句话说在落地之后，不是在空中喊
    pump(0.3)
    b = app.bubbles.get('p1')
    chk('落地后说了句「到位啦」', b is not None and '到位啦' in b._text,
        b._text if b else None)

    # ---- 3. 别人认领 → 他的角色从天而降 ----
    print('\n--- 别人加入 ---')
    c2 = C(port)
    c2.wait('state', 3)
    c2.send(t='join', role='p2')
    joined = c2.wait('joined', 3)
    chk('服务端确认他认领成功', (joined or {}).get('role') == 'p2', joined)
    pump(0.4)
    chk('我的桌面上多了一只 p2', 'p2' in app.pets, list(app.pets))
    p2 = app.pets['p2']
    chk('p2 也是从上面掉下来的', p2.y < p2.ground, (p2.y, p2.ground))
    chk('没认领的 p3 不显示（桌面不是空的）',
        'p3' not in app.pets, list(app.pets))
    chk('p2 落地', settle())
    chk('两只能并排站（x 不同）', abs(app.pets['p1'].x - p2.x) > 10,
        (app.pets['p1'].x, p2.x))

    # ---- 4. 互动事件作用在他的角色上 ----
    print('\n--- 互动 ---')
    c2.send(t='event', kind='wave', text='')
    chk('收到他的挥手', (c2.wait('state', 1), True)[1])
    pump(0.2)
    chk('他的角色在做挥手动作', p2.pose == 'wave', p2.pose)
    chk('我的角色跟着挥手（mirror）', app.pets['p1'].pose == 'wave',
        app.pets['p1'].pose)
    b2 = app.bubbles.get('p2')
    chk('他头顶有气泡', b2 is not None, b2._text if b2 else None)

    pump(2.0)
    chk('动作结束后回到 idle', p2.pose == 'idle', p2.pose)

    # 全员广播：不是只有「我自己」跟着做，桌上所有角色都要一起做
    app._spawn('p3')
    chk('临时放出第三只并落地', settle(), list(app.pets))
    c2.drain('event')
    c2.send(t='event', kind='drink', text='')
    pump(0.2)
    chk('全员喝水：发起者的角色在喝', p2.pose == 'drink', p2.pose)
    chk('全员喝水：我也在喝', app.pets['p1'].pose == 'drink',
        app.pets['p1'].pose)
    chk('全员喝水：桌面上第三个角色也在喝（不只自己跟着做）',
        app.pets['p3'].pose == 'drink', app.pets['p3'].pose)
    app.pets.pop('p3').destroy()
    pump(0.1)

    # 起飞：不是摆姿势，是真的升空 —— 发起方和桌上其他人都得离开地面
    c2.drain('event')
    ground1 = app.pets['p1'].ground
    app.do_action('fly')
    pump(0.25)
    chk('起飞：发起者已经离地',
        app.pets['p1'].phase in ('rise', 'fall')
        and app.pets['p1'].y < ground1,
        (app.pets['p1'].phase, app.pets['p1'].y, ground1))
    ev = c2.drain('event')
    chk('起飞也发给对方了', ev is not None and ev.get('kind') == 'fly', ev)
    chk('起飞：别人的角色跟着升空（不是只有自己）',
        p2.phase in ('rise', 'fall') and p2.y < ground1,
        (p2.phase, p2.y, ground1))
    pump(2.5)
    chk('起飞落地后回到 idle',
        p2.phase == 'idle' and abs(p2.y - ground1) <= 1.5,
        (p2.phase, p2.y, ground1))

    # 只做给我看的指令不传给他，他做的事也不该在「只对他」的会话里重复记
    app._log_tip('wave', '你', 'p2', target=ROLE_NAME.get('p2'))
    chk('单聊里的痕迹带「对他」的意思', '胡大神' in app.chat_log['p2'][-1]['text'],
        app.chat_log['p2'][-1]['text'])

    # 以前「只在自己屏幕上」的那几条，现在也要真的传出去
    c2.drain('event')
    app.do_action('sleep', 'p2')
    pump(0.2)
    ev = c2.drain('event')
    chk('睡觉也会发给他（不再只在自己屏幕上）',
        ev is not None and ev.get('kind') == 'sleep' and ev.get('to') == 'p2', ev)
    chk('他那边做的是睡姿', app.pets['p2'].pose == 'sleep', app.pets['p2'].pose)
    c2.drain('event')
    app.do_action('jump')
    pump(0.2)
    ev = c2.drain('event')
    chk('跳一下对全员也发得出去',
        ev is not None and ev.get('kind') == 'jump' and not ev.get('to'), ev)
    chk('对全员时我这边也看得到他跟着跳（mirror 两边一起演）',
        app.pets['p2'].pose == 'jump', app.pets['p2'].pose)
    settle()

    # 集合：唯一一条改位置的指令，走的是 crawl 两帧而不是 mirror 姿势
    c2.drain('event')
    anchor_x = app.pets['p1'].x
    before_x = p2.x
    app.do_action('gather')
    pump(0.25)
    ev = c2.drain('event')
    chk('集合也发一条 event 给对方',
        ev is not None and ev.get('kind') == 'gather' and not ev.get('to'), ev)
    chk('集合：发起者招手，自己不爬',
        app.pets['p1'].pose == 'wave' and not app.pets['p1'].crawling
        and abs(app.pets['p1'].x - anchor_x) < 1.0,
        (app.pets['p1'].pose, app.pets['p1'].x))
    chk('集合：别人的角色用 crawl 两帧在爬',
        p2.crawling and p2.pose in ('crawl_1', 'crawl_2'),
        (p2.phase, p2.pose))
    chk('集合：x 真的在朝发起人靠近',
        abs(p2.x - anchor_x) < abs(before_x - anchor_x),
        (before_x, p2.x, anchor_x))
    chk('集合：最终靠拢并站回 idle',
        settle() and not p2.crawling and p2.pose == 'idle'
        and abs(p2.x - anchor_x) <= p2.win.win_w,
        (p2.x, anchor_x, p2.phase, p2.pose))
    p1 = app.pets['p1']
    gap_px = max(p1.body_w, p2.body_w) * GATHER_GAP
    want = (p1.body_w + p2.body_w) / 2.0 + gap_px
    chk('集合间距按人的宽度算（窗口那圈透明留白不算）',
        p1.body_w < p1.win.win_w and p2.body_w < p2.win.win_w,
        (p1.body_w, p1.win.win_w, p2.body_w, p2.win.win_w))
    chk('站定后两人身体之间正好是那一个空隙',
        abs(abs(p2.x - anchor_x) - want) <= 1.0,
        (p2.x, anchor_x, want))

    # ---- 5. 他离开 → 角色从桌面消失 ----
    print('\n--- 离开 ---')
    c2.send(t='leave')
    pump(0.8)
    chk('他走了，角色从桌面消失', 'p2' not in app.pets, list(app.pets))
    chk('我自己的角色还在', 'p1' in app.pets, list(app.pets))

    # ---- 6. 换人认领同一个身份 ----
    print('\n--- 换人 ---')
    c3 = C(port)
    c3.wait('state', 3)
    c3.send(t='join', role='p2')
    chk('新客户端认领成功', (c3.wait('joined', 3) or {}).get('role') == 'p2')
    settle()
    chk('同一个身份换了人，角色照样出现', 'p2' in app.pets, list(app.pets))
    chk('本人没被顶掉', (app.me or {}).get('role') == 'p1', app.me)

    # ---- 7. 右键菜单 ----
    print('\n--- 菜单 ---')
    me_win = app.pets['p1'].win
    self_menu = app.build_self_menu()
    other_menu = app.build_other_menu('p2')
    self_texts = [a.text() for a in self_menu.actions()]
    other_texts = [a.text() for a in other_menu.actions()]

    chk('自己的菜单第一行写明「我 → 全员」',
        self_texts[0].startswith(ROLE_NAME['p1']) and '全员' in self_texts[0],
        self_texts[0])

    # 菜单里哪些项是指令：按 label 反查 key（分隔线和标题行的 text 是空串）
    labels = {A.get(k)['label']: k for k in A.ORDER}

    def actions_of(menu):
        return [(a, labels[a.text()]) for a in menu.actions()
                if a.text() in labels]

    probes = actions_of(self_menu)
    chk('动作项数量和 ORDER 一致', len(probes) == len(A.ORDER),
        (len(probes), len(A.ORDER)))
    chk('动作项顺序和 ORDER 一致',
        [k for _act, k in probes] == list(A.ORDER))
    chk('菜单里不再列服务器成员（在线状态已经不在这一层看）',
        not any('服务器成员' in t for t in self_texts), self_texts)
    chk('菜单里没有每人一条的「在线 / 空闲」',
        not any(t.endswith('· 在线') or t.endswith('· 空闲')
                for t in self_texts), self_texts)
    chk('菜单里有「换一个身份」', '换一个身份' in self_texts, self_texts)
    chk('菜单里有「退出」', '退出' in self_texts, self_texts)

    chk('别人的菜单第一行写明「只对他」',
        '只对他' in other_texts[0] and ROLE_NAME['p2'] in other_texts[0],
        other_texts[0])
    chk('别人的菜单不列成员', not any('服务器成员' in t for t in other_texts),
        other_texts)
    chk('两个菜单的动作项个数一样', len(actions_of(other_menu)) == len(probes))

    # 没有冷却：做过一次的动作，菜单里照样是亮的、文案没有秒数
    self_menu.close()
    other_menu.close()
    app.do_action('wave')
    pump(0.15)
    menu2 = app.build_self_menu()
    wave_act = [a for a, k in actions_of(menu2) if k == 'wave'][0]
    chk('刚做过的动作立刻还能点（没有冷却）', wave_act.isEnabled(),
        wave_act.text())
    chk('文案里不再带倒计时', '秒' not in wave_act.text(), wave_act.text())
    menu3 = app.build_other_menu('p2')
    drink_other = [a for a, k in actions_of(menu3) if k == 'drink'][0]
    chk('「只对他」的菜单也一样亮着', drink_other.isEnabled(),
        drink_other.text())
    for m in (menu2, menu3):
        m.close()

    # ---- 8. 拖动：只有自己看得见的玩法 ----
    print('\n--- 拖动 ---')
    pet = app.pets['p1']
    start = (pet.x, pet.y)
    app.on_pet_press(me_win, QPoint(int(pet.x), int(pet.y - 40)))
    app.on_pet_drag(me_win, QPoint(int(pet.x) + 60, int(pet.y - 90)))
    chk('抓起来以后是 drag 姿势', pet.pose == 'drag', pet.pose)
    chk('被抓着时不受重力（y 不会自己涨）',
        pet.phase == 'hold', pet.phase)
    moved_x = pet.x
    chk('位置跟手（x 变了）', abs(moved_x - start[0]) > 20, (start[0], moved_x))
    app.on_pet_drop(me_win, QPoint(int(moved_x), int(pet.y)))
    chk('松手后开始下落', pet.phase == 'fall', pet.phase)
    chk('松手后落回地面', settle(), pet.phase)
    chk('落地位置就是松开时的 x', abs(pet.x - moved_x) < 2.0,
        (pet.x, moved_x))

    # 点一下（不拖动）= 摸摸头
    app.on_pet_press(me_win, QPoint(int(pet.x), int(pet.y - 40)))
    app.on_pet_drop(me_win, QPoint(int(pet.x), int(pet.y - 40)))
    chk('原地按一下 = 摸摸头（伸手的 wave 姿势）', pet.pose == 'wave', pet.pose)

    # 拖动不发网络消息：别人屏幕上不该有变化
    c3.drain('event')
    app.on_pet_press(me_win, QPoint(int(pet.x), int(pet.y - 40)))
    app.on_pet_drag(me_win, QPoint(int(pet.x) + 50, int(pet.y - 60)))
    app.on_pet_drop(me_win, QPoint(int(pet.x) + 50, int(pet.y - 60)))
    pump(0.2)
    chk('拖动一个字节都不发给别人', c3.drain('event') is None)
    settle()

    # ---- 9. 聊天面板：一个群聊 + 每人一个单聊 ----
    print('\n--- 聊天面板 ---')
    chat = app.open_chat(None)
    chk('打开的是群聊', chat.key == CONV_ALL, chat.key)
    keys = [k for k, _ in chat.convs()]
    chk('会话栏有群聊 + 其他人各一个', keys == [CONV_ALL] + [r for r in ROLES
                                                if r != 'p1'], keys)
    chk('自己的单聊不在列表里', 'p1' not in keys, keys)
    chk('会话栏真的建出了这么多行', len(chat._rows) == len(keys),
        len(chat._rows))

    # 群里收到一句话
    c3.send(t='event', kind='say', text='在的', mt='text')
    pump(0.3)
    chk('群聊里收到他的话', '在的' in chat.history_text(CONV_ALL),
        chat.history_text(CONV_ALL))
    chk('记录里写的是他说的', chat.entries(CONV_ALL)[-1].get('who') == 'p2',
        chat.entries(CONV_ALL)[-1])

    # 我发言
    chat.field.setText('我也在')
    chat.send()
    pump(0.2)
    chk('我发的话进了记录', chat.history_text().splitlines()[-1] == '我也在',
        chat.history_text().splitlines()[-1:])
    chk('连发几句都不拦（没有发言冷却）',
        app.send_chat('再来一句') == '' and app.send_chat('再来一句') == '',
        app.send_chat('再来一句'))

    # ---- 10. 未读与托盘 ----
    print('\n--- 未读与托盘 ---')
    app.open_chat('p3')          # 切走，群里的话才开始算未读
    chk('切到单聊', chat.key == 'p3', chat.key)
    c3.send(t='event', kind='say', text='群里喊一句', mt='text')
    pump(0.3)
    chk('不在看群聊时，群消息算未读', app.unread.get(CONV_ALL, 0) == 1,
        app.unread)
    before = len(app.tray.frames)
    pump(1.6)
    frames = app.tray.frames[before:]
    chk('托盘开始闪', len(frames) >= 2, frames)
    chk('闪是真的交替（不是一直亮）', frames.count(True) >= 1
        and frames.count(False) >= 1, frames)
    chk('提示里带未读条数',
        any('1 条未读' in t for t in app.tray.tips), app.tray.tips)

    app.tray_left()
    chk('左键托盘落到最新那条未读的会话', app.chat.key == CONV_ALL, app.chat.key)
    chk('进去以后未读清零', app.unread.get(CONV_ALL, 0) == 0, app.unread)
    pump(1.6)
    chk('没未读了就不闪了', app.tray.frames[-1] is False, app.tray.frames[-3:])

    # 自己的发言、动作痕迹都不算未读
    app.open_chat('p3')
    n0 = sum(app.unread.values())
    c3.send(t='event', kind='wave', text='')
    pump(0.3)
    chk('别人的动作痕迹不算「有话说」', sum(app.unread.values()) == n0,
        app.unread)
    c3.send(t='event', kind='say', text='Hi', mt='text', to='p1')
    pump(0.3)
    chk('私聊落在 p2 的会话里', app.unread.get('p2', 0) == 1, app.unread)
    chk('私聊不会再进群聊', app.unread.get(CONV_ALL, 0) == 0, app.unread)

    # ---- 11. 媒体 ----
    print('\n--- 媒体 ---')
    app.open_chat(CONV_ALL)
    why = app.send_media('emoji', '😀')
    chk('表情发得出去', why == '', why)
    e = app.chat_log[CONV_ALL][-1]
    chk('记录里是 emoji 类型', e.get('mt') == 'emoji', e.get('mt'))
    chk('表情字符存进了记录', e.get('char') == '😀', e.get('char'))

    # 表情包：三页拾取面板，翻页是同一块格子换内容
    pop = chat._picker_pop()
    chat._pop = pop
    chk('拾取面板有三页（表情 / 我的 / 他人）',
        set(chat._tabs) == {'emoji', 'mine', 'others'}, list(chat._tabs))
    chat._fill_page('mine')
    chk('「我的」是自己那八张', chat._pick_grid.count() == 8,
        chat._pick_grid.count())
    chat._fill_page('others')
    want = (len(ROLES) - 1) * 8
    chk('「他人」是另外 %d 个角色各八张' % (len(ROLES) - 1),
        chat._pick_grid.count() == want, chat._pick_grid.count())
    chat._fill_page('emoji')
    doge = media.doge_path(assets_dir())
    has_doge = os.path.exists(doge)
    chk('「表情」页 = 内置 emoji（有贴图时多一格）',
        chat._pick_grid.count() == len(media.EMOJI_CHARS) + (1 if has_doge else 0),
        chat._pick_grid.count())
    first = chat._pick_grid.itemAt(0).widget()
    chk('没有贴图时第一格就是字符 emoji',
        (first._pix is not None and first._text == '') if has_doge
        else (first._pix is None and first._text == media.EMOJI_CHARS[0]),
        (first._pix, first._text))
    chk('翻页时勾选状态跟着走', chat._tabs['emoji'].isChecked()
        and not chat._tabs['mine'].isChecked())

    card = media.sticker_paths(assets_dir(), 'p1')[0]
    chat._pick_sticker(card)
    chk('点一张表情包就发出去了',
        app.chat_log[CONV_ALL][-1].get('mt') == 'sticker',
        app.chat_log[CONV_ALL][-1].get('mt'))
    chk('发出去以后面板自己收掉', chat._pop is None)
    chk('表情包记录里带得回一张图',
        media.thumb(app.chat_log[CONV_ALL][-1].get('img'), '', 140) is not None)

    png = os.path.join(TMP, 'shot.png')
    Image.new('RGB', (900, 600), (200, 120, 90)).save(png)
    why = app.send_media('img', png)
    chk('图片发得出去', why == '', why)
    e = app.chat_log[CONV_ALL][-1]
    chk('图片记录里有 base64', bool(e.get('img')) and e.get('mt') == 'img',
        (e.get('mt'), len(e.get('img') or '')))
    th = media.thumb(e.get('img'), '', 220)
    chk('发出去的图能解回来（对面看得见）', th is not None and th.width == 220,
        th.size if th else None)
    chk('原图缩到了 480 以内',
        media.decode(e['img']).width <= media.MAX_SIDE,
        media.decode(e['img']).size)

    txt = os.path.join(TMP, 'note.txt')
    with open(txt, 'w', encoding='utf-8') as f:
        f.write('pet-club 文件传输')
    why = app.send_media('file', txt)
    chk('文件发得出去', why == '', why)
    e = app.chat_log[CONV_ALL][-1]
    chk('文件记录带名字和大小',
        e.get('name') == 'note.txt' and e.get('size') == os.path.getsize(txt),
        (e.get('name'), e.get('size'), os.path.getsize(txt)))

    huge = os.path.join(TMP, 'huge.bin')
    with open(huge, 'wb') as f:
        f.write(b'\0' * (media.MAX_FILE + 10))
    n_before = len(app.chat_log[CONV_ALL])
    why = app.send_media('file', huge)
    chk('太大的文件当场拒绝（不入记录）',
        '太大' in why and len(app.chat_log[CONV_ALL]) == n_before, why)

    # 收到别人的图片
    small = os.path.join(TMP, 'p2.png')
    Image.new('RGB', (300, 200), (90, 160, 220)).save(small)
    b64, _w, _h = media.encode_image(small)
    c3.send(t='event', kind='say', mt='img', img=b64, text='')
    pump(0.4)
    e = app.chat_log[CONV_ALL][-1]
    chk('收到图片消息', e.get('mt') == 'img' and e.get('who') == 'p2',
        (e.get('mt'), e.get('who')))

    # ---- 12. 搜索 ----
    print('\n--- 搜索 ---')
    chat._on_search('胡')
    vis = [k for k, _ in chat.visible_convs()]
    chk('按名字搜能搜到那个会话', 'p2' in vis, vis)
    chk('没命中的会话被过滤掉', not ({'p3', 'p4', 'p5'} & set(vis)), vis)
    chat._on_search('在的')
    vis = [k for k, _ in chat.visible_convs()]
    chk('按内容也能搜到', CONV_ALL in vis, vis)
    chat._on_search('不存在的词')
    chk('搜不到就是空列表', chat.visible_convs() == [])
    chat._on_search('')
    chk('清空搜索恢复全部', len(chat.visible_convs()) == len(chat.convs()))

    # ---- 13. 单聊发不出去（对方不在线）----
    print('\n--- 离线的人 ---')
    app.open_chat('p5')
    app.chat.field.setText('你在吗')
    chat.send()
    note = chat.note.text()
    chk('给不在线的人发消息会被挡住', '不在线' in note, note)

    # ---- 14. 认领窗 ----
    print('\n--- 认领窗 ---')
    dlg = ClaimDialog(None, {ROLES[1]: {'role': ROLES[1]}}, '有人占了中间那张')
    chk('五张角色卡都画出来了', len(dlg.cards) == len(ROLES), len(dlg.cards))
    chk('被别人占的卡标成 taken', dlg.cards[ROLES[1]].taken,
        dlg.cards[ROLES[1]].taken)
    dlg._pick(ROLES[0])
    chk('点一张 = 选中', dlg.role == ROLES[0], dlg.role)
    chk('选中的卡是 selected 状态',
        dlg.cards[ROLES[0]].selected, dlg.cards[ROLES[0]].selected)
    dlg._pick(ROLES[1])
    chk('占掉的卡点不动', dlg.role == ROLES[0], dlg.role)
    chk('没选中的卡不是 selected', not dlg.cards[ROLES[2]].selected,
        dlg.cards[ROLES[2]].selected)
    dlg._pick(ROLES[2])
    chk('换一张就换人', dlg.role == ROLES[2], dlg.role)
    chk('换过去以后上一张取消选中', not dlg.cards[ROLES[0]].selected,
        dlg.cards[ROLES[0]].selected)
    chk('卡片不超出窗口（右边不留空）',
        dlg.cards[ROLES[-1]].geometry().right() <= dlg.width(),
        (dlg.cards[ROLES[-1]].geometry().right(), dlg.width()))
    dlg.close()

    # ---- 15. 长跑稳定性 ----
    print('\n--- 长跑 ---')
    pump(3.0)
    chk('跑了一段没崩', 'p1' in app.pets and app.pets['p1'].pose in
        ('idle', 'jump', 'wave', 'knock', 'heart', 'drink', 'sleep', 'drag'),
        app.pets['p1'].pose)
    chk('期间没有未捕获的异常', not ERRORS, ERRORS[:1])

    # ---- 16. 远程互动开关 ----
    print('\n--- 远程互动开关 ---')
    chk('默认是开的', app.remote_on is True, app.remote_on)
    m = app.tray_menu()
    item = [a for a in m.actions() if a.text() == '同步远程互动']
    chk('托盘菜单里有这一项，而且默认勾着',
        len(item) == 1 and item[0].isCheckable() and item[0].isChecked(),
        [a.text() for a in m.actions()])
    app.remote_toggle(False)
    chk('关掉后偏好落盘', load_prefs().get('remote_actions') is False,
        load_prefs())
    m = app.tray_menu()
    item = [a for a in m.actions() if a.text() == '同步远程互动']
    chk('重开菜单时勾选跟着状态走',
        len(item) == 1 and not item[0].isChecked())
    chk('关掉后自己的角色还在', 'p1' in app.pets and app.pets['p1'].pose == 'idle',
        app.pets.get('p1').pose if 'p1' in app.pets else None)

    settle()
    pump(2.8)                   # 先把上一段留下的气泡等过期，下面的判断才说明问题
    shown = [r for r, b in app.bubbles.items() if b.isVisible()]
    chk('等了一会儿，桌面上没有气泡了', not shown, shown)
    tips = len(app.chat_log.get(CONV_ALL) or [])
    c3.send(t='event', kind='drink', text='')
    pump(0.6)
    chk('关掉后他那边发来的指令不演了',
        app.pets['p2'].pose == 'idle', app.pets['p2'].pose)
    chk('关掉后我这边也不跟着做', app.pets['p1'].pose == 'idle',
        app.pets['p1'].pose)
    shown = [r for r, b in app.bubbles.items() if b.isVisible()]
    chk('关掉后谁都不冒泡', not shown, shown)
    log = app.chat_log.get(CONV_ALL) or []
    chk('但聊天记录里仍旧留一条',
        len(log) > tips and log[-1].get('kind') == 'tip'
        and '喝水' in log[-1].get('text', ''), log[-1:])

    why = app.do_action('wave')
    chk('自己的指令照常发得出去', why is None and app.pets['p1'].pose == 'wave',
        (why, app.pets['p1'].pose))
    pump(2.0)

    app.remote_toggle(True)
    chk('打开后恢复同步', app.remote_on is True
        and load_prefs().get('remote_actions') is True)
    c3.send(t='event', kind='drink', text='')
    pump(0.6)
    chk('打开后他又演了一遍', app.pets['p2'].pose == 'drink', app.pets['p2'].pose)
    pump(3.0)

    # 同一个开关也挂在聊天窗左下角的头像菜单上
    chat._show_me_menu()
    item = [a for a in chat._me_menu.actions() if a.text() == '同步远程互动']
    chk('聊天窗头像菜单里也有这一项，勾选跟 App 一致',
        len(item) == 1 and item[0].isCheckable() and item[0].isChecked(),
        [a.text() for a in chat._me_menu.actions()])
    item[0].trigger()
    chk('在这里关掉，App 和 prefs 一起变',
        app.remote_on is False and load_prefs().get('remote_actions') is False
        and chat.remote_on is False,
        (app.remote_on, load_prefs().get('remote_actions'), chat.remote_on))
    chat._me_menu.hide()
    app.remote_toggle(True)
    chk('从托盘再打开时，聊天窗那份状态也同步回来',
        app.remote_on is True and chat.remote_on is True,
        (app.remote_on, chat.remote_on))

    # ---- 17. 退出 ----
    print('\n--- 收尾 ---')
    c2.close()
    c3.close()
    app.quit()
    pump(0.2)
    chk('退出时把窗口都藏起来了',
        not any(p.win.isVisible() for p in app.pets.values()), '还有窗口在')
    chk('退出后不再处理消息（_closing 生效）', app._closing)

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    for e in ERRORS[:3]:
        print(e)
    srv.shutdown()
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
