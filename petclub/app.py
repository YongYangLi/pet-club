# -*- coding: utf-8 -*-
"""主程序：网络、动作、冷却、聊天记录、更新、认领流程 + Qt 窗口的生命周期。

线程模型：网络在子线程，主线程的 QTimer 每 33ms 从队列里拉一次消息。
Qt 的槽都在主线程执行，跨线程只走 `queue.Queue`，不碰控件。
"""
import base64
import math
import os
import queue
import sys
import time

from PIL import Image
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from . import config, media, update, windows
from .actions import (ORDER, get, is_local, media_line, reply_line, say_line,
                      tip_text)
from .actors import Pet
from .config import ROLES, ROLE_NAME, assets_dir, files_dir
from .cooldown import Cooldown
from .host import foreground, owner_at, tray_icons, work_area
from .net import Net
from .chat import CONV_ALL, ChatWindow
from .claim import ClaimDialog
from .theme import QSS as UI_QSS
from .tray import Tray
from .sprites import load_all

BUBBLE_SEC = 2.6
GROUND_MARGIN = 34
DRAG_SLOP = 4

CD_SELF = 0.8
CD_PERSON = 2.0
CD_ALL = 5.0

CHAT_HISTORY = 50
TRAY_BLINK_SEC = 0.7

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
        # 全局样式：按钮、输入框、滚动条、弹出菜单。挂在 QApplication 上，
        # 所以弹出去的菜单（不是任何窗口的后代）也一样吃得到。
        self.qapp.setStyleSheet(UI_QSS)

        self.sprites = load_all(assets_dir(), ROLES)
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

        self.cd = Cooldown()
        self._drag = None
        self.offline = False

        self._menu = None
        self._menu_probes = []
        self.chat = None
        self._chat_trash = []       # 已关闭但还不能回收的面板（见 _chat_closed）
        self.chat_log = {}
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
        self._menu_timer = QTimer(self.qapp)
        self._menu_timer.setInterval(200)
        self._menu_timer.timeout.connect(self._refresh_menu)

    # ---------------- 启动 ----------------

    def run(self):
        QTimer.singleShot(120, self.connect)
        QTimer.singleShot(60, self.start_tray)
        if update.enabled():
            QTimer.singleShot(int(config.UPDATE_START_DELAY * 1000),
                              self.check_update)
        self._timer.start()
        self._menu_timer.start()
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
            if self.net.send(t='join', role=self._pending_role):
                self._pending_sent = True

        if self.net.connected and now - self._last_hb >= config.HEARTBEAT_SEC:
            self._last_hb = now
            self.net.send(t='ping')

        for pet in list(self.pets.values()):
            pet.update(dt)

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

    def _keep_top(self, now):
        fg = foreground()
        switched = fg != self._last_fg
        self._last_fg = fg
        chat = self.chat
        if chat is not None and chat.alive():
            chat.follow(fg)

        if not self._top_on or not self.pets:
            return
        if not switched and now - self._top_checked < 3.0:
            return
        self._top_checked = now
        for pet in self.pets.values():
            pt = pet.win.hit_point()
            if pt is None:
                continue
            if owner_at(pt[0], pt[1], pet.win) != 'other':
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
                self.net.send(t='join', role=self.me['role'])
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

    def on_event(self, m):
        kind = m.get('kind')
        spec = get(kind)
        actor = m.get('from')
        if spec is None or is_local(kind) or actor not in self.pets:
            return
        target = m.get('to') if isinstance(m.get('to'), str) else None
        key = CONV_ALL if not target else actor
        text = (m.get('text') or '').strip()
        mt = m.get('mt') if isinstance(m.get('mt'), str) else 'text'
        img = m.get('img') if isinstance(m.get('img'), str) else ''

        self.pets[actor].act(spec['pose'], spec['dur'])
        if kind == 'say':
            self.say_role(actor, (text if mt == 'text' else '')
                          or media_line(mt, actor))
            self._accept_say(actor, mt, text, img, m, key)
        else:
            self.say_role(actor, text or say_line(kind, actor))
            self._log_tip(kind, ROLE_NAME.get(actor, actor), key)

        me = self.me['role'] if self.me else None
        mirror = spec.get('mirror')
        if mirror and me and me != actor and me in self.pets:
            self.pets[me].act(mirror, spec['dur'])
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
            dlg.deleteLater()
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
            self.on_pet_click()

    def _clamp(self, pet, x, y):
        half = pet.win.win_w / 2.0
        x = min(max(x, half), max(half, self.sw - half))
        y = min(max(y, pet.win.win_h), self.sh - GROUND_MARGIN)
        return x, y

    def on_pet_click(self):
        role = self.me['role'] if self.me else None
        pet = self.pets.get(role) if role else None
        if pet is None or pet.held:
            return
        if pet.pose == 'sleep':
            pet.act('jump', 1.0)
            self.say_role(role, say_line('wake', role, scope='l'))
            return
        self.do_action('pet')

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
        self._menu = menu
        self._menu_probes = getattr(menu, '_probes', [])
        menu.aboutToHide.connect(self._menu_closed)
        menu.popup(pos if isinstance(pos, QPoint) else QPoint(*pos))

    def _close_menu(self):
        if self._menu is not None:
            m, self._menu = self._menu, None
            m.close()
            m.deleteLater()
            self._menu_probes = []

    def _menu_closed(self):
        self._menu = None
        self._menu_probes = []

    def _swallow_menu_click(self):
        if self._menu is None:
            return False
        self._close_menu()
        return True

    def _refresh_menu(self):
        """菜单弹着的时候每 200ms 刷一次文案（冷却倒计时）。"""
        if self._menu is None or not self._menu_probes:
            return
        now = time.monotonic()
        for act, key, to, label in self._menu_probes:
            left = self.cd.left(self._cd_key(key, to), now)
            act.setText(self._cd_label(label, left))
            act.setEnabled(left <= 0)

    # ---- 冷却 ----

    def _cd_key(self, key, to):
        if is_local(key):
            return 'local:' + key
        return 'all' if to is None else '%s>%s' % (to, key)

    def _cd_span(self, key, to):
        if is_local(key):
            return CD_SELF
        return CD_ALL if to is None else CD_PERSON

    @staticmethod
    def _cd_label(label, left):
        return label if left <= 0 else '%s（%d 秒）' % (label, math.ceil(left))

    # ---- 菜单构建 ----

    def _fill_actions(self, menu, to):
        probes = []
        for key in ORDER:
            spec = get(key)
            ckey = self._cd_key(key, to)
            left = self.cd.left(ckey, time.monotonic())
            act = menu.addAction(self._cd_label(spec['label'], left))
            act.setEnabled(left <= 0)
            act.triggered.connect(lambda _=False, k=key, t=to: self.do_action(k, t))
            probes.append((act, key, to, spec['label']))
        menu._probes = probes

    def build_self_menu(self):
        players = self.players or {}
        online = sum(1 for v in players.values() if v)
        me_role = self.me['role'] if self.me else None
        m = QMenu()
        a = m.addAction('%s（我）→ 全员' % ROLE_NAME.get(me_role, ''))
        a.setEnabled(False)
        m.addSeparator()
        self._fill_actions(m, None)
        m.addSeparator()
        a = m.addAction('服务器成员 · %d/%d 在线' % (online, len(ROLES)))
        a.setEnabled(False)
        for r in ROLES:
            tag = '（我）' if r == me_role else ('在线' if players.get(r) else '空闲')
            act = m.addAction('%s · %s' % (ROLE_NAME[r], tag))
            act.setEnabled(False)
        if self.offline:
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
        return m

    # ---- 执行 ----

    def do_action(self, key, to=None):
        spec = get(key)
        me = self.me['role'] if self.me else None
        if spec is None or me is None:
            return
        if spec['scope'] == 'local':
            return self._do_local(key, spec, to or me)
        if key == 'say':
            return self.open_chat(to)

        role = me
        pet = self.pets.get(role)
        if pet is None:
            return
        now = time.monotonic()
        ckey = self._cd_key(key, to)
        if not self.cd.ready(ckey, now):
            return
        self.cd.mark(ckey, now, self._cd_span(key, to))
        if to:
            self.net.send(t='event', kind=key, text='', to=to)
        else:
            self.net.send(t='event', kind=key, text='')
        pet.act(spec['pose'], spec['dur'])
        self.say_role(role, say_line(key, role))
        self._log_tip(key, '你', self._conv_key(to),
                      target=ROLE_NAME.get(to, to) if to else None)

    # ---------------- 聊天 ----------------

    def _conv_key(self, to):
        return CONV_ALL if not to else to

    def open_chat(self, to=None):
        if self._closing or self.me is None:
            return None
        key = self._conv_key(to)
        if self.chat is None or not self.chat.alive():
            self.chat = ChatWindow(None, self.send_chat, self._chat_closed,
                                   me=self.me['role'],
                                   is_online=lambda r: bool(self.players.get(r)),
                                   log=self.chat_log,
                                   on_media=self.send_media,
                                   unread=self.unread)
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
        self._emit_say(to, text=text)
        self._log_msg('me', text, key)
        return ''

    def send_media(self, mt, payload, free=False):
        ok, why = self._guard_say(free=free)
        if not ok:
            return why
        key = self.chat.key
        to = None if key == CONV_ALL else key

        if mt == 'emoji':
            if not isinstance(payload, str) or not payload:
                return '这个表情发不出去'
            self._emit_say(to, mt='emoji', text=payload)
            self._log_media('me', 'emoji', key, char=payload)
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
                self._emit_say(to, mt='file', img=b64, name=name, size=size)
            elif isinstance(payload, Image.Image):
                b64, _w, _h = media.encode_pil(payload, side=media.MAX_SIDE)
                self._emit_say(to, mt=mt, img=b64)
            else:
                path = payload
                name = os.path.basename(path or '')
                b64, _w, _h = media.encode_image(
                    path, side=media.MAX_SIDE, png=(mt == 'sticker'))
                self._emit_say(to, mt=mt, img=b64)
        except ValueError as e:
            return str(e)

        self._log_media('me', mt, key, img=b64, name=name, size=size, path=path)
        self._say_media_line(mt)
        return ''

    def _say_media_line(self, mt):
        me = self.me['role'] if self.me else None
        line = media_line(mt, me)
        if line:
            self.say_role(me, line)

    def _guard_say(self, free=False):
        me = self.me['role'] if self.me else None
        if me is None or self.chat is None:
            return False, ''
        key = self.chat.key
        to = None if key == CONV_ALL else key
        if not self.net.connected:
            return False, self.offline_note()
        if to and not self.players.get(to):
            return False, '「%s」不在线，这句发不出去' % ROLE_NAME.get(to, to)
        if free:
            return True, ''
        now = time.monotonic()
        ckey = self._cd_key('say', to)
        left = self.cd.left(ckey, now)
        if left > 0:
            return False, '还要等 %d 秒' % int(math.ceil(left))
        self.cd.mark(ckey, now, self._cd_span('say', to))
        return True, ''

    def _emit_say(self, to, mt='text', text='', img='', name='', size=0):
        msg = {'t': 'event', 'kind': 'say', 'text': text, 'mt': mt}
        if to:
            msg['to'] = to
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
        if (entry.get('kind') == 'msg' and entry.get('who') != 'me'
                and not self._watching(key)):
            self.unread[key] = self.unread.get(key, 0) + 1
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

    def _log_msg(self, who, text, key=CONV_ALL):
        if not text:
            return
        self._store(key, {'kind': 'msg', 'who': who, 'text': text,
                          'ts': time.time()})

    def _log_tip(self, kind, actor, key, target=None):
        text = tip_text(kind, actor, one=(key != CONV_ALL), target=target)
        if not text:
            return
        self._store(key, {'kind': 'tip', 'who': actor, 'text': text,
                          'ts': time.time()})

    def _log_media(self, who, mt, key, img='', name='', size=0, path='',
                   char=''):
        if mt == 'img':
            text = '[图片]'
        elif mt == 'emoji':
            text = '[表情]'
        elif mt == 'sticker':
            text = '[表情包]'
        else:
            text = '[文件] %s' % (name or '文件')
        self._store(key, {'kind': 'msg', 'mt': mt, 'who': who, 'text': text,
                          'img': img if mt in ('img', 'sticker') else '',
                          'char': char, 'name': name, 'size': size,
                          'path': path, 'ts': time.time()})

    def _accept_say(self, actor, mt, text, img, m, key):
        if mt == 'emoji' and text:
            self._log_media(actor, 'emoji', key, char=text)
        elif mt in ('img', 'sticker') and img:
            self._log_media(actor, mt, key, img=img)
        elif mt == 'file' and img:
            name = m.get('name') or '文件'
            size = m.get('size') if isinstance(m.get('size'), int) else 0
            path = self._save_incoming(name, img)
            if not path:
                self._log_msg(actor, '（收到一个文件，但存不下来）', key)
                return
            self._log_media(actor, 'file', key, name=name, size=size, path=path)
        else:
            self._log_msg(actor, text, key)

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

    def _do_local(self, key, spec, role):
        pet = self.pets.get(role)
        if pet is None:
            return
        now = time.monotonic()
        ckey = 'local:' + key
        if not self.cd.ready(ckey, now):
            return
        self.cd.mark(ckey, now, CD_SELF)
        pet.act(spec['pose'], spec['dur'])
        text = say_line(key, role, scope='l')
        if text:
            self.say_role(role, text)

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

    def tray_right(self):
        if self._closing:
            return
        self._close_menu()
        m = QMenu()
        a = m.addAction('pet-club %s · 左键打开聊天' % config.version())
        a.setEnabled(False)
        m.addSeparator()
        m.addAction(self.layer_label()).triggered.connect(self.layer_toggle)
        a = m.addAction(self.layer_hint())
        a.setEnabled(False)
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
        self._popup(m, QPoint(*self._cursor()))

    @staticmethod
    def _cursor():
        from PySide6.QtGui import QCursor
        p = QCursor.pos()
        return p.x(), p.y()

    # ---- 图层 ----

    def layer_label(self):
        return '✓ 始终置顶' if self._top_on else '　 始终置顶'

    def layer_hint(self):
        if self._top_on:
            return ('当前：角色压在所有普通窗口上方。'
                    '点击取消勾选后，其他窗口会挡住角色。')
        return ('当前：和普通窗口同级，会被其他窗口挡住。'
                '点击勾选后，角色始终显示在最上层。')

    def layer_toggle(self):
        on = not self._top_on
        self._top_on = on
        for pet in self.pets.values():
            pet.win.apply_top(on)
        for b in self.bubbles.values():
            b.apply_top(on)
        if self.me:
            self.say_role(self.me['role'],
                          '又回到最上层了' if on else '现在会被别的窗口盖住')
        return on

    def do_leave(self):
        if self.me:
            self.net.send(t='leave')
        self.me = None
        self._pending_role = None
        self._pending_sent = False
        self._clear_pets()
        QTimer.singleShot(
            1200, lambda: self._claim_soon('已释放身份，重新认领一个吧'))

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
        self.cd.reset()
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

    def say_role(self, role, text):
        pet = self.pets.get(role) if role else None
        if not pet or not text:
            return
        b = self.bubbles.get(role)
        if b is None:
            b = self.bubbles[role] = windows.Bubble(None)
        b.show_at(text, *pet.bubble_anchor())
        self._bubble_until[role] = time.monotonic() + BUBBLE_SEC

    def toast(self, text):
        QMessageBox.warning(None, 'pet-club', text)

    def quit(self):
        if self._closing:
            return
        self._closing = True
        self._close_menu()
        if self.tray is not None:
            self.tray.close()
            self.tray = None
        if self.chat is not None:
            # 只藏。close() 会触发 on_close 把引用清空，ChatWindow 立刻被
            # Python 回收，而它的子控件还在 Qt 的对象树上 —— 解释器退出时崩。
            self.chat.hide()
            self.chat = None
        self._timer.stop()
        self._menu_timer.stop()
        try:
            self.net.send(t='leave')
        except Exception:
            pass
        self.net.stop()
        # 退出时只藏不删：deleteLater 之后 C++ 对象已经没了，而 Python 那边
        # 还攥着 wrapper —— 解释器退出、GC 走一遍就是段错误。
        # 进程马上结束，窗口由系统回收。
        for p in self.pets.values():
            p.win.hide()
        self.pets.clear()
        for b in self.bubbles.values():
            b.hide()
        self.bubbles.clear()
        self._bubble_until.clear()
        self.qapp.quit()
