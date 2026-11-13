# -*- coding: utf-8 -*-
"""主程序：网络、动作、聊天记录、更新、认领流程 + Qt 窗口的生命周期。

线程模型：网络在子线程，主线程的 QTimer 每 33ms 从队列里拉一次消息。
Qt 的槽都在主线程执行，跨线程只走 `queue.Queue`，不碰控件。
"""
import base64
import os
import queue
import random
import sys
import time

from PIL import Image
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from . import config, media, store, update, windows
from .actions import (ORDER, get, media_line, reply_line, say_line,
                      tip_text)
from .actors import Pet, gather_targets
from .effects import Fireworks
from .config import ROLES, ROLE_NAME, assets_dir, files_dir
from .host import cursor_pos, foreground, hwnd_at, owner_at, tray_icons, work_area
from .net import Net
from .chat import CONV_ALL, ChatWindow
from .claim import ClaimDialog
from .theme import QSS as UI_QSS
from .tray import Tray
from .sprites import load_all

BUBBLE_SEC = 2.6
GROUND_MARGIN = 34
DRAG_SLOP = 4
GATHER_GAP = 0.10       # 集合后两个人**身体之间**的空隙 = 最宽那只的身宽 × 它

CHAT_HISTORY = 200
TRAY_BLINK_SEC = 0.7
TOY_SEC = 8.0           # 逗猫棒持续多久：够逗两圈，又不至于一直追着光标跑
WATER_MIN = 45          # 喝水提醒默认间隔（分钟）
SIT_MIN = 60            # 久坐提醒默认间隔（分钟）

OFFLINE_NOTE = '连不上服务器，正在重试…'


def endpoint(host=None, port=None):
    return (config.SERVER_HOST if host is None else host,
            config.SERVER_PORT if port is None else port)


def tray_wanted():
    return os.environ.get('PETCLUB_NO_TRAY') != '1'


class App:

    def __init__(self, host=None, port=None):
        host, port = endpoint(host, port)
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)   # 关掉所有窗口也要留在托盘
        # 任务栏 / 窗口图标：不设的话源码模式下是 Python 的图标，
        # 认领窗和聊天窗各显示各的，看着像两个程序。
        ico = os.path.join(assets_dir(), 'app.ico')
        if os.path.exists(ico):
            self.qapp.setWindowIcon(QIcon(ico))
        # 全局样式：按钮、输入框、滚动条、弹出菜单。挂在 QApplication 上，
        # 所以弹出去的菜单（不是任何窗口的后代）也一样吃得到。
        self.qapp.setStyleSheet(UI_QSS)

        self.sprites = load_all(assets_dir(), ROLES)
        # 本机偏好：桌宠大小、要不要同步别人发来的互动（见托盘菜单）。
        # 文件坏了 / 没写过都回到默认：默认大小、默认开着远程互动。
        self._prefs = config.load_prefs()
        _size = self._prefs.get('pet_size')
        config.PET_SIZE = (_size if _size in config.PET_SIZE_FACTOR
                           else 'default')
        self.remote_on = self._prefs.get('remote_actions') is not False
        left, top, right, bottom = work_area()
        self.sw = right - left
        self.sh = bottom - top

        self.host = host
        self.port = port
        self.net = Net(host, port)
        self.bubbles = {}
        self._bubble_until = {}

        self.me = None
        self.players = {}
        self.pets = {}

        self._pending_role = None
        self._pending_sent = False
        self._claim_open = False
        self._claim_dlg = None
        self._note = ''
        self._state_seen = False
        self._stash = []
        self._last_hb = 0.0
        self._last_t = time.monotonic()
        self._say_on_land = None
        self._closing = False
        self._last_fg = None
        self._top_checked = 0.0
        self._top_on = True

        self._drag = None
        self.offline = False

        # 分部位点击的连击计数：{role: [(zone, ts), ...]}，只存最近四秒。
        self._taps = {}
        # 逗猫棒：被逗的那只 + 结束时间 + 下一次蹦跶的时间。
        self._toy_role = None
        self._toy_until = 0.0
        self._toy_hop = 0.0
        self._fx = None
        self._fx_trash = []
        # 喝水提醒：只保留这一种，间隔可改
        self._reminder_at = 0.0
        self._reminder_min = WATER_MIN
        self._water_on = False
        self._reminder_act = None
        # 久坐提醒：和喝水提醒同一套开关，默认关
        self._sit_on = False
        self._sit_min = SIT_MIN
        self._sit_next = 0.0
        self._sit_due = False
        self._sit_due_until = 0.0

        # Qt 对象的所有权只能有一套：这里全部交给 Python 引用计数。
        # 千万不要 deleteLater() 之后又把引用丢掉 —— shiboken 会立刻 delete
        # 掉 C++ 对象，Qt 排队中的 DeferredDelete 再删一次，就是 double free
        # （WER 里的 0xc0000374 堆损坏）。信号派发栈上更不能丢引用。
        self._menu = None
        self._menu_prev = None      # 上一个菜单，多留一拍再让 GC 回收
        self._menu_open = False
        self._claim_prev = None     # 刚 exec() 完的认领窗，同理
        self._win_trash = []        # 退出前收留所有窗口，留到进程结束
        self.chat = None
        self._chat_trash = []       # 已关闭但还不能回收的面板（见 _chat_closed）
        self.history = store.History(host, port)
        self.chat_log = self.history.load()
        self.unread = {}
        self._tray_on = False
        self._tray_at = 0.0
        self._tray_tip = ''

        self.upd_state = 'idle'
        self.upd_info = None
        self.upd_pct = 0.0
        self.upd_note = ''
        self.upd_q = queue.Queue()
        self._upd_asked = False
        self._upd_next = 0.0
        self._upd_apply_id = None
        self._upd_checker = update.Checker(lambda k, p: self.upd_q.put((k, p)))

        self.tray = None
        self._timer = QTimer(self.qapp)
        self._timer.setInterval(max(16, int(config.POLL_MS)))
        self._timer.timeout.connect(self.tick)

    # ---------------- 启动 ----------------

    def run(self):
        QTimer.singleShot(120, self.connect)
        QTimer.singleShot(60, self.start_tray)
        if update.enabled():
            QTimer.singleShot(int(config.UPDATE_START_DELAY * 1000),
                              self.check_update)
        self._timer.start()
        return self.qapp.exec()

    def connect(self):
        if self._closing:
            return
        self.net.start()

    def tick(self):
        if self._closing:
            return
        # 认领窗是模态的：它自己的定时器在拉网络，这里什么都不做，
        # 否则两处同时处理消息（Qt 的 exec() 不会停掉别的定时器）。
        if self._claim_dlg is not None:
            return
        now = time.monotonic()
        dt = now - self._last_t
        self._last_t = now
        if dt > 0.25:
            dt = 0.25

        for m in self._stash + self.net.poll():
            self.on_msg(m)
        self._stash = []

        self._pump_update()
        self._tray_tick(now)
        if update.enabled() and self._upd_next and now >= self._upd_next:
            self._upd_next = now + config.UPDATE_EVERY
            self.check_update()

        if self._pending_role and not self._pending_sent and self.net.connected:
            if self.net.send(t='join', role=self._pending_role,
                             client='desktop'):
                self._pending_sent = True

        if self.net.connected and now - self._last_hb >= config.HEARTBEAT_SEC:
            self._last_hb = now
            self.net.send(t='ping')

        for pet in list(self.pets.values()):
            pet.update(dt)
        self._tick_toy(now)
        self._tick_reminder(now)
        self._tick_sit(now)

        # 爬行时气泡得跟着人走，否则那句「来了」留在原地，人已经爬远了
        for role in self._bubble_until:
            pet = self.pets.get(role)
            b = self.bubbles.get(role)
            if pet is not None and pet.crawling and b is not None:
                b.follow(*pet.bubble_anchor())

        self._keep_top(now)

        if self._say_on_land:
            role, text = self._say_on_land
            pet = self.pets.get(role)
            if pet is None:
                self._say_on_land = None
            elif pet.landed:
                self._say_on_land = None
                self.say_role(role, text)

        if self._bubble_until:
            for role, until in list(self._bubble_until.items()):
                if now > until:
                    b = self.bubbles.get(role)
                    if b is not None:
                        b.hide()
                    self._bubble_until.pop(role, None)

    def _tick_toy(self, now):
        """逗猫棒：被逗的那只朝鼠标爬，够近了就蹦一下。"""
        role = self._toy_role
        if not role:
            return
        pet = self.pets.get(role)
        if now >= self._toy_until or pet is None:
            if pet is not None:
                pet.stop_chase()
            self._toy_role = None
            return
        if pet.held or pet.airborne or not pet.landed:
            return

        # 角色坐标是屏幕物理像素。Windows 上直接问系统要光标（物理像素），
        # 高 DPI 下才不会比到「指针左上」去；其他平台用 Qt 逻辑像素 × dpr。
        pos = cursor_pos()
        if pos is not None:
            cx, cy = float(pos[0]), float(pos[1])
        else:
            cur = QCursor.pos()
            scr = QGuiApplication.screenAt(QPoint(cur.x(), cur.y()))
            dpr = float(scr.devicePixelRatio()) if scr is not None else pet.win.dpr
            cx, cy = cur.x() * dpr, cur.y() * dpr
        half = pet.win.win_w / 2.0
        target_x = min(max(cx, half), max(half, self.sw - half))
        target_y = min(max(cy, pet.win.win_h),
                       max(pet.win.win_h, self.sh - GROUND_MARGIN))
        if abs(cx - pet.x) > 48 or abs(target_y - pet.y) > 48:
            pet.chase(target_x, target_y)
        else:
            pet.stop_chase()
            if now >= self._toy_hop:
                self._toy_hop = now + 1.2
                pet.act('jump', 0.6)

    def start_toy(self, role):
        """「逗一逗」：这只角色跟着鼠标跑 8 秒，纯本机效果。"""
        if role not in self.pets:
            return
        self._toy_role = role
        self._toy_until = time.monotonic() + TOY_SEC
        self._toy_hop = 0.0

    # ---------------- 提醒 ----------------

    def set_reminder(self, kind, on, minutes=None):
        """喝水 / 久坐提醒：开、关、改间隔，都在这里落一次状态。"""
        if kind not in ('water', 'sit'):
            return
        if minutes is not None:
            minutes = max(5, min(600, int(minutes)))
        if kind == 'water':
            if minutes is not None:
                self._reminder_min = minutes
            self._water_on = bool(on)
            self._reminder_at = (time.monotonic() + self._reminder_min * 60
                                 if self._water_on else 0.0)
            self._reminder_act = None
        else:
            if minutes is not None:
                self._sit_min = minutes
            self._sit_on = bool(on)
            self._sit_next = (time.monotonic() + self._sit_min * 60
                              if self._sit_on else 0.0)
            self._sit_due = False
            self._sit_due_until = 0.0
        if self.chat is not None:
            self.chat.water_on = self._water_on
            self.chat.water_min = self._reminder_min
            self.chat.sit_on = self._sit_on
            self.chat.sit_min = self._sit_min

    def _tick_reminder(self, now):
        if self._reminder_at and now >= self._reminder_at:
            self._reminder_at = time.monotonic() + self._reminder_min * 60
            me = self.me['role'] if self.me else None
            pet = self.pets.get(me) if me else None
            if pet is not None:
                pet.start_crawl(self.sw / 2.0, pet.y)
                self._reminder_act = (me, 'drink', '该喝水啦，润润嗓子')

        act = self._reminder_act
        if not act:
            return
        role, pose, text = act
        pet = self.pets.get(role)
        if pet is None:
            self._reminder_act = None
            return
        if (not pet.held and not pet.airborne
                and not pet.crawling and pet.landed):
            self._reminder_act = None
            pet.act(pose, 2.8)
            self.say_role(role, text)

    def _tick_sit(self, now):
        if self._sit_due and now > self._sit_due_until:
            self._sit_due = False
        if not self._sit_on:
            return
        if self._sit_next <= 0:
            self._sit_next = now + self._sit_min * 60
            return
        if now < self._sit_next:
            return
        self._sit_next = now + self._sit_min * 60
        me = self.me['role'] if self.me else None
        if me in self.pets:
            self._sit_due = True
            self._sit_due_until = now + 120.0
            self.say_role(me, '坐挺久了，起来动动？', sec=10.0)

    def _chat_hwnd(self):
        """聊天框的窗口句柄；没开 / 已经收起来就是 0。"""
        chat = self.chat
        if chat is None:
            return 0
        try:
            if not chat.isVisible():
                return 0
            return int(chat.winId())
        except RuntimeError:            # 底层 C++ 对象已经没了
            return 0

    def _covered_by_chat(self, pt, pet_win):
        """盖住角色的那个自家窗口，是不是聊天框。

        「始终置顶」是对角色说的：聊天框也是自己的一部分，它压住角色等于
        角色消失了。所以自家聊天框要像别的程序一样，把角色重新插上去。
        预览大图 / 表情弹层 / 右键菜单 / 头顶气泡不在此列 —— 那些是临时浮层，
        本来就该压在角色上方，抢的话会出现「点菜单时人被顶到菜单上面」。
        """
        if owner_at(pt[0], pt[1], pet_win) != 'own':
            return False
        hwnd = self._chat_hwnd()
        return bool(hwnd) and hwnd_at(pt[0], pt[1]) == hwnd

    def _keep_top(self, now):
        fg = foreground()
        switched = fg != self._last_fg
        self._last_fg = fg
        chat = self.chat
        if chat is not None and chat.alive():
            # 聊天框刚把自己置顶（用户点了它 / 刚打开）时立刻把角色插回去，
            # 不然巡检要等下一轮，用户会看到「人被聊天框盖住了」好几秒。
            if chat.follow(fg) and self._top_on:
                for pet in self.pets.values():
                    pet.win.reassert_top()

        if not self._top_on or not self.pets:
            return
        if not switched and now - self._top_checked < 3.0:
            return
        self._top_checked = now
        for pet in self.pets.values():
            pt = pet.win.hit_point()
            if pt is None:
                continue
            who = owner_at(pt[0], pt[1], pet.win)
            if who == 'me':
                continue
            # 'own' 默认让位（自家弹层在上面是有意的）；聊天框例外。
            if who == 'own' and not self._covered_by_chat(pt, pet.win):
                continue
            pet.win.reassert_top()

    # ---------------- 消息处理 ----------------

    def on_msg(self, m):
        t = m.get('t')
        if t == 'state':
            self.on_state(m)
        elif t == 'joined':
            self.on_joined(m)
        elif t == 'reject':
            self.on_reject(m)
        elif t == 'event':
            self.on_event(m)
        elif t == 'error':
            self.on_error(m)
        elif t == '_up':
            self._set_offline(False)
            self._pending_sent = False
            if self.me:
                self.net.send(t='join', role=self.me['role'], client='desktop')
            else:
                self.net.send(t='query')
        elif t == '_down':
            self._set_offline(True)
            if self.me:
                self.say_role(self.me['role'], m.get('msg', '和服务器断开了'))

    def on_state(self, m):
        self.players = m.get('players') or {}
        self._state_seen = True
        if self.chat is not None and self.chat.alive():
            self.chat.refresh_state()
        if self.me:
            if not self.players.get(self.me['role']):
                self.me = None
                self._clear_pets()
                self._claim_soon('你的身份已被释放，重新认领一个吧')
                return
            self.sync_pets()
        if self._claim_dlg is not None:
            self._claim_dlg.set_players(self.players)
        elif not self.me and not self._pending_role and not self._claim_open:
            self._claim_soon()

    def on_joined(self, m):
        role = m.get('role')
        first = self.me is None
        self.me = {'role': role}
        self._pending_role = None
        self._pending_sent = False
        # joined 一定先于下一次 state 到：那一刻 players 里还没有自己，
        # 光靠 sync_pets 会让角色晚一帧才出现，出场那句「到位啦」也就跟着丢了。
        # 服务端都说我认领成功了，先把角色放出来（sync_pets 会把它当成
        # 「不在 players 里」删掉，所以顺序必须是先 sync 再补）。
        self.sync_pets()
        if role and role not in self.pets:
            self._spawn(role)
        if first and role in self.pets:
            self._say_on_land = (role, '我是%s，到位啦～' % ROLE_NAME.get(role, ''))

    def on_reject(self, m):
        self._pending_role = None
        self._pending_sent = False
        if isinstance(m.get('players'), dict):
            self.players = m['players']
        self._claim_soon(m.get('msg', '认领失败，换一个身份吧'))

    def on_error(self, m):
        msg = m.get('msg') or '这条没发出去'
        if self.chat is not None and self.chat.alive():
            self.chat.set_note(msg)
        elif self.me:
            self.say_role(self.me['role'], msg)

    @staticmethod
    def _what(spec):
        """这条指令要让角色「做什么」：起飞的 launch，其余是姿势名。"""
        return 'launch' if spec.get('launch') else spec.get('pose')

    def _play(self, pet, what, dur=None):
        """让一只角色动一下。

        `what` 一般是姿势名（wave/drink/jump…），直接丢给 Pet.act；
         'launch' 是起飞 —— 它不是姿势，是把角色从当前位置升到最高处再
        掉下来，走 Pet.take_off 的物理，落地手感和拖动松手完全一样。
        """
        if pet is None:
            return
        if what == 'launch':
            pet.take_off()
        elif what and dur:
            pet.act(what, dur)

    def _gather(self, anchor, only=None):
        """让桌上别的角色爬到 anchor 身边，返回真的动起来的身份。

        位置上不了网，也不该上网：每台机器按**自己屏幕上**的站位算落点，
        所以「向发起人靠拢」在谁的屏幕上都成立，服务端一个字段都不用加。
        `only` 是点名时那一位；广播时不传，所有在场的别人都过来。
        """
        host = self.pets.get(anchor)
        if host is None:
            return []
        roles = [r for r in self.pets
                 if r != anchor and (only is None or r in only)]
        if not roles:
            return []
        pets = [self.pets[r] for r in roles]
        # 间距按**人的宽度**算，不按窗口宽：窗口是所有姿势的最大包围盒，
        # 横着爬的那两张比站着宽一大截，用它排出来的队会散掉。边界也按人的
        # 宽度留（窗口那圈透明留白不算，它不挡人）。
        body = max([host.body_w] + [p.body_w for p in pets])
        half = body / 2.0
        targets = gather_targets(host.x,
                                 [p.x for p in pets],
                                 [p.body_w for p in pets],
                                 host.body_w,
                                 body * GATHER_GAP,
                                 half, max(half, self.sw - half))
        for r, x in zip(roles, targets):
            self.pets[r].start_crawl(x, host.y)
        return roles

    def on_event(self, m):
        kind = m.get('kind')
        if kind == 'game':
            # 小游戏的结果是一条独立的消息，不参与动作表 / TIP / 镜像。
            self.on_game_event(m)
            return
        if kind == 'fx':
            self.on_fx_event(m)
            return
        spec = get(kind)
        actor = m.get('from')
        if spec is None or actor not in self.pets:
            return
        target = m.get('to') if isinstance(m.get('to'), str) else None
        key = CONV_ALL if not target else actor
        text = (m.get('text') or '').strip()
        mt = m.get('mt') if isinstance(m.get('mt'), str) else 'text'
        img = m.get('img') if isinstance(m.get('img'), str) else ''

        if not self.remote_on and kind != 'say':
            # 关了「同步远程互动」：桌面一点不动。但聊天记录里仍旧留一条 ——
            # 否则对方以为没发出去，我这边事后也说不清刚才为什么没反应。
            # 传话（kind='say'）不受影响：那是聊天消息，不是互动动作。
            self._log_tip(kind, ROLE_NAME.get(actor, actor), key)
            return

        self._play(self.pets[actor], self._what(spec), spec.get('dur'))
        if kind == 'say':
            self.say_role(actor, (text if mt == 'text' else '')
                          or media_line(mt, actor))
            self._accept_say(actor, mt, text, img, m, key)
        else:
            self.say_role(actor, text or say_line(kind, actor))
            self._log_tip(kind, ROLE_NAME.get(actor, actor), key)

        me = self.me['role'] if self.me else None
        if spec.get('gather') and me and me != actor \
                and (not target or target == me):
            # 集合没有 mirror 姿势：广播时我这边所有别人都爬向发起人，
            # 点名时只有我自己爬过去 —— 两边屏幕上演的是同一件事。
            moved = self._gather(actor, [me] if target else None)
            if me in moved:
                reply = reply_line(kind, me)
                if reply:
                    self.say_role(me, reply)
            return

        mirror = spec.get('mirror')
        if mirror and me and me != actor:
            # 对全员：桌上所有别人都跟着做（包括我自己），每个客户端演的是
            # 同一件事 —— 不管看哪块屏幕，都是「一屋子人一起做」。
            # 指定某个人：只有被点到的那位跟着做，别人站着。
            if target:
                peers = [me] if target == me else []
            else:
                peers = [r for r in self.pets if r != actor]
            for r in peers:
                if r in self.pets:
                    self._play(self.pets[r], mirror, spec.get('dur'))
            if me in peers:
                reply = reply_line(kind, me)
                if reply:
                    self.say_role(me, reply)

    # ---------------- 认领 ----------------

    def _claim_soon(self, note=''):
        """延迟一拍再弹认领窗 —— 不要在 tick 的调用栈里开模态窗口。"""
        if self._claim_open or self._closing or self.me or self._pending_role:
            return
        if self.offline:
            # 连不上的时候，「为什么点了没反应」比「先认个身份」更该被看到：
            # 认领窗是此刻唯一亮着的窗口，状态只能先说在这儿。
            note = self.offline_note()
        self._claim_open = True
        self._note = note
        QTimer.singleShot(1, self._open_claim)

    def _open_claim(self):
        if self._closing:
            self._claim_open = False
            return
        if self.me or self._pending_role:
            self._claim_open = False
            return
        dlg = ClaimDialog(None, self.players, self._note,
                          on_poll=self._poll_for_dialog)
        self._claim_dlg = dlg
        self._note = ''
        role = None
        try:
            if dlg.exec():
                role = dlg.role
        finally:
            self._claim_dlg = None
            self._claim_open = False
            # exec() 刚返回时 Qt 还在这个对话框的调用栈上，别在这儿析构它。
            # 留到下一次认领覆盖，进程结束前不再碰。
            self._claim_prev = dlg
        if not role:
            return self.quit()
        self._pending_role = role
        self._pending_sent = False

    def _poll_for_dialog(self):
        for m in self.net.poll():
            if m.get('t') == 'state':
                self.players = m.get('players') or {}
                self._state_seen = True
                if self._claim_dlg is not None:
                    self._claim_dlg.set_players(self.players)
            else:
                self._stash.append(m)

    # ---------------- 角色 ----------------

    def sync_pets(self):
        for role in ROLES:
            if self.players.get(role) and role not in self.pets:
                self._spawn(role)
        for role in list(self.pets):
            if not self.players.get(role):
                self.pets.pop(role).destroy()

    def _spawn(self, role):
        sp = self.sprites.get(role)
        if sp is None:
            return
        i = ROLES.index(role)
        x = self.sw * (i + 1) / (len(ROLES) + 1)
        win = windows.PetWindow(self, sp)
        win.role = role
        pet = Pet(sp, win, x, self.sh - GROUND_MARGIN, drop=True)
        self.pets[role] = pet

    def _clear_pets(self):
        self._drag = None
        for p in self.pets.values():
            p.destroy()
        self.pets.clear()
        self._clear_bubbles()
        self._say_on_land = None

    def _clear_bubbles(self):
        for b in self.bubbles.values():
            b.destroy()
        self.bubbles.clear()
        self._bubble_until.clear()

    # ---------------- 拖动 / 菜单 ----------------

    @staticmethod
    def _role_of(win):
        return getattr(win, 'role', None)

    def on_pet_press(self, win, pos):
        if self._swallow_menu_click():
            return
        role = self._role_of(win)
        pet = self.pets.get(role)
        if pet is None:
            return
        self._drag = {'role': role, 'x0': pos.x(), 'y0': pos.y(),
                      'dx': pet.x - pos.x(), 'dy': pet.y - pos.y(),
                      'moved': False}

    def on_pet_drag(self, win, pos):
        d = self._drag
        if not d or d['role'] != self._role_of(win):
            return
        pet = self.pets.get(d['role'])
        if pet is None:
            self._drag = None
            return
        if not d['moved']:
            if (abs(pos.x() - d['x0']) <= DRAG_SLOP
                    and abs(pos.y() - d['y0']) <= DRAG_SLOP):
                return
            d['moved'] = True
            pet.grab()
        x, y = self._clamp(pet, pos.x() + d['dx'], pos.y() + d['dy'])
        pet.move_to(x, y)
        b = self.bubbles.get(d['role'])
        if b is not None:
            b.follow(*pet.bubble_anchor())

    def on_pet_drop(self, win, pos):
        d = self._drag
        self._drag = None
        if not d or d['role'] != self._role_of(win):
            return
        pet = self.pets.get(d['role'])
        if pet is None:
            return
        if d['moved']:
            pet.release()
            return
        if self.me and d['role'] == self.me['role']:
            # 点哪儿反应不一样：头顶摸头、肚子吓一跳、脚边轻拍。
            zone = pos.y() / max(1.0, float(win.win_h))
            self.on_pet_click(zone)

    def _clamp(self, pet, x, y):
        half = pet.win.win_w / 2.0
        x = min(max(x, half), max(half, self.sw - half))
        y = min(max(y, pet.win.win_h), self.sh - GROUND_MARGIN)
        return x, y

    def on_pet_click(self, zone=0.35):
        """点自己那只：睡着就戳醒，否则按部位给不同反应。

        这一下是本机的悄悄话，不发 event —— 菜单里的「摸摸头」才是会传给
        别人的指令。不然手滑点一下自己，六个人的屏幕都得跟着跳。

        同一个部位连着点三下会触发一句「被戳烦了」的回应（复用 `.me` 台词），
        给「一直点」这件事一个小小的反馈。
        """
        role = self.me['role'] if self.me else None
        pet = self.pets.get(role) if role else None
        if pet is None or pet.held:
            return
        if self._sit_due:
            self._sit_due = False
            pet.act('jump', 1.0)
            self.say_role(role, '起来动动')
            return
        if pet.pose == 'sleep':
            pet.act('jump', 1.0)
            self.say_role(role, say_line('wake', role))
            return

        if zone < 0.38:
            part, key = 'head', 'pet'
        elif zone < 0.72:
            part, key = 'belly', 'jump'
        else:
            part, key = 'feet', 'pat'

        now = time.monotonic()
        hist = [(z, t) for z, t in self._taps.get(role, []) if now - t < 4.0]
        hist.append((part, now))
        self._taps[role] = hist[-3:]
        if len(hist) >= 3 and all(z == part for z, _t in hist[-3:]):
            self._taps[role] = []
            pet.act('jump', 1.3)
            self.say_role(role, reply_line(key, role) or say_line('wake', role))
            return

        pose, dur = {'head': ('wave', 1.2), 'belly': ('jump', 1.0),
                     'feet': ('knock', 1.2)}[part]
        pet.act(pose, dur)
        self.say_role(role, say_line(key, role))

    def on_pet_menu(self, win, pos):
        role = self._role_of(win)
        me = self.me['role'] if self.me else None
        if role == me:
            menu = self.build_self_menu()
        else:
            menu = self.build_other_menu(role)
        self._popup(menu, pos)

    def _popup(self, menu, pos):
        self._close_menu()
        # 旧菜单多留一拍：覆盖 self._menu 本身就可能发生在上一次点击的
        # 派发栈里，直接丢引用会让 Qt 用着已经被 shiboken 删掉的对象。
        self._menu_prev, self._menu = self._menu, menu
        menu.aboutToHide.connect(self._menu_closed)
        menu.popup(pos if isinstance(pos, QPoint) else QPoint(*pos))
        self._menu_open = True

    def _close_menu(self):
        m = self._menu
        if m is not None and self._menu_open:
            m.close()               # 触发 aboutToHide → _menu_closed
        self._menu_open = False
        # 不清 self._menu、也不 deleteLater：菜单可能正在派发 triggered。

    def _menu_closed(self):
        # 点菜单项时 Qt 先发 aboutToHide 再发 triggered。这里一旦把
        # self._menu 清成 None，QMenu 的 Python 引用就归零，C++ 对象会在
        # Qt 自己的点击栈中间被删掉 → 堆损坏。只记「关了」，不清对象。
        self._menu_open = False

    def _swallow_menu_click(self):
        if not self._menu_open or self._menu is None:
            return False
        self._close_menu()
        return True

    # ---- 菜单构建 ----

    def _fill_actions(self, menu, to):
        # 「传个话…」放最上面：从桌面上右键的主要目的就是开聊
        for key in ['say'] + [k for k in ORDER if k != 'say']:
            spec = get(key)
            act = menu.addAction(spec['label'])
            act.setToolTip(spec.get('hint', ''))
            act.triggered.connect(lambda _=False, k=key, t=to: self.do_action(k, t))

    def _toy_actions(self, menu, role, to):
        """逗一逗 / 烟花：两条普通菜单项，跟在动作后面，不另起分组。"""
        toy = menu.addAction('逗一逗')
        toy.triggered.connect(lambda _=False, r=role: self.start_toy(r))
        fx = menu.addAction('烟花')
        fx.triggered.connect(lambda _=False, t=to: self.play_fireworks(t))

    def build_self_menu(self):
        me_role = self.me['role'] if self.me else None
        m = QMenu()
        a = m.addAction('%s（我）→ 全员' % ROLE_NAME.get(me_role, ''))
        a.setEnabled(False)
        m.addSeparator()
        self._fill_actions(m, None)
        self._toy_actions(m, me_role, None)
        if self.offline:
            m.addSeparator()
            act = m.addAction('连不上服务器 · 正在重试')
            act.setEnabled(False)
        m.addSeparator()
        m.addAction('换一个身份').triggered.connect(self.do_leave)
        m.addAction('退出').triggered.connect(self.quit)
        return m

    def build_other_menu(self, role):
        m = QMenu()
        a = m.addAction('%s → 只对他' % ROLE_NAME.get(role, role))
        a.setEnabled(False)
        m.addSeparator()
        self._fill_actions(m, role)
        self._toy_actions(m, role, role)
        return m

    # ---- 执行 ----

    def do_action(self, key, to=None):
        """下一条指令：我做一个动作，同时把这条 event 发出去。

        没有冷却，也不排队 —— 连点就是连点，后一个动作直接顶掉前一个
        （Pet.act 只改姿势和一个计时器，不存在两个动作交错）。

        `to` 为 None 表示对全员：每个人屏幕上我做这个动作，他自己跟着做
        mirror；指定某个人时只有他跟着做。两边屏幕看到的是**同一件事**，
        所以 mirror 在我这儿也要演一遍 —— 不然我这边只有自己在动，
        看起来像「发没发出去」都不知道。
        """
        spec = get(key)
        me = self.me['role'] if self.me else None
        if spec is None or me is None:
            return
        if key == 'say':
            return self.open_chat(to)

        pet = self.pets.get(me)
        if pet is None:
            return
        if to:
            self.net.send(t='event', kind=key, text='', to=to)
        else:
            self.net.send(t='event', kind=key, text='')
        self._play(pet, self._what(spec), spec.get('dur'))
        # 对方那边他自己会做这个动作。我这边也得看得见 —— 否则
        # 「拍一下他会跳」只有他自己知道，发起的人反而什么都没看到。
        # 对全员时收信的是所有人，所以这边所有别人的角色都跟着做。
        if spec.get('gather'):
            # 集合是位移不是姿势：别的角色朝我爬过来，点名时只有那一位动。
            self._gather(me, [to] if to else None)
        else:
            mirror = spec.get('mirror')
            if mirror:
                peers = [to] if to else [r for r in self.pets if r != me]
                for r in peers:
                    if r in self.pets:
                        self._play(self.pets[r], mirror, spec.get('dur'))
        self.say_role(me, say_line(key, me))
        self._log_tip(key, '你', self._conv_key(to),
                      target=ROLE_NAME.get(to, to) if to else None)

    # ---------------- 小游戏 ----------------

    def _send_game(self, to, text):
        msg = {'t': 'event', 'kind': 'game', 'text': text}
        if to:
            msg['to'] = to
        self.net.send(**msg)

    def play_game(self, key, to=None):
        """掷骰子：结果在发起端算一次、随 event 发出去，所有人看到同一个数。"""
        me = self.me['role'] if self.me else None
        if me is None or key != 'dice':
            return
        if to and not self.players.get(to):
            if self.chat is not None:
                self.chat.set_note('「%s」不在线' % ROLE_NAME.get(to, to))
            return
        conv = self._conv_key(to)
        pet = self.pets.get(me)
        n = random.randint(1, 6)
        self._send_game(to, 'dice:%d' % n)
        if pet is not None:
            pet.act('jump', 1.0)
        self.say_role(me, str(n))
        self._log_game(me, '你 掷出了 %d' % n, conv)

    def on_game_event(self, m):
        actor = m.get('from')
        parts = (m.get('text') or '').split(':')
        if actor not in ROLES:
            return
        target = m.get('to') if isinstance(m.get('to'), str) else None
        key = CONV_ALL if not target else actor
        pet = self.pets.get(actor)

        if parts[0] == 'dice' and len(parts) == 2:
            n = parts[1]
            if self.remote_on:
                if pet is not None:
                    self._play(pet, 'jump', 1.0)
                self.say_role(actor, n)
            self._log_game(actor, '%s 掷出了 %s'
                           % (ROLE_NAME.get(actor, actor), n), key)

    def _log_game(self, who, text, key):
        if text:
            self._store(key, {'kind': 'game', 'who': who, 'text': text,
                              'ts': time.time()})

    # ---------------- 烟花 ----------------

    def _show_fireworks(self):
        if self._fx is not None:
            self._fx.stop()
            self._fx_trash.append(self._fx)
            if len(self._fx_trash) > 3:
                self._fx_trash.pop(0)
            self._fx = None
        fx = Fireworks()
        fx.show()
        fx.raise_()
        self._fx = fx

    def play_fireworks(self, to=None):
        """放一场烟花：纯视觉事件，所有人屏幕上各放各的。"""
        me = self.me['role'] if self.me else None
        if me is None:
            return
        if to and not self.players.get(to):
            if self.chat is not None:
                self.chat.set_note('「%s」不在线' % ROLE_NAME.get(to, to))
            return
        msg = {'t': 'event', 'kind': 'fx', 'text': 'fireworks'}
        if to:
            msg['to'] = to
        self.net.send(**msg)
        self._show_fireworks()
        self._log_fx(me, '你 放了烟花', self._conv_key(to))

    def on_fx_event(self, m):
        actor = m.get('from')
        if actor not in ROLES:
            return
        target = m.get('to') if isinstance(m.get('to'), str) else None
        key = CONV_ALL if not target else actor
        if self.remote_on:
            self._show_fireworks()
        self._log_fx(actor, '%s 放了烟花' % ROLE_NAME.get(actor, actor), key)

    def _log_fx(self, who, text, key):
        self._store(key, {'kind': 'fx', 'who': who, 'text': text,
                          'ts': time.time()})

    # ---------------- 聊天 ----------------

    def client_of(self, role):
        """某个身份是网页版还是桌面版；老服务端没有这个字段就返回空。"""
        info = self.players.get(role) if role else None
        return info.get('client') if isinstance(info, dict) else None

    def _conv_key(self, to):
        return CONV_ALL if not to else to

    def open_chat(self, to=None):
        if self._closing or self.me is None:
            return None
        key = self._conv_key(to)
        # 关窗现在只是 hide，窗口对象还活着 —— 复用它，别每次都新建一个。
        if self.chat is None:
            self.chat = ChatWindow(None, self.send_chat, self._chat_closed,
                                   me=self.me['role'],
                                   is_online=lambda r: bool(self.players.get(r)),
                                   client_of=self.client_of,
                                   log=self.chat_log,
                                   on_media=self.send_media,
                                   on_game=self.play_game,
                                   on_toy=self.start_toy,
                                   on_fx=self.play_fireworks,
                                   on_reminder=self.set_reminder,
                                   water_on=self._water_on,
                                   water_min=self._reminder_min,
                                   sit_on=self._sit_on,
                                   sit_min=self._sit_min,
                                   unread=self.unread,
                                   on_action=self.do_action,
                                   on_leave=self.do_leave,
                                   on_quit=self.quit,
                                   on_pet_size=self.set_pet_size,
                                   pet_size=config.PET_SIZE,
                                   remote_on=self.remote_on,
                                   on_remote=self.remote_toggle,
                                   remote_hint=self.remote_hint,
                                   on_update=self.update_action,
                                   update_label=self.update_label,
                                   update_enabled=lambda: update.enabled())
            self.chat.switch(key)
            self.chat.show()
        else:
            self.chat.switch(key)
            self.chat.lift()
        self.chat.focus_input()
        return self.chat

    def _chat_closed(self):
        """面板关了：**别让它被立刻回收**。

        close() 之后 Qt 那边还有一层子控件挂在对象树上，如果 Python 引用
        当场清零，解释器做一次 GC 就踩着野指针（实测退出时段错误）。
        留一份引用在 _trash 里，等下一次开面板再整体放掉。
        """
        if self.chat is not None:
            self._chat_trash.append(self.chat)
            if len(self._chat_trash) > 4:
                self._chat_trash.pop(0)
        self.chat = None

    def send_chat(self, text):
        text = (text or '').strip()
        if not text:
            return ''
        ok, why = self._guard_say()
        if not ok:
            return why
        key = self.chat.key
        to = None if key == CONV_ALL else key
        re_ = self.chat.take_quote()
        self._emit_say(to, text=text, re_=re_)
        self._log_msg(self.me['role'], text, key, re_=re_)
        return ''

    def send_media(self, mt, payload):
        ok, why = self._guard_say()
        if not ok:
            return why
        key = self.chat.key
        to = None if key == CONV_ALL else key
        # 一条消息只带一个引用：多个附件时挂在第一件上
        re_ = self.chat.take_quote()

        if mt == 'emoji':
            if not isinstance(payload, str) or not payload:
                return '这个表情发不出去'
            self._emit_say(to, mt='emoji', text=payload, re_=re_)
            self._log_media(self.me['role'], 'emoji', key, char=payload,
                            re_=re_)
            self._say_media_line(mt)
            return ''

        path = ''
        name = ''
        size = 0
        try:
            if mt == 'file':
                path = payload
                name = os.path.basename(path or '')
                b64, size = media.encode_file(path)
                self._emit_say(to, mt='file', img=b64, name=name, size=size,
                               re_=re_)
            elif isinstance(payload, Image.Image):
                b64, _w, _h = media.encode_pil(payload, side=media.MAX_SIDE)
                self._emit_say(to, mt=mt, img=b64, re_=re_)
            else:
                path = payload
                name = os.path.basename(path or '')
                b64, _w, _h = media.encode_image(
                    path, side=media.MAX_SIDE, png=(mt == 'sticker'))
                self._emit_say(to, mt=mt, img=b64, re_=re_)
        except ValueError as e:
            return str(e)

        self._log_media(self.me['role'], mt, key, img=b64, name=name,
                        size=size, path=path, re_=re_)
        self._say_media_line(mt)
        return ''

    def _say_media_line(self, mt):
        me = self.me['role'] if self.me else None
        line = media_line(mt, me)
        if line:
            self.say_role(me, line)

    def _guard_say(self):
        """能不能说话：得认领了身份、连着服务器、而且会话那头真的在线。"""
        me = self.me['role'] if self.me else None
        if me is None or self.chat is None:
            return False, ''
        key = self.chat.key
        to = None if key == CONV_ALL else key
        if not self.net.connected:
            return False, self.offline_note()
        if to and not self.players.get(to):
            return False, '「%s」不在线，这句发不出去' % ROLE_NAME.get(to, to)
        return True, ''

    def _emit_say(self, to, mt='text', text='', img='', name='', size=0,
                  re_=None):
        msg = {'t': 'event', 'kind': 'say', 'text': text, 'mt': mt}
        if to:
            msg['to'] = to
        if isinstance(re_, dict) and re_.get('text'):
            msg['re'] = re_
        if mt in ('img', 'sticker', 'file'):
            msg['img'] = img
            if mt == 'file':
                msg['name'] = name
                msg['size'] = size
        self.net.send(**msg)
        me = self.me['role'] if self.me else None
        pet = self.pets.get(me) if me else None
        if pet is not None:
            spec = get('say')
            pet.act(spec['pose'], spec['dur'])
        if mt == 'text':
            self.say_role(me, text)

    # ---- 记录 ----

    def _store(self, key, entry):
        log = self.chat_log.setdefault(key, [])
        log.append(entry)
        if len(log) > CHAT_HISTORY:
            del log[:-CHAT_HISTORY]
        me = self.me['role'] if self.me else None
        if (entry.get('kind') in ('msg', 'game', 'fx')
                and entry.get('who') != me
                and entry.get('who') != 'me'
                and not self._watching(key)):
            self.unread[key] = self.unread.get(key, 0) + 1
        self.history.add(key, entry)
        if self.chat is not None:
            self.chat.on_new(key)

    def _watching(self, key):
        c = self.chat
        return c is not None and c.key == key and c.alive()

    def _unread_total(self):
        return sum(int(n or 0) for n in self.unread.values())

    def _latest_unread(self):
        best, best_ts = None, -1.0
        for key, n in self.unread.items():
            if not n:
                continue
            log = self.chat_log.get(key) or []
            ts = log[-1].get('ts', 0) if log else 0
            if ts >= best_ts:
                best, best_ts = key, ts
        return best

    def _log_msg(self, who, text, key=CONV_ALL, re_=None):
        if not text:
            return
        entry = {'kind': 'msg', 'who': who, 'text': text, 'ts': time.time()}
        if isinstance(re_, dict) and re_.get('text'):
            entry['re'] = re_
        self._store(key, entry)

    def _log_tip(self, kind, actor, key, target=None):
        """互动痕迹：**TIP 里有这条指令才记**。

        sleep / jump 这些环境动作不在 TIP 里，tip_text 给空串，这里就此打住 ——
        不留痕没有第二个开关，判断只发生在 actions.TIP 一处。
        """
        text = tip_text(kind, actor, one=(key != CONV_ALL), target=target)
        if not text:
            return
        self._store(key, {'kind': 'tip', 'who': actor, 'text': text,
                          'ts': time.time()})

    def _log_media(self, who, mt, key, img='', name='', size=0, path='',
                   char='', re_=None):
        if mt == 'img':
            text = '[图片]'
        elif mt == 'emoji':
            text = '[表情]'
        elif mt == 'sticker':
            text = '[表情包]'
        else:
            text = '[文件] %s' % (name or '文件')
        # 图片落盘再记路径：历史记录重启后还能看，SQLite 也不会被 base64 撑大
        if mt in ('img', 'sticker') and img:
            saved = media.save_chat_image(img, sticker=(mt == 'sticker'))
            if saved:
                path = saved
        entry = {'kind': 'msg', 'mt': mt, 'who': who, 'text': text,
                 'img': img if mt in ('img', 'sticker') else '',
                 'char': char, 'name': name, 'size': size,
                 'path': path, 'ts': time.time()}
        if isinstance(re_, dict) and re_.get('text'):
            entry['re'] = re_
        self._store(key, entry)

    def _accept_say(self, actor, mt, text, img, m, key):
        re_in = m.get('re')
        re_ = re_in if isinstance(re_in, dict) else None
        if mt == 'emoji' and text:
            self._log_media(actor, 'emoji', key, char=text, re_=re_)
        elif mt in ('img', 'sticker') and img:
            self._log_media(actor, mt, key, img=img, re_=re_)
        elif mt == 'file' and img:
            name = m.get('name') or '文件'
            size = m.get('size') if isinstance(m.get('size'), int) else 0
            path = self._save_incoming(name, img)
            if not path:
                self._log_msg(actor, '（收到一个文件，但存不下来）', key,
                              re_=re_)
                return
            self._log_media(actor, 'file', key, name=name, size=size,
                            path=path, re_=re_)
        else:
            self._log_msg(actor, text, key, re_=re_)

    def _save_incoming(self, name, b64):
        safe = ''.join(ch for ch in str(name or '')
                       if ch not in '\\/:*?"<>|\r\n\t').strip(' .') or 'file'
        stamp = time.strftime('%Y%m%d-%H%M%S')
        base = '%s_%s' % (stamp, safe)
        path = os.path.join(files_dir(), base)
        n = 1
        while os.path.exists(path):
            root, ext = os.path.splitext(base)
            path = os.path.join(files_dir(), '%s_%d%s' % (root, n, ext))
            n += 1
        try:
            with open(path, 'wb') as f:
                f.write(base64.b64decode(b64))
        except (OSError, ValueError):
            return ''
        return path

    # ---------------- 更新 ----------------

    def check_update(self, force=False):
        if not update.enabled():
            return False
        if force:
            self._upd_asked = True
        return self._upd_checker.start(config.VERSION, force=force)

    def _pump_update(self):
        while True:
            try:
                kind, payload = self.upd_q.get_nowait()
            except queue.Empty:
                return
            self._on_update(kind, payload)

    def _on_update(self, kind, payload):
        prev = self.upd_state
        if kind == 'checking':
            self.upd_state = 'checking'
        elif kind == 'found':
            self.upd_state = 'downloading'
            self.upd_info = payload
        elif kind == 'progress':
            self.upd_pct = float(payload or 0)
        elif kind == 'ready':
            self.upd_state = 'ready'
            self.upd_info = payload
            if prev != 'ready':
                ver = payload.get('version')
                if self._upd_asked:
                    self.say_role(self.me['role'] if self.me else None,
                                  '新版 %s 下载好了，正在重启换上' % ver)
                    self._upd_apply_id = QTimer.singleShot(1500, self._apply_update)
                else:
                    self.say_role(self.me['role'] if self.me else None,
                                  '新版 %s 已就绪，右键「更新到 %s」就能换上'
                                  % (ver, ver))
        elif kind == 'latest':
            self.upd_state = 'latest'
            if self._upd_asked:
                self.say_role(self.me['role'] if self.me else None,
                              '已经是最新版啦')
        elif kind == 'error':
            self.upd_state = 'error'
            self.upd_note = payload or ''
            if self._upd_asked:
                self.say_role(self.me['role'] if self.me else None,
                              '更新没成功：%s' % self.upd_note)
        elif kind == 'idle':
            self.upd_state = 'idle'
        if kind in ('ready', 'latest', 'error', 'idle'):
            self._upd_next = time.monotonic() + config.UPDATE_EVERY

    def update_label(self):
        if not update.enabled():
            return '检查更新 · 未配置'
        st = self.upd_state
        ver = (self.upd_info or {}).get('version', '')
        if st == 'ready':
            return '更新到 %s · 重启' % ver
        if st == 'downloading':
            return '正在下载 %s… %d%%' % (ver, int(self.upd_pct * 100))
        if st == 'checking':
            return '正在检查更新…'
        if st == 'found':
            return '发现新版 %s' % ver
        return '检查更新'

    def update_action(self):
        if self.upd_state == 'ready':
            ok, msg = update.apply(relaunch=True)
            if not ok:
                self.upd_state = 'error'
                self.upd_note = msg
                self.say_role(self.me['role'] if self.me else None, msg)
                return
            self.quit()
            return
        if self.upd_state in ('checking', 'downloading', 'found'):
            self.say_role(self.me['role'] if self.me else None, '正在弄呢，稍等')
            return
        self.check_update(force=True)

    def _apply_update(self):
        self._upd_apply_id = None
        if self.upd_state != 'ready':
            return
        self.update_action()

    # ---------------- 托盘 ----------------

    def start_tray(self):
        if self.tray is not None or not tray_wanted():
            return self.tray
        icon, alert = tray_icons(assets_dir())
        t = Tray(icon=icon, alert=alert,
                 tip='pet-club %s' % config.version(),
                 on_left=self.tray_left, on_right=self.tray_right)
        self.tray = t if t.ok else None
        self._tray_tip = ('pet-club %s' % config.version()) if t.ok else ''
        return self.tray

    def _tray_tick(self, now):
        t = self.tray
        if t is None:
            return
        n = self._unread_total()
        if n and t.can_blink:
            if now >= self._tray_at:
                self._tray_on = not self._tray_on
                self._tray_at = now + TRAY_BLINK_SEC
                t.blink(self._tray_on)
        elif self._tray_on:
            self._tray_on = False
            t.blink(False)
        tip = ('pet-club %s · %d 条未读' % (config.version(), n) if n
               else 'pet-club %s' % config.version())
        if tip != self._tray_tip:
            self._tray_tip = tip
            t.set_tip(tip)

    def tray_left(self):
        if self._closing:
            return
        if self.me is None:
            self._claim_soon('先认领一个身份，才能说话')
            return
        key = self._latest_unread() or CONV_ALL
        self.open_chat(None if key == CONV_ALL else key)

    def tray_menu(self):
        """托盘右键菜单。

        只放「能点的东西」：一行说明文字在 QMenu 里会按最长那行把整个菜单
        撑宽，还没法折行 —— 所以「始终置顶」的勾选状态交给 QAction 自己的
        勾，解释文案进 tooltip；版本号在托盘悬停提示里，不占菜单空间。
        """
        m = QMenu()
        m.addAction('打开聊天').triggered.connect(self.tray_left)
        m.addSeparator()
        act = m.addAction('始终置顶')
        act.setCheckable(True)
        act.setChecked(self._top_on)
        act.setToolTip(self.layer_hint())
        act.triggered.connect(self.layer_toggle)
        act = m.addAction('同步远程互动')
        act.setCheckable(True)
        act.setChecked(self.remote_on)
        act.setToolTip(self.remote_hint())
        act.triggered.connect(self.remote_toggle)
        m.addSeparator()
        act = m.addAction(self.update_label())
        act.setEnabled(update.enabled())
        act.triggered.connect(self.update_action)
        if self.upd_state == 'error' and self.upd_note:
            a = m.addAction(self.upd_note)
            a.setEnabled(False)
        if self.me:
            m.addAction('换一个身份').triggered.connect(self.do_leave)
        m.addSeparator()
        m.addAction('退出').triggered.connect(self.quit)
        return m

    def tray_right(self):
        if self._closing:
            return
        self._close_menu()
        self._popup(self.tray_menu(), QPoint(*self._cursor()))

    @staticmethod
    def _cursor():
        from PySide6.QtGui import QCursor
        p = QCursor.pos()
        return p.x(), p.y()

    # ---- 图层 ----

    def layer_hint(self):
        if self._top_on:
            return '角色压在普通窗口和自家聊天框上方；取消勾选后，其他窗口会挡住角色。'
        return '和普通窗口同级，会被其他窗口挡住；勾选后角色始终在最上层。'

    def layer_toggle(self, on=None):
        """切换（或按 on 设定）是否始终置顶，返回切换后的状态。

        菜单里那颗是可勾选 QAction，triggered 会把勾好的状态传进来；
        代码里直接调 layer_toggle() 就是「反过来」。
        """
        on = (not self._top_on) if on is None else bool(on)
        if on == self._top_on:
            return on
        self._top_on = on
        for pet in self.pets.values():
            pet.win.apply_top(on)
        for b in self.bubbles.values():
            b.apply_top(on)
        if self.me:
            self.say_role(self.me['role'],
                          '又回到最上层了' if on else '现在会被别的窗口盖住')
        return on

    def remote_hint(self):
        if self.remote_on:
            return '别人发来的互动会在桌面上同步演一遍；取消勾选后只在聊天记录里留一条。'
        return '已关闭：别人发来的互动不会在桌面上演，仅聊天记录留一条；自己发的照常。'

    def remote_toggle(self, on=None):
        """切换（或按 on 设定）是否同步别人发来的互动，返回切换后的状态。

        只管**收到**的那一侧：指令照样发得出去，聊天记录也照样留痕，
        只是本机桌面不跟着动 —— 开会、演示、录屏时不想被一堆动作打断就关掉。
        选择存进 prefs.json，重启后还是这个状态。

        托盘菜单和聊天窗左下角头像菜单共用这一个入口：两处都只是把勾选
        结果交过来，状态只有 `remote_on` 一份，切完回写给聊天窗。
        """
        self.remote_on = (not self.remote_on) if on is None else bool(on)
        self._prefs['remote_actions'] = self.remote_on
        config.save_prefs(self._prefs)
        if self.chat is not None:
            self.chat.remote_on = self.remote_on
        return self.remote_on

    def do_leave(self):
        if self.me:
            self.net.send(t='leave')
        # 换了身份，聊天窗里的头像、发送目标、历史都不再是这个人的了 ——
        # 面板直接收掉。延后一拍再关：这一下可能是从面板自己的菜单点进来的，
        # 不能在菜单派发 triggered 的栈上把窗户拆了。
        if self.chat is not None:
            QTimer.singleShot(0, self._close_chat)
        self.me = None
        self._pending_role = None
        self._pending_sent = False
        self._clear_pets()
        QTimer.singleShot(
            1200, lambda: self._claim_soon('已释放身份，重新认领一个吧'))

    def set_pet_size(self, level):
        """桌宠大小三档：小 / 默认 / 大。

        窗口尺寸是在 PetWindow 初始化时算死的（位图设备比、蒙版、命中区都
        跟着走），所以改档位就重建角色 —— 它们会重新落一次，看得见发生了什么。
        位置按身份重新等分，不保留刚才拖到的位置。
        """
        if level not in config.PET_SIZE_FACTOR or level == config.PET_SIZE:
            return
        config.PET_SIZE = level
        self._prefs['pet_size'] = level
        config.save_prefs(self._prefs)
        roles = [r for r in ROLES if r in self.pets]
        self._clear_pets()
        for r in roles:
            self._spawn(r)
        if self.chat is not None:
            self.chat.pet_size = level

    def _close_chat(self):
        if self.chat is not None:
            self.chat.close()

    def set_server(self, host, port):
        self.net.stop()
        self._clear_pets()
        self.me = None
        self.players = {}
        self._pending_role = None
        self._pending_sent = False
        self._state_seen = False
        self._claim_open = False
        self._stash = []
        self.host, self.port = host, port
        self.net = Net(host, port)
        self.net.start()
        self._last_hb = 0.0
        self._set_offline(False)

    # ---------------- 状态 ----------------

    def offline_note(self):
        return OFFLINE_NOTE

    def _set_offline(self, on):
        on = bool(on)
        changed = on != self.offline
        self.offline = on
        dlg = self._claim_dlg
        if dlg is not None:
            if on:
                dlg.set_note(self.offline_note())
            elif dlg.note_text() == self.offline_note():
                dlg.set_note('')
        if changed:
            print('[pet-club] %s %s' % (
                '连不上' if on else '已连上',
                config.format_endpoint(self.host, self.port)))

    def say_role(self, role, text, sec=None):
        pet = self.pets.get(role) if role else None
        if not pet or not text:
            return
        b = self.bubbles.get(role)
        if b is None:
            b = self.bubbles[role] = windows.Bubble(None)
        b.show_at(text, *pet.bubble_anchor())
        self._bubble_until[role] = time.monotonic() + (sec or BUBBLE_SEC)

    def toast(self, text):
        QMessageBox.warning(None, 'pet-club', text)

    def quit(self):
        if self._closing:
            return
        self._closing = True
        self._close_menu()
        if self.tray is not None:
            self.tray.close()       # 只 hide：托盘对象留到进程结束再析构
        if self.chat is not None:
            self.chat.hide()
            self._chat_trash.append(self.chat)
        self._timer.stop()
        try:
            self.net.send(t='leave')
        except Exception:
            pass
        self.net.stop()
        self.history.close()
        # 退出时只 hide，不 deleteLater、也不丢引用：deleteLater 之后引用一断，
        # shiboken 和 Qt 会各删一次同一个 C++ 对象。进程马上结束，
        # 这些窗口由系统连同进程一起回收。
        self._win_trash.extend(p.win for p in self.pets.values())
        for p in self.pets.values():
            p.win.hide()
        self._win_trash.extend(self.bubbles.values())
        for b in self.bubbles.values():
            b.hide()
        self.qapp.quit()
