# -*- coding: utf-8 -*-
"""pet-club 主程序。

线程模型（唯一要记住的事）：
  网络跑在子线程，所有 Tk 操作都在主线程的 tick 循环里做。
  网络线程只往队列里塞消息，主线程 poll 出来串行处理 —— Tk 不是线程安全的，
  直接跨线程碰窗口会得到偶发且难复现的崩溃。

这一版的流程：
  打开 → 直连内置服务器（地址写死，没有填地址这一步）→
  服务端告诉你哪些身份已被认领 → 挑一个空着的认领 →
  桌面上出现「这台服务器上所有被认领的角色」。
  别人认领时，他的角色会从天而降落到你的桌面上。

连不上服务器时不做任何弹窗（地址改不了，弹了也没用）：一直按退避重试，
状态只说一句话 —— 还没认领身份时说在认领窗那行提示里，已经在桌面上时说在
角色头顶的气泡里。桌面上除了角色本身不再有任何常驻控件。
界面上任何地方都不出现服务器地址，地址只进控制台。

右键谁，指令就作用在谁身上：右键自己 = 对全员下达，右键别人 = 只对他。
两个菜单逐字相同，唯一的差别是第一行灰字。其中「不出门」的几条
（摸摸头 / 睡一会 / 跳一下 / 发个呆）一个字节都不发：
  左键点角色 = 摸摸头 / 叫醒
  按住任意角色拖动 = 把它挪走，松手会掉回地面
  右键别人点「睡一会」= 他那只在我屏幕上睡下，他本人毫不知情
"""

import base64
import math
import os
import queue
import time
import tkinter as tk
from tkinter import messagebox

from PIL import Image

from . import config, media, petwin, tray, update
from .actions import (ORDER, get, is_local, media_line, reply_line, say_line,
                      tip_text)
from .actors import Pet
from .chat import CONV_ALL, ChatWindow
from .config import (ROLES, ROLE_NAME, SERVER_HOST, SERVER_PORT, assets_dir,
                     files_dir)
from .cooldown import Cooldown
from .menu import PopMenu
from .net import Net
from .petwin import Bubble
from .sprites import load_all
from .surface import foreground, owner_at, work_area
from .ui import ClaimDialog

BUBBLE_SEC = 2.6
GROUND_MARGIN = 34          # 脚底离任务栏上沿的距离
DRAG_SLOP = 4               # 移动超过这么多像素才算拖动（否则当单击）

# 冷却：自己玩只要防手抖连点；对一个人要防刷屏；对全员得让别人喘口气
CD_SELF = 0.8
CD_PERSON = 2.0
CD_ALL = 5.0

CHAT_HISTORY = 50           # 传话窗留多少条历史（只在内存里，退出就没了）
TRAY_BLINK_SEC = 0.7        # 有未读时托盘图标多久换一次（再快像故障，再慢不像提醒）

OFFLINE_NOTE = '连不上服务器，正在重试…'    # 没有角落入口了，状态只能靠这句话说


def endpoint(host=None, port=None):
    """不传 = 连内置服务器。传参只有一个用途：探针在本机起临时服务端。"""
    return (SERVER_HOST if host is None else host,
            SERVER_PORT if port is None else port)


def tray_wanted():
    """要不要挂托盘图标。

    默认要 —— 它是关掉窗口以后唯一的入口。探针一次起好几个 App，
    每个都往系统托盘里塞一个图标再撤掉，屏幕右下角会闪个不停，所以留了
    一个环境变量让它们安静：PETCLUB_NO_TRAY=1。
    """
    return os.environ.get('PETCLUB_NO_TRAY') != '1'


class App:

    def __init__(self, host=None, port=None):
        host, port = endpoint(host, port)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title('pet-club')
        self.root.protocol('WM_DELETE_WINDOW', self.quit)

        self.sprites = load_all(assets_dir(), ROLES)
        left, top, right, bottom = work_area()
        self.sw = right - left
        self.sh = bottom - top

        self.host = host
        self.port = port
        self.net = Net(host, port)
        self.bubbles = {}               # role -> Bubble，每个角色各挂各的气泡
        self._bubble_until = {}         # role -> 气泡消失的时刻

        self.me = None                  # {'role': 'p2'}
        self.players = {}               # 服务端推来的身份占用快照
        self.pets = {}                  # role -> Pet

        self._pending_role = None       # 已经选了身份、等 joined
        self._pending_sent = False
        self._claim_open = False
        self._claim_dlg = None
        self._note = ''
        self._state_seen = False
        self._stash = []
        self._last_hb = 0.0
        self._last_t = time.monotonic()
        self._say_on_land = None
        self._tick_id = None
        self._closing = False
        self._last_fg = None            # 上一次看到的前台窗口：变了就说明切了应用
        self._top_checked = 0.0         # 上一次置顶巡检的时刻

        self.cd = Cooldown()            # 动作冷却（纯本地，防连点）
        self._drag = None               # 正在拖的角色：{'role','x0','y0','dx','dy','moved'}
        self.offline = False            # 连不上服务器：认领窗和气泡会说出来

        self._menu = None               # 当前弹出的右键菜单（同一时刻只有一份）
        self.chat = None                # 聊天面板（关掉置 None，历史留在下面）
        # 聊天记录：键 = 'all'（群聊）或角色 id，值是条目列表。
        # {'kind': 'msg'|'tip', 'who': 'me'|角色, 'text': str, 'ts': float}
        # 消息条目还可能带 mt（text/img/sticker/file）、img（base64）、name/size/path（文件）。
        # 只有一个群聊 + 每人一个单聊，所以键就是会话本身 —— 记录不会串台。
        self.chat_log = {}
        # 未读：键 = 会话，值 = 条数。**由 App 持有**，聊天面板只是画红点
        # （面板可以关掉，未读不能跟着没；托盘闪烁读的也是这一份）。
        self.unread = {}
        # 托盘闪烁的节拍：_tray_on 是「这一拍显示未读图标吗」，
        # _tray_at 是下一次该换图的时刻。没有未读时这两个都不参与。
        self._tray_on = False
        self._tray_at = 0.0
        self._tray_tip = ''

        # 自动更新：后台线程查/下，主线程只负责显示和换。
        # 队列是这两边唯一的桥 —— 和网络层同一个纪律，子线程一个 Tk 对象都不碰。
        self.upd_state = 'idle'
        self.upd_info = None
        self.upd_pct = 0.0
        self.upd_note = ''
        self.upd_q = queue.Queue()
        self._upd_asked = False
        self._upd_next = 0.0
        self._upd_apply_id = None
        self._upd_checker = update.Checker(lambda k, p: self.upd_q.put((k, p)))

        # 托盘图标：所有窗口都关掉以后，它是唯一还能操作的入口
        # （左键 = 聊天，右键 = 菜单，见下面的「托盘」一节）。
        self.tray = None

    # ---------------- 启动与主循环 ----------------

    def run(self):
        self._schedule()
        self.root.after(120, self.connect)
        # 托盘跟着启动就挂上：它不依赖网络，也不依赖认领 —— 认领窗开着的时候
        # 人就已经能从这里退出、切图层了。
        self.root.after(60, self.start_tray)
        # 更新放最后排：先把身份认了、角色落地了，再谈升级。
        # （启动瞬间去拉 HTTP，碰上网络慢就会拖住第一次界面。）
        if update.enabled():
            self.root.after(int(config.UPDATE_START_DELAY * 1000),
                            self.check_update)
        self.root.mainloop()

    def connect(self):
        """直接连内置服务器 —— 没有填地址这一步，界面上也不显示地址。

        连不上的处理：一直按退避重试，并且把「连不上服务器，正在重试」
        说在当下唯一亮着的窗口上（认领窗那行提示，或角色头顶的气泡）。
        没有对话框要用户点，也不需要用户做什么。
        """
        if self._closing:
            return
        self.net.start()

    def _schedule(self):
        if not self._closing:
            self._tick_id = self.root.after(config.POLL_MS, self.tick)

    def tick(self):
        self._tick_id = None
        if self._closing:
            return
        if self.tray is not None:
            self.tray.poll()            # 托盘上的点击（WndProc 只入队，这儿才执行）
        now = time.monotonic()
        dt = now - self._last_t
        self._last_t = now
        if dt > 0.25:
            dt = 0.25                   # 掉帧时别让动画一次跳太远

        for m in self._stash + self.net.poll():
            self.on_msg(m)
        self._stash = []

        self._pump_update()
        self._tray_tick(now)            # 未读：闪图标 / 改悬停提示
        # 后台每三小时自己看一次（下一次的时间在 _on_update 里定）
        if update.enabled() and self._upd_next and now >= self._upd_next:
            self._upd_next = now + config.UPDATE_EVERY
            self.check_update()

        # 选了身份但当时还没连上：连上后补发
        if self._pending_role and not self._pending_sent and self.net.connected:
            if self.net.send(t='join', role=self._pending_role):
                self._pending_sent = True

        # 心跳不分「有没有身份」。服务端给每条连接设了 IDLE_TIMEOUT（90 秒不
        # 发数据就断），而认领界面是模态的 —— 人可以盯着四个立绘挑上一分钟。
        # 从前这里挂着 `self.me and`，等于是「还没认领就不许说话」，于是刚连上
        # 就坐在认领窗里的人，90 秒后必被服务端当僵尸踢掉：界面跳「连不上服务器，
        # 正在重试」，接着重连、再被踢，一直循环。认领本身还能成功（重连后补发
        # join），所以症状是「提示连不上，但点了却能进」—— 非常容易误判成网络问题。
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

        self._schedule()

    def _keep_top(self, now):
        """置顶巡检：被别的程序压住时，把宠物重新插回置顶带最前面。

        为什么光靠 `-topmost` 不够：Windows 的置顶是一整条**共享的带子**，
        所有置顶窗口挤在里面，谁最后被激活谁排前面。我们只在造窗口那一刻插过
        一次队，之后浏览器、资源管理器、任何自己置顶的程序一被激活就插到我们
        前面，而且再没有人把我们放回去 —— 症状就是「勾着始终置顶，还是被盖住」。

        所以这里隔一会儿问一句「我还露着吗」：切了前台窗口就立刻问，
        平时每 3 秒兜底一次。问的时候拿角色身上一个不透明像素做命中测试：
          · 点到自己 → 露着，什么都不用做（绝大多数时候是这个，不开销）
          · 点到同一个进程的别的窗口（聊天面板、右键菜单、别的宠物）→ 也不动，
            否则宠物会爬到聊天窗口上面去
          · 点到别人的程序 → 确实被压了，重新插队

        同一趟里还要管聊天面板的层级。面板以前是「永远置顶」，结果用户点了
        微信也翻不上来。现在它只在自己是前台窗口时置顶，切走就让位（见
        ChatWindow.follow）—— 这件事和「始终置顶」那个开关无关，所以放在
        前面，不受下面那几个 return 影响。
        """
        fg = foreground()
        switched = fg != self._last_fg
        self._last_fg = fg
        chat = getattr(self, 'chat', None)
        if chat is not None and chat.alive():
            chat.follow(fg)

        if not petwin.TOP_ON['on'] or not self.pets:
            return
        if not switched and now - self._top_checked < 3.0:
            return
        self._top_checked = now
        for pet in self.pets.values():
            pt = pet.win.hit_point()
            if pt is None:
                continue
            if owner_at(pt[0], pt[1], pet.win.surface.hwnd) != 'other':
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
                # 重连后恢复身份；万一被占了，服务端会回 reject
                self.net.send(t='join', role=self.me['role'])
            else:
                self.net.send(t='query')
        elif t == '_down':
            # 还没认领身份时没有任何角色能冒泡，状态先说在认领窗那行提示里
            self._set_offline(True)
            if self.me:
                self.say_role(self.me['role'], m.get('msg', '和服务器断开了'))

    def on_state(self, m):
        self.players = m.get('players') or {}
        self._state_seen = True

        # 有人进 / 有人走：聊天窗开着的话，那个「n/m 在线」要当场跟上
        if self.chat is not None and self.chat.alive():
            self.chat.refresh_state()

        if self.me:
            if not self.players.get(self.me['role']):
                # 身份被服务端释放了（重启 / 被巡查踢掉）：回到认领界面
                self.me = None
                self._clear_pets()
                self._claim_soon('你的身份已被释放，重新认领一个吧')
                return
            self.sync_pets()

        if self._claim_dlg is not None:
            # 认领界面开着的时候，别人先认领了某个身份 —— 卡片实时灰掉
            self._claim_dlg.set_players(self.players)
        elif not self.me and not self._pending_role and not self._claim_open:
            self._claim_soon()

    def on_joined(self, m):
        role = m.get('role')
        first = self.me is None
        self.me = {'role': role}
        self._pending_role = None
        self._pending_sent = False
        self.sync_pets()
        if first and role in self.pets:
            self._say_on_land = (role, '我是%s，到位啦～' % ROLE_NAME.get(role, ''))

    def on_reject(self, m):
        self._pending_role = None
        self._pending_sent = False
        if isinstance(m.get('players'), dict):
            self.players = m['players']
        self._claim_soon(m.get('msg', '认领失败，换一个身份吧'))

    def on_error(self, m):
        """服务端驳回了一条消息（附件太大 / 格式不对 / 还没认领身份）。

        这类错误基本都是「我发了东西，但没发出去」，所以优先显示在聊天窗那行
        提示上；窗口没开就冒个泡 —— 只在控制台里说等于没说。
        """
        msg = m.get('msg') or '这条没发出去'
        if self.chat is not None and self.chat.alive():
            self.chat.set_note(msg)
        elif self.me:
            self.say_role(self.me['role'], msg)

    def on_event(self, m):
        """收到互动：他的角色做这个动作，我的角色跟着一起做。

        这样两边看到的是同一件事 —— 他提醒我喝水，我屏幕上他也端着杯子，
        我自己也在喝。单向动作（敲窗）只让他那边演。
        说话口气按各自的角色挑，所以「喝口水」两边说的不是同一句。
        """
        kind = m.get('kind')
        spec = get(kind)
        actor = m.get('from')
        # 不出门的指令不该出现在网络上；真收到了也不理（老版本客户端可能发）
        if spec is None or is_local(kind) or actor not in self.pets:
            return
        # 这条是「对我说的」还是「对大家说的」：服务端带上 to 字段，
        # 点名的只送给收件人，所以收得到就说明我是收件人 —— 会话键就是发件人。
        # 老服务端不带这个字段，当成群发处理，宁可记进群聊也别记错人。
        target = m.get('to') if isinstance(m.get('to'), str) else None
        key = CONV_ALL if not target else actor
        text = (m.get('text') or '').strip()
        mt = m.get('mt') if isinstance(m.get('mt'), str) else 'text'
        img = m.get('img') if isinstance(m.get('img'), str) else ''

        self.pets[actor].act(spec['pose'], spec['dur'])
        if kind == 'say':
            # 他打的那句话进聊天记录：传话是唯一带内容的动作。
            # 图片/表情/文件各有各的落法（文件要先存到本地），见 _accept_say。
            # 表情的 text 就是那个字符，直接塞进 Tk 的气泡会变成豆腐块，
            # 所以显示用的台词一律走 media_line。
            self.say_role(actor, (text if mt == 'text' else '')
                          or media_line(mt, actor))
            self._accept_say(actor, mt, text, img, m, key)
        else:
            self.say_role(actor, text or say_line(kind, actor))
            # 动作留一条系统提示（居中灰字），不然气泡飘完就没痕迹了
            self._log_tip(kind, ROLE_NAME.get(actor, actor), key)

        me = self.me['role'] if self.me else None
        mirror = spec.get('mirror')
        if mirror and me and me != actor and me in self.pets:
            self.pets[me].act(mirror, spec['dur'])
            reply = reply_line(kind, me)
            if reply:
                self.say_role(me, reply)

    # ---------------- 认领流程 ----------------

    def _claim_soon(self, note=''):
        """延迟一拍再弹认领窗 —— 不要在 tick 的调用栈里开模态窗口。"""
        if self._claim_open or self._closing or self.me or self._pending_role:
            return
        if not note and self.offline:
            # 还没连上：认领窗是此刻唯一亮着的窗口，状态只能先说在这儿
            note = self.offline_note()
        self._claim_open = True
        self._note = note
        self.root.after(1, self._open_claim)

    def _open_claim(self):
        if self._closing:
            self._claim_open = False
            return
        if self.me or self._pending_role:
            self._claim_open = False
            return
        dlg = ClaimDialog(self.root, self.players, self._note,
                          on_poll=self._poll_for_dialog)
        self._claim_dlg = dlg
        self._note = ''
        role = None
        try:
            role = dlg.show()
        finally:
            self._claim_dlg = None
            self._claim_open = False
        if not role:
            return self.quit()
        self._pending_role = role
        self._pending_sent = False

    def _poll_for_dialog(self):
        """认领窗开着时主循环是停的，由窗口定时回调这里拉一次网络消息。

        只处理 `state`（让卡片实时变色），其余消息先存进 _stash，
        等主循环恢复后按原顺序处理 —— 这样「开着窗等别人让位」也能等得到。
        """
        for m in self.net.poll():
            if m.get('t') == 'state':
                self.players = m.get('players') or {}
                self._state_seen = True
                if self._claim_dlg is not None:
                    self._claim_dlg.set_players(self.players)
            else:
                self._stash.append(m)

    # ---------------- 桌面上的角色 ----------------

    def sync_pets(self):
        """按服务端快照对齐桌面：多出来的落地，没了的移除。"""
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
        pet = Pet(self.root, sp, x, self.sh - GROUND_MARGIN, drop=True)
        self.pets[role] = pet
        self._bind_pet(role, pet)

    def _bind_pet(self, role, pet):
        """鼠标绑定：左键拖动（松手看情况算不算单击），右键出菜单。

        拖动是**纯本地**的：只挪自己屏幕上的窗口，不发任何消息。
        所以每个人看到的布局可以各不相同，谁也不影响谁。
        """
        top = pet.win.top
        top.configure(cursor='hand2')
        top.bind('<Button-1>', lambda e, r=role: self.on_pet_press(r, e))
        top.bind('<B1-Motion>', lambda e, r=role: self.on_pet_drag(r, e))
        top.bind('<ButtonRelease-1>', lambda e, r=role: self.on_pet_drop(r, e))
        if self.me and role == self.me['role']:
            top.bind('<Button-3>', self.on_pet_menu)
        else:
            # 右键别人的角色 = 直接对他互动，目标不用再选一遍
            top.bind('<Button-3>',
                     lambda e, r=role: self.on_other_menu(r, e))

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

    # ---------------- 互动 ----------------

    # ---------------- 拖动：只有自己看得见的玩法 ----------------

    def on_pet_press(self, role, event):
        """按下只记账，先不动 —— 还没法判断这是单击还是拖动。

        菜单开着的时候这一下先用来收菜单：桌宠是 overrideredirect 窗口，点它不会
        让菜单失焦，不拦一道就会「关菜单」和「摸摸头」一起发生。
        """
        if self._swallow_menu_click():
            return
        pet = self.pets.get(role)
        if pet is None:
            return
        self._drag = {'role': role,
                      'x0': event.x_root, 'y0': event.y_root,
                      'dx': pet.x - event.x_root,
                      'dy': pet.y - event.y_root,
                      'moved': False}

    def on_pet_drag(self, role, event):
        d = self._drag
        if not d or d['role'] != role:
            return
        pet = self.pets.get(role)
        if pet is None:
            self._drag = None
            return
        if not d['moved']:
            # 抖一下不算拖 —— 否则「摸摸头」会顺手把它挪歪几个像素
            if (abs(event.x_root - d['x0']) <= DRAG_SLOP
                    and abs(event.y_root - d['y0']) <= DRAG_SLOP):
                return
            d['moved'] = True
            pet.grab()              # 到这一步才真的抓起来（姿势换成 drag.png）
        x, y = self._clamp(pet, event.x_root + d['dx'], event.y_root + d['dy'])
        pet.move_to(x, y)
        b = self.bubbles.get(role)
        if b is not None:
            b.follow(*pet.bubble_anchor())

    def on_pet_drop(self, role, event=None):
        """松手。拖过 = 撒手让它掉下去；没拖 = 单击。"""
        d = self._drag
        self._drag = None
        if not d or d['role'] != role:
            return
        pet = self.pets.get(role)
        if pet is None:
            return
        if d['moved']:
            pet.release()           # 从当前位置自由落体，落地回到 idle
            return
        if self.me and role == self.me['role']:
            self.on_pet_click(event)

    def _clamp(self, pet, x, y):
        """别把人拖到屏幕外面 —— 拖出去就再也抓不回来了。"""
        half = pet.win.win_w / 2.0
        x = min(max(x, half), max(half, self.sw - half))
        y = min(max(y, pet.win.win_h), self.sh - GROUND_MARGIN)
        return x, y

    def on_pet_click(self, event=None):
        """左键点自己的角色 = 摸摸头；睡着的时候点它是叫醒。"""
        role = self.me['role'] if self.me else None
        pet = self.pets.get(role) if role else None
        if pet is None or pet.held:
            return
        if pet.pose == 'sleep':
            pet.act('jump', 1.0)
            self.say_role(role, say_line('wake', role, scope='l'))
            return
        self.do_action('pet')          # 左键 = 对自己的角色下「摸摸头」

    def on_pet_menu(self, event):
        """右键自己：自己玩的动作平铺，加一个「对全员」。"""
        role = self.me['role'] if self.me else None
        self._popup(self.build_self_menu(), event, self._pet_rect(role))

    def on_other_menu(self, role, event):
        """右键别人：菜单就是「对他做什么」，目标由点谁决定，不用再选人。"""
        self._popup(self.build_other_menu(role), event, self._pet_rect(role))

    def _pet_rect(self, role):
        """角色窗口在屏幕上的矩形 —— 菜单要躲开它，别糊在人家脸上。"""
        pet = self.pets.get(role) if role else None
        if pet is None:
            return None
        w, h = pet.win.win_w, pet.win.win_h
        left = int(round(pet.x - w / 2.0))
        return (left, int(round(pet.y - h)), left + w, int(round(pet.y)))

    def _popup(self, menu, event, avoid=None):
        self._close_menu()
        self._menu = menu
        menu.popup(event.x_root, event.y_root, avoid=avoid)

    def _close_menu(self):
        """收起菜单。菜单自己关（Esc / 失焦 / 点了某项）时也会回调到这里。"""
        if self._menu is not None:
            self._menu.close()          # close 会回调 _menu_closed 置空

    def _menu_closed(self):
        self._menu = None

    def _swallow_menu_click(self):
        """菜单开着时点了一下角色：这一下只用来收菜单。

        桌宠窗口是 overrideredirect 的，点它不会让菜单失焦，所以得在这儿拦一道 ——
        不然「点空白处收菜单」会顺手摸一下宠物。
        """
        if self._menu is None:
            return False
        self._close_menu()
        return True

    # ---- 冷却 ----
    #
    # 三个桶，粒度不同：
    #   local:<指令>    不出门的指令 0.8 秒（防手抖连点）
    #   <对方>:<指令>   只对他 2 秒（防刷屏）
    #   all            对全员共用一个 5 秒桶 —— 按指令分桶就绕过去了，
    #                  挨个点「打招呼/喝水/比心」等于没冷却

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
        """冷却中直接写在菜单里 —— 点了没反应是最糟的反馈。"""
        return label if left <= 0 else '%s（%d 秒）' % (label, math.ceil(left))

    def _fill_actions(self, menu, to):
        """一视同仁地铺开全部指令。

        to=None 表示「对全员」（右键自己时），否则是某个身份。
        不出门的那几条不看 to，它们永远只作用在被点中的那个角色身上。

        每一条都挂一个 probe：菜单弹着的时候它每 200ms 重算一次，倒计时会自己
        往下走 —— 这正是 tk.Menu 做不到、必须自己画菜单的原因（见 menu.py）。
        """
        for key in ORDER:
            spec = get(key)
            ckey = self._cd_key(key, to)

            def probe(now, _ckey=ckey, _label=spec['label']):
                left = self.cd.left(_ckey, now)
                return self._cd_label(_label, left), left <= 0

            menu.item(spec['label'],
                      lambda k=key, t=to: self.do_action(k, t),
                      probe=probe)

    # ---- 菜单构建（独立出来才好测：不弹窗也能读到文案和可点状态）----

    def build_self_menu(self):
        """右键自己 = 对全员下达指令。菜单和右键别人时一模一样。"""
        players = self.players or {}
        online = sum(1 for v in players.values() if v)
        me_role = self.me['role'] if self.me else None

        m = PopMenu(self.root, on_close=self._menu_closed)
        m.head('%s（我）→ 全员' % ROLE_NAME.get(me_role, ''))
        m.sep()
        self._fill_actions(m, None)

        m.sep()
        # 这里只列人，不写地址：地址是内部细节，界面上不露出任何一段。
        # 但「现在连没连上」得留着 —— 离线时 share 类指令会静默无效，
        # 用户至少要能在菜单里看到原因。
        m.head('服务器成员 · %d/%d 在线' % (online, len(ROLES)))
        for r in ROLES:
            if r == me_role:
                tag = '（我）'
            elif players.get(r):
                tag = '在线'
            else:
                tag = '空闲'
            m.head('%s · %s' % (ROLE_NAME[r], tag))
        if self.offline:
            m.head('连不上服务器 · 正在重试')

        m.sep()
        # 更新挪到托盘上了（见「托盘」一节）：这个菜单是「对角色下指令」的地方，
        # 软件自己的维护动作（检查更新、切图层、退出）都归托盘管。
        m.item('换一个身份', self.do_leave)
        m.item('退出', self.quit)
        return m

    def build_other_menu(self, role):
        """右键别人 = 只对他。指令列表和右键自己时完全相同。"""
        m = PopMenu(self.root, on_close=self._menu_closed)
        m.head('%s → 只对他' % ROLE_NAME.get(role, role))
        m.sep()
        self._fill_actions(m, role)
        return m

    # ---- 执行：目标决定作用在谁身上，范围决定要不要发出去 ----

    def do_action(self, key, to=None):
        """唯一的执行入口。to=None 表示对全员。

        右键谁，指令就作用在谁身上 —— 这两件事是同一件事，
        所以菜单里不再需要「自己玩 / 一起玩」那一层选择。
        """
        spec = get(key)
        me = self.me['role'] if self.me else None
        if spec is None or me is None:
            return

        if spec['scope'] == 'local':
            # 不出门：作用在被点中的角色身上（右键自己就是自己）
            return self._do_local(key, spec, to or me)

        if key == 'say':
            # 传话不是「点了就发」的一次性动作：开一个常驻的对话窗，
            # 想说的那句话在窗里输入、可以连着说，历史也留在那儿
            return self.open_chat(to)

        role = me
        pet = self.pets.get(role)
        if pet is None:
            return
        now = time.monotonic()
        ckey = self._cd_key(key, to)
        if not self.cd.ready(ckey, now):
            return                  # 菜单里已经写着还要等几秒，这里只是兜底
        # 先记账再发：同一个毫秒里的第二次点击不能漏过去
        self.cd.mark(ckey, now, self._cd_span(key, to))
        if to:
            self.net.send(t='event', kind=key, text='', to=to)
        else:
            self.net.send(t='event', kind=key, text='')
        # 本地这一半：我的角色先做起来，对方收到后再一起做
        pet.act(spec['pose'], spec['dur'])
        self.say_role(role, say_line(key, role))
        # 聊天记录里也留一条：群发进群聊，点名进那个人的会话
        self._log_tip(key, '你', self._conv_key(to),
                      target=ROLE_NAME.get(to, to) if to else None)

    # ---------------- 聊天面板 ----------------

    def _conv_key(self, to):
        """会话键：'all' 是群聊，否则就是那个人的单聊。"""
        return CONV_ALL if not to else to

    def open_chat(self, to=None):
        """打开聊天面板，并切到对应的会话。to=None = 全体群聊。"""
        if self._closing or self.me is None:
            return None
        key = self._conv_key(to)
        if self.chat is None or not self.chat.alive():
            self.chat = ChatWindow(self.root, self.send_chat, self._chat_closed,
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
        self.chat = None

    def send_chat(self, text):
        """面板里按下发送。发到哪个会话，看当下选中的是哪一个。

        返回空串 = 发出去了；返回一句话 = 没发出去（冷却 / 离线 / 对方不在线），
        窗口把这句话显示在自己那行提示上 —— 静默丢弃是最糟的反馈。
        """
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
        """发表情 / 图片 / 文件。返回 '' = 发出去了，否则是给提示行的一句话。

        为什么不和 send_chat 共用一个函数：那条路是「一句话」，这条路要先读文件、
        缩图、编码，失败点完全不同（文件太大、格式不认、读不出来），
        混在一个函数里会让两边的错误处理互相打架。

        表情是最轻的：`payload` 其实是那个字符，一个字节的图片都不传 ——
        对面用自己机器上的字体渲同一张脸（见 media.emoji_image）。

        `free=True` 表示「这一件不占冷却额度」：Ctrl+V 一次贴进来三张图是**一个**
        动作，第二张不该被自己刚记下的 2 秒冷却挡回去（见 ChatWindow.on_paste）。

        `payload` 有三种形态：表情是字符，普通图片/文件是路径，
        剪贴板里的截图是一张还没落盘的 PIL 图（没有名字，也不需要名字）。
        """
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
                # 表情包是画好的 PNG（有圆角透明），当图片发会多一层白框，所以单独标
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
        """能不能往当前会话发东西。返回 (可以, 不行的一句话)。

        三道闸：没连上、对方不在线、冷却没过。**通过时顺手记下冷却**，
        所以调用它一次就等于「占了一个发言位」，别为了问一句而调两次。
        `free=True` 只查不记：一次贴三张图算一个动作，不能自己把自己挡住。
        """
        me = self.me['role'] if self.me else None
        if me is None or self.chat is None:
            return False, ''
        key = self.chat.key
        to = None if key == CONV_ALL else key
        if not self.net.connected:
            # 没连上就别假装发出去了：那句话留在输入框里，提示行说明原因
            return False, self.offline_note()
        if to and not self.players.get(to):
            # 对不在线的人说话，服务端找不到收件人，消息会凭空消失
            return False, '「%s」不在线，这句发不出去' % ROLE_NAME.get(to, to)
        if free:
            # 一次粘贴拆出来的第 2、3 件：连冷却都不看 —— 它们和第一件是同一个动作，
            # 只是第一件已经把这一格的冷却记上了。看的话会被自己挡住。
            return True, ''
        now = time.monotonic()
        ckey = self._cd_key('say', to)
        left = self.cd.left(ckey, now)
        if left > 0:
            return False, '还要等 %d 秒' % int(math.ceil(left))
        self.cd.mark(ckey, now, self._cd_span('say', to))
        return True, ''

    def _emit_say(self, to, mt='text', text='', img='', name='', size=0):
        """真正发出去的那一条，外加本地这一半（宠物跟着说/动）。"""
        msg = {'t': 'event', 'kind': 'say', 'text': text, 'mt': mt}
        if to:
            msg['to'] = to
        # 只有带图的三种才传 img；表情是一个字符，走 text 就够了
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
        # 未读只记「别人说的话」：
        #   · who == 'me' —— 自己发的不算；
        #   · kind == 'tip' —— 动作痕迹（「拍了拍」那种），不是一句话；
        #   · 正看着这个会话 —— 那就已经读过了。
        if (entry.get('kind') == 'msg' and entry.get('who') != 'me'
                and not self._watching(key)):
            self.unread[key] = self.unread.get(key, 0) + 1
        if self.chat is not None:
            try:
                self.chat.on_new(key)
            except tk.TclError:
                self.chat = None

    def _watching(self, key):
        """这条消息进来的时候，人是不是正看着这个会话。

        「正看着」= 面板开着，而且选中的就是这个会话。面板被别人挡住也算 ——
        聊天窗的层级策略是「跟着前台走」，按可见性算的话，随便点一下浏览器
        红点就全冒出来了，那反而更烦人。
        """
        c = self.chat
        return c is not None and c.key == key and c.alive()

    def _unread_total(self):
        return sum(int(n or 0) for n in self.unread.values())

    def _latest_unread(self):
        """未读里最新收到一条的那个会话，没有就 None。"""
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
        """一句人打的字。who='me' 是我说的，否则是某个角色说的。"""
        if not text:
            return
        self._store(key, {'kind': 'msg', 'who': who, 'text': text,
                          'ts': time.time()})

    def _log_tip(self, kind, actor, key, target=None):
        """一条互动痕迹（居中灰字，不是气泡）。

        actor 传 '你' 或角色的显示名 —— 它是文案的一部分，不是身份 id。
        """
        text = tip_text(kind, actor, one=(key != CONV_ALL), target=target)
        if not text:
            return
        self._store(key, {'kind': 'tip', 'who': actor, 'text': text,
                          'ts': time.time()})

    def _log_media(self, who, mt, key, img='', name='', size=0, path='',
                   char=''):
        """一张图 / 一个表情 / 一个表情包 / 一个文件。

        `text` 在这里是「给人看的那一行」：会话栏预览、探针、日志都用它。
        图片不再额外存宽高 —— 解码一次就知道，多两个字段只会多两处不一致。
        文件存的是本地路径，原始字节用完就丢：几十兆的东西不该在内存里留 50 份。
        表情只存那个字符（char），图是每次按当前尺寸现渲的 —— 换 DPI 也不会糊。
        """
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
        """收到一条 say：按类型落进聊天记录。"""
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
        """把收到的文件落到 files_dir，返回路径。存不下来返回 ''。

        文件名要过滤：对面发过来的名字里可能带路径分隔符或控制字符，
        直接拿它拼路径就等于让人往任意位置写文件。
        """
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
        """不出门的指令：一个字节都不发，只有我这块屏幕看得到。"""
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

    # ---------------- 自动更新 ----------------
    #
    # 状态机只有五个值，全在主线程里改（后台线程只往 upd_q 里丢事件）：
    #   idle → checking → downloading → ready
    #                  ↘ error          ↘ latest（已经是最新）
    # 每 UPDATE_EVERY 秒自己再看一次；右键菜单里也能手动点一下。

    def check_update(self, force=False):
        """开始一轮检查。已经有一轮在跑就直接返回（点两下菜单不该起两个线程）。"""
        if not update.enabled():
            return False
        if force:
            self._upd_asked = True
        return self._upd_checker.start(config.VERSION, force=force)

    def _pump_update(self):
        """把后台线程放下的事件取出来处理（主线程，tick 里调）。"""
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
                    # 手动点的「检查更新」= 用户已经说过要装，别让人再点一次。
                    # 以前的做法是下完就停在那儿等第二次点击，用户看到的结果是
                    # 「.new 躺在那儿、什么也没发生」—— 下载了不装，等于没更新。
                    self.say_role(self.me['role'] if self.me else None,
                                  '新版 %s 下载好了，正在重启换上' % ver)
                    self._upd_apply_id = self.root.after(1500, self._apply_update)
                else:
                    # 后台自己发现的：不擅自重启（可能正打字），只把入口说清楚
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
        """托盘菜单/面板里那一行字。状态不同，字不同 —— 点了没反应是最糟的反馈。"""
        if not update.enabled():
            # 没配更新地址（源码模式、内网）：还把这一行留着，只是点不动。
            # 藏掉的话没人知道「有这个功能，只是没配」。
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
        """菜单里点「检查更新 / 更新到 x.y.z」。"""
        if self.upd_state == 'ready':
            ok, msg = update.apply(relaunch=True)
            if not ok:
                self.upd_state = 'error'
                self.upd_note = msg
                self.say_role(self.me['role'] if self.me else None, msg)
                return
            # 脚本正等着我们消失才能换文件 —— 这里必须真的退出
            self.quit()
            return
        if self.upd_state in ('checking', 'downloading', 'found'):
            self.say_role(self.me['role'] if self.me else None, '正在弄呢，稍等')
            return
        self.check_update(force=True)

    def _apply_update(self):
        """下完 1.5 秒后真的换上。

        留这点延迟只是为了让「正在重启换上」那句话能被看见 —— 立刻 quit 的话
        气泡刚冒出来就没了，用户只会看到程序突然消失。
        """
        self._upd_apply_id = None
        if self.upd_state != 'ready':
            return
        self.update_action()

    # ---------------- 托盘 ----------------
    #
    # 认领窗、聊天面板都关掉以后程序还在跑 —— 那时候唯一点得到它的地方就是
    # 系统托盘。**左键开聊天，右键开菜单**（图层、检查更新、换身份、退出）。
    # 宠物右键菜单只管「对角色下指令」，软件自己的维护动作一律不往那儿塞。

    def start_tray(self):
        """挂上托盘图标。起不来就当没有 —— 宠物右键菜单里仍然有退出。"""
        if self.tray is not None or not tray_wanted():
            return self.tray
        t = tray.Tray(icon=os.path.join(assets_dir(), 'app.ico'),
                      tip='pet-club %s' % config.version(),
                      alert=os.path.join(assets_dir(), 'app_alert.ico'),
                      on_left=self.tray_left, on_right=self.tray_right)
        self.tray = t if t.ok else None
        self._tray_tip = ('pet-club %s' % config.version()) if t.ok else ''
        return self.tray

    def _tray_tick(self, now):
        """未读提示：图标在原样 / 带红点之间交替闪，悬停提示带上条数。

        为什么用「闪」而不是气泡：Shell 的气泡通知会进操作中心、可能带提示音，
        为一句「在吗」太重了；图标闪是老习惯，瞟一眼就知道，点一下就能看。
        """
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
            # 读完了（或者压根没有第二张图）：立刻回常态，
            # 别把人晾在「带红点」的那半拍上，看着像还有未读。
            self._tray_on = False
            t.blink(False)
        tip = ('pet-club %s · %d 条未读' % (config.version(), n) if n
               else 'pet-club %s' % config.version())
        if tip != self._tray_tip:
            self._tray_tip = tip
            t.set_tip(tip)

    def tray_left(self, x, y):
        """左键：打开聊天面板，有未读就落在最新那条所在的会话。

        托盘是「想说话」时最顺手的入口 —— 角色可能被人挡住了，或者缩在角落。
        图标在闪说明有东西没看，这时点开停在群聊、还得自己在左边找哪一行，
        那一下就白闪了。
        """
        if self._closing:
            return
        if self.me is None:
            # 聊天窗挂在身份上（头像、台词、单聊列表都要它），没身份先认领
            self._claim_soon('先认领一个身份，才能说话')
            return
        key = self._latest_unread() or CONV_ALL
        self.open_chat(None if key == CONV_ALL else key)

    def tray_right(self, x, y):
        """右键：菜单（「退出」永远在最下面）。"""
        self._close_menu()
        self._menu = self.tray_menu()
        self._menu.popup(x, y)

    def tray_menu(self):
        """托盘的右键菜单。"""
        m = PopMenu(self.root, width=248, on_close=self._menu_closed)
        m.head('pet-club %s · 左键打开聊天' % config.version())
        m.sep()
        # 图层这一行必须一眼看出「点下去会变成什么样」，否则没人敢点 ——
        # 所以写成勾选态 + 一句当前/之后的说明，而不是只说现在是什么状态。
        # 给 probe 是为了菜单开着的时候文案也跟着状态走（和「检查更新」那行一样）。
        m.item(self.layer_label(), self.layer_toggle,
               probe=lambda now: (self.layer_label(), True))
        m.head(self.layer_hint())
        m.sep()
        # 更新行永远在，只是没配更新地址时是灰的 —— 藏起来的话，
        # 用户会以为「这个功能根本没做」。
        m.item(self.update_label(), self.update_action,
               probe=lambda now: (self.update_label(), update.enabled()))
        if self.upd_state == 'error' and self.upd_note:
            m.head(self.upd_note)
        if self.me:
            m.item('换一个身份', self.do_leave)
        m.sep()
        m.item('退出', self.quit)
        return m

    # ---- 图层 ----

    def layer_label(self):
        """图层那一行：勾选态表示现在开着，不带勾就是关着。

        从前写「图层：会被其他窗口盖住」—— 那是在描述*状态*，可菜单项读起来
        像在描述*结果*，于是没人分得清点一下是置顶还是取消置顶。
        现在一行是勾选框，紧跟着一句说明，两边都说清楚。
        """
        return '✓ 始终置顶' if petwin.TOP_ON['on'] else '　 始终置顶'

    def layer_hint(self):
        """紧跟图层那一行的一句灰字：现在是什么、点完会变成什么。"""
        if petwin.TOP_ON['on']:
            return ('当前：角色压在所有普通窗口上方。'
                    '点击取消勾选后，其他窗口会挡住角色。')
        return ('当前：和普通窗口同级，会被其他窗口挡住。'
                '点击勾选后，角色始终显示在最上层。')

    def layer_toggle(self):
        """在「始终置顶」和「被别的窗口盖住」之间切。

        只管桌面上那一层（角色和它们的气泡）：认领窗、聊天面板、菜单不跟着变，
        否则切完图层连聊天都翻不出来了。
        """
        on = petwin.set_layer(not petwin.TOP_ON['on'])
        for pet in self.pets.values():
            petwin.apply_top(pet.win.top)
            if on:
                # 光设属性只是把自己标成「置顶」，并不保证排在置顶带的最前面 ——
                # 切回来的时候直接插队一次，别让人等巡检那 3 秒
                pet.win.reassert_top()
        for b in self.bubbles.values():
            petwin.apply_top(b.top)
        # 菜单一关就没有任何反馈了，所以让角色自己说一句 —— 它就在屏幕上
        if self.me:
            self.say_role(self.me['role'],
                          '又回到最上层了' if on else '现在会被别的窗口盖住')
        return on

    def do_leave(self):
        """释放身份，回到认领界面 —— 不用重启程序就能换角色。"""
        if self.me:
            self.net.send(t='leave')
        self.me = None
        self._pending_role = None
        self._pending_sent = False
        self._clear_pets()
        # 正常会收到服务端推来的新状态并自动弹认领窗；收不到也有兜底
        self.root.after(1200, lambda: self._claim_soon('已释放身份，重新认领一个吧'))

    def set_server(self, host, port):
        """换一台服务器（内部用：测试要在本机起临时服务端）。

        用户界面上没有这个入口 —— 地址写死在 config 里。
        Net 的 stop 之后不能复用（_stop 是单向的），所以必须新建一个实例。
        """
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
        self.cd.reset()             # 换服务器 = 换一屋子人，冷却不该带过去
        self._set_offline(False)

    # ---------------- 连接状态 ----------------

    def offline_note(self):
        """连不上时界面上要说的一句话：说状态，不说地址。

        地址属于实现细节，界面上一个字都不出现 —— 用户也改不了它，
        写出来只是把内部信息漏给不该关心的人。
        """
        return OFFLINE_NOTE

    def _set_offline(self, on):
        on = bool(on)
        changed = on != self.offline
        self.offline = on
        # 没有角落入口了：还没认领身份时唯一能说话的窗口就是认领窗，
        # 已经站在桌面上的话走网络层那条气泡（见 on_msg 的 _down）。
        #
        # 连回来的时候**必须把这行字擦掉**，否则「正在重试…」会一直挂在那儿，
        # 人看着以为还断着（实际早就重连好了，只是没有人来清它）。
        # 只擦自己写过的那句：这一行同时也是校验错误的落点，别人的话不能碰。
        dlg = self._claim_dlg
        if dlg is not None:
            if on:
                dlg.set_note(self.offline_note())
            elif dlg.note_text() == self.offline_note():
                dlg.set_note(' ')
        # 界面上不写地址，但排查时得知道在连谁 —— 这条只进控制台
        if changed:
            print('[pet-club] %s %s' % (
                '连不上' if on else '已连上',
                config.format_endpoint(self.host, self.port)))

    # ---------------- 杂项 ----------------

    def say_role(self, role, text):
        pet = self.pets.get(role) if role else None
        if not pet or not text:
            return
        b = self.bubbles.get(role)
        if b is None:
            b = self.bubbles[role] = Bubble(self.root)
        b.show(text, *pet.bubble_anchor())
        self._bubble_until[role] = time.monotonic() + BUBBLE_SEC

    def toast(self, text):
        messagebox.showwarning('pet-club', text, parent=self.root)

    def quit(self):
        if self._closing:
            return
        self._closing = True
        self._close_menu()
        if self.tray is not None:
            # 必须显式摘掉：进程没了图标还留在托盘里，要等鼠标划过去才消失
            self.tray.close()
            self.tray = None
        if self.chat is not None:
            self.chat.close()
        if self._tick_id is not None:
            try:
                self.root.after_cancel(self._tick_id)
            except tk.TclError:
                pass
            self._tick_id = None
        if self._upd_apply_id is not None:
            # 自己点「更新」时 _apply_update 已经在跑了，这里只是别留一个悬着的定时器
            try:
                self.root.after_cancel(self._upd_apply_id)
            except tk.TclError:
                pass
            self._upd_apply_id = None
        try:
            self.net.send(t='leave')
        except Exception:
            pass
        self.net.stop()
        for p in self.pets.values():
            p.destroy()
        self.pets.clear()
        for b in self.bubbles.values():
            b.destroy()
        self.bubbles.clear()
        self._bubble_until.clear()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
