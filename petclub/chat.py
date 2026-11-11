# -*- coding: utf-8 -*-
"""聊天面板：一个全体群聊 + 每个人一个单聊。

微信的样子，不是像素级复刻，是把它的结构搬过来：
  左边会话栏（搜索框、头像、名字和时间同一行、下面一行最后一句、未读红点），
  右边消息流（我的靠右绿气泡、别人的靠左白气泡），
  底下输入区（表情 / 图片 / 文件三个小按钮 + 发送）。
一个窗口，四个会话，点一下就换，随时能发。

为什么值得这么改：
  - 从前只有一个「传话」窗口，对全员和对某个人混在一条时间线上 ——
    聊上十几句就分不清哪句是对谁说的了。
  - 互动（敲窗、提醒喝水、拍一拍）以前只会冒一个 2.6 秒的气泡，飘完就没。
    现在它们作为**系统提示**落在对应的会话里：群发的记进群聊，点名给我的记进
    那个人的会话。样式和普通消息不一样 —— 居中的灰色小字，微信里
    「张三 拍了拍你」就是那个东西。它是记录，不是消息，不占气泡、不显示头像。

数据不在这个类里：`App.chat_log` 是唯一一份（键 = 'all' 或角色 id），
窗口只是它的视图。所以关窗口不丢、重开还在、切会话也不会串台。

四种消息（`entry['mt']`）：
  text     普通文字气泡
  img      图片：白边里的那张图，点开看大图（原比例）
  sticker  表情包：成品卡片，不套气泡
  file     文件：一张卡片，点一下用系统默认程序打开
"""

import os
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageTk

from . import media
from .config import ROLE_NAME, ROLES, assets_dir
from .surface import root_of
from .ui import (ACCENT, ACCENT_SOFT, FIELD, FG, FONT, FONT_M, FONT_S, FONT_XS,
                 LINE, MUTED, SUB, WINDOW, CardWindow, RoundButton, _Field, _rr)

CONV_ALL = 'all'            # 群聊的会话键（用字符串，别用 None：字典键和日志都好认）

WIDTH = 728
LIST_W = 212
MSG_H = 392
ROW_H = 64                  # 会话行高（微信是 64）
GAP_TIME = 300.0            # 两条之间超过 5 分钟，插一行时间
BUBBLE_PAD = 10             # 气泡内边距
BUBBLE_MAX = 300            # 气泡最大文字宽度
AVATAR = 30
SIDE = 14                   # 气泡离窗口边的距离

# ---- 微信那套颜色 ----
CANVAS_BG = '#f2f2f2'       # 消息区底色（不是纯白：白气泡要浮在灰上才分得开）
BUBBLE_ME = '#95ec69'       # 自己：微信绿
BUBBLE_ME_FG = '#111111'    # 绿底上用深字（白字在绿上根本看不清）
BUBBLE_OTH = '#ffffff'
SIDE_BG = '#f7f7f7'         # 会话栏
SIDE_SEL = '#e6e6e6'        # 选中的那一行
SIDE_HOVER = '#efefef'

AVATAR_COLORS = ('#5b5bf0', '#0f9d76', '#e08c2a', '#d6455d', '#2b8fd6')

# 头像是从 idle 立绘上「掐头」来的：大头娃娃的头占了画面上端四成左右，
# 直接缩整张立绘的话，30px 的圆圈里是一粒看不清的小人。
HEAD_RATIO = 0.44
_PHOTO_CACHE = {}
_HEAD_CACHE = {}


def head_square(role):
    """角色的「头」—— 从 idle 立绘上裁一个正方形，返回 PIL 图或 None。

    和 head_photo 分开是因为调试时想拿原图看裁得准不准：
    PhotoImage 出了 Tk 就没法检查像素了。
    """
    if role in _HEAD_CACHE:
        return _HEAD_CACHE[role]
    path = os.path.join(assets_dir(), 'roles', role, 'idle.png')
    try:
        im = Image.open(path).convert('RGBA')
    except (OSError, ValueError):
        _HEAD_CACHE[role] = None
        return None

    box = im.split()[3].getbbox()
    if box:
        im = im.crop(box)
    side = max(8, min(im.width, int(round(im.height * HEAD_RATIO))))
    cx = im.width / 2.0
    im = im.crop((int(round(cx - side / 2.0)), 0,
                  int(round(cx + side / 2.0)), side))
    _HEAD_CACHE[role] = im
    return im


def head_photo(role, size):
    """角色头像（圆形）。认不出角色/没图就返回 None，调用方退回色块。"""
    key = (role, size)
    if key in _PHOTO_CACHE:
        return _PHOTO_CACHE[key]
    im = head_square(role)
    if im is None:
        _PHOTO_CACHE[key] = None
        return None

    # 先按 4 倍画圆再缩回去：Tk 没有抗锯齿，直接在小图上画边缘是锯齿状的
    big = im.resize((size * 4, size * 4), Image.LANCZOS)
    mask = Image.new('L', (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    big.putalpha(mask)
    photo = ImageTk.PhotoImage(
        big.resize((size, size), Image.LANCZOS), master=None)
    _PHOTO_CACHE[key] = photo
    return photo


def human_size(n):
    """12345 → '12.1 KB'。文件卡片上那一行小字。"""
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return ''


class _IconBtn(tk.Canvas):
    """输入区左下角那种小图标按钮：一个 26px 的方块，里面是手画的图标。

    不写字也不放 emoji：Tk 在 Windows 上对彩色 emoji 的支持时有时无，
    真机上很容易变成豆腐块。自己画三条线反而稳定，也跟界面的手绘风格一致。
    """

    SIZE = 26

    def __init__(self, parent, kind, cmd, bg=WINDOW):
        s = self.SIZE
        super().__init__(parent, width=s, height=s, bg=bg,
                         highlightthickness=0, bd=0, cursor='hand2')
        self.kind = kind
        self.cmd = cmd
        self._bg = bg
        self._hover = False
        self._idle = '#8a8f99'
        self._draw()
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<Button-1>', lambda e: self.cmd())

    def _set_hover(self, on):
        self._hover = on
        self.configure(bg='#eef0f4' if on else self._bg)
        self._draw()

    def _draw(self):
        self.delete('all')
        c = '#5b6068' if self._hover else self._idle
        w = 1.4
        if self.kind == 'emoji':
            self.create_oval(4, 4, 22, 22, outline=c, width=w)
            self.create_oval(9, 10, 10.6, 11.6, fill=c, outline='')
            self.create_oval(15.4, 10, 17, 11.6, fill=c, outline='')
            self.create_arc(8, 9, 18, 19, start=200, extent=140,
                            style='arc', outline=c, width=w)
        elif self.kind == 'image':
            self.create_rectangle(4, 6, 22, 20, outline=c, width=w)
            self.create_oval(7, 9, 11, 13, outline=c, width=w)
            self.create_line(5, 19, 12, 12, 16, 16, 19, 13, 22, 17,
                             fill=c, width=w)
        else:                                   # file
            self.create_line(6, 4, 16, 4, 21, 9, 21, 22, 6, 22, 6, 4,
                             fill=c, width=w)
            self.create_line(16, 4, 16, 9, 21, 9, fill=c, width=w)
            for y in (12, 15, 18):
                self.create_line(9, y, 18, y, fill=c, width=1)


class _PickerPop(tk.Toplevel):
    """表情面板：悬在输入区上方的一个小方框，分成三页。

      表情   —— Windows 自带的那套 emoji（用 PIL 渲成位图，见 media.emoji_image）
      我的   —— 我自己角色的八张卡片表情包（assets/stickers/<角色>/）
      他人   —— 另外三个角色的卡片，共 24 张

    三页共用一套格子，切换只是换内容；高度不同的那点差异直接重算窗口大小，
    切页时窗口跟着缩一下，比留一块空白好看。

    刻意不 grab：微信里选表情的时候还能滚消息、还能打字。收起来靠主窗口那一下
    点击（见 ChatWindow._pop_closed）。
    """

    CELL = 52
    COLS = 6
    PAD = 2

    def __init__(self, parent, groups, on_pick, x, y):
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.configure(bg=LINE)
        self._groups = groups
        self._on_pick = on_pick
        self._tab_labels = {}
        self._grid = None
        self._keep = []
        self._anchor = (x, y)

        body = tk.Frame(self, bg=WINDOW)
        body.pack(padx=1, pady=1)
        self._body = body

        head = tk.Frame(body, bg=WINDOW)
        head.pack(fill='x', padx=8, pady=(8, 4))
        for i, group in enumerate(groups):
            lb = tk.Label(head, text=group[0], bg=WINDOW, fg=MUTED, font=FONT_XS,
                          cursor='hand2', padx=8, pady=3)
            lb.pack(side='left')
            lb.bind('<Button-1>', lambda e, k=i: self.select(k))
            self._tab_labels[i] = lb

        self.select(0)

    def select(self, index):
        if not self.winfo_exists():
            return
        for i, lb in self._tab_labels.items():
            on = i == index
            lb.configure(fg=ACCENT if on else MUTED,
                         bg=ACCENT_SOFT if on else WINDOW)
        if self._grid is not None:
            self._grid.destroy()
        self._keep = []
        group = self._groups[index]
        cell, cols = (group[2], group[3]) if len(group) > 2 else (self.CELL,
                                                                 self.COLS)
        grid = tk.Frame(self._body, bg=WINDOW)
        grid.pack(padx=8, pady=(0, 8))
        self._grid = grid
        for i, (mt, payload, ph) in enumerate(group[1]):
            # 两页格子大小不同：emoji 是一张张脸，卡片表情下面还有一行字，
            # 用同一个尺寸的话字会糊成一团（第一版就是这样）
            box = self._cell(grid, mt, payload, ph, cell)
            box.grid(row=i // cols, column=i % cols, padx=self.PAD, pady=self.PAD)
        self._fit()

    def _fit(self):
        if not self.winfo_exists():
            return
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        x, y = self._anchor
        x = max(4, min(x, sw - w - 4))
        y = max(4, y - h - 6)
        self.geometry('%dx%d+%d+%d' % (w, h, x, y))
        try:
            self.deiconify()
            self.lift()
        except tk.TclError:                 # 已经收起来了（切页时点到了别处）
            pass

    def _cell(self, parent, mt, payload, ph, size):
        cell = tk.Canvas(parent, width=size, height=size, bg=WINDOW,
                         highlightthickness=0, bd=0, cursor='hand2')
        if ph is not None:
            cell.create_image(size / 2.0, size / 2.0, image=ph)
            self._keep.append(ph)       # 引用必须留住，否则图会被回收成空白
        cell.bind('<Enter>', lambda e, c=cell: c.configure(bg='#f0f2f6'))
        cell.bind('<Leave>', lambda e, c=cell: c.configure(bg=WINDOW))
        cell.bind('<Button-1>',
                  lambda e, m=mt, p=payload: self._pick(m, p))
        return cell

    def _pick(self, mt, payload):
        cb, self._on_pick = self._on_pick, None
        self.destroy()
        if cb is not None:
            cb(mt, payload)


class ChatWindow(CardWindow):

    def __init__(self, parent, on_send, on_close=None, me=None,
                 is_online=None, log=None, on_media=None, unread=None):
        super().__init__(parent, WIDTH)
        self.on_send = on_send
        self.on_media = on_media        # (mt, path) -> 错误文案 / ''
        self.on_close = on_close
        self.me = me
        self.is_online = is_online or (lambda r: False)
        self.log = log if log is not None else {}
        # 未读由 App 持有并传进来（关掉面板也不该丢）：托盘闪烁读的是同一份，
        # 两边各存一份就会出现「红点算清了，图标还在闪」这种事。
        self.unread = unread if unread is not None else {}
        self.key = CONV_ALL
        self.query = ''
        self._rows = {}
        self._canvas_w = 0
        self._pop = None
        self._top_on = True         # 起来时在置顶带里，之后跟着前台走（见 follow）
        self._f = tkfont.Font(font=FONT)
        self._f_m = tkfont.Font(font=FONT_M)
        self._f_s = tkfont.Font(font=FONT_XS)
        self._f_11 = tkfont.Font(font=('Microsoft YaHei UI', 9, 'bold'))
        self._build()
        self.refresh_list()
        self.switch(CONV_ALL)

    # ---------------- 搭界面 ----------------

    def _build(self):
        body = tk.Frame(self.content, bg=WINDOW)
        body.pack(fill='both', expand=True)

        left = tk.Frame(body, bg=SIDE_BG, width=LIST_W)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)

        # 搜索框：微信会话栏顶上那一条。没有它是「像微信」，有了它还能用 ——
        # 输入就按名字和聊天内容过滤下面的会话。
        sbox = tk.Frame(left, bg='#ededed')
        sbox.pack(fill='x', padx=10, pady=(10, 8))
        inner = tk.Frame(sbox, bg='#ededed')
        inner.pack(fill='x', padx=1, pady=1)
        tk.Label(inner, text='⌕', bg='#ededed', fg=MUTED,
                 font=('Microsoft YaHei UI', 11)).pack(side='left', padx=(6, 2))
        self.search = tk.Entry(inner, font=FONT_XS, relief='flat',
                               bg='#ededed', fg=FG, insertbackground=FG,
                               highlightthickness=0, bd=0)
        self.search.pack(fill='x', side='left', ipady=5, padx=(0, 6))
        self.search.bind('<KeyRelease>', self._on_search)
        self._search_hint = tk.Label(inner, text='搜索', bg='#ededed',
                                     fg=MUTED, font=FONT_XS)
        self._search_hint.place(x=26, y=3)
        self.search.bind('<FocusIn>', lambda e: self._search_hint.place_forget())
        self.search.bind('<FocusOut>', self._search_blur)

        self.list_box = tk.Frame(left, bg=SIDE_BG)
        self.list_box.pack(fill='both', expand=True)

        right = tk.Frame(body, bg=WINDOW)
        right.pack(side='left', fill='both', expand=True, padx=(12, 0))

        head = tk.Frame(right, bg=WINDOW)
        head.pack(fill='x')
        self.title = tk.Label(head, text='', bg=WINDOW, fg=FG,
                              font=('Microsoft YaHei UI', 12, 'bold'))
        self.title.pack(side='left')
        self.state_lb = tk.Label(head, text='', bg=WINDOW, fg=MUTED, font=FONT_XS)
        self.state_lb.pack(side='left', padx=(8, 0), pady=(3, 0))

        self.msgs = tk.Canvas(right, bg=CANVAS_BG, height=MSG_H,
                              highlightthickness=0, bd=0)
        self.msgs.pack(fill='both', expand=True, pady=(8, 0))
        self.msgs.bind('<MouseWheel>', self._wheel)
        self.msgs.bind('<Configure>', self._on_resize)
        self.msgs.bind('<Button-1>', self._pop_closed, add='+')

        # 和校验错误共用的一行：冷却没过、离线、对方不在线，都先说在这儿
        self.note = tk.Label(right, text=' ', bg=WINDOW, fg=SUB, font=FONT_XS,
                             anchor='w', justify='left', height=1,
                             wraplength=WIDTH - LIST_W - 90)
        self.note.pack(fill='x', pady=(4, 0))

        # 输入区：一个描边框，上半是输入行，下半是一排小图标 + 发送
        inbox = tk.Frame(right, bg=LINE)
        inbox.pack(fill='x', pady=(2, 0))
        pad = tk.Frame(inbox, bg=WINDOW)
        pad.pack(fill='both', expand=True, padx=1, pady=1)

        self.field = _Field(pad)
        self.field.entry.bind('<Return>', lambda e: self.send())
        # Ctrl+V：剪贴板里是图或文件就直接发，是文字就交回系统默认的粘贴。
        # Tk 的绑定不会从子控件冒到 Toplevel，所以要一个个控件挂过去。
        self.bind_paste()

        bar = tk.Frame(pad, bg=WINDOW)
        bar.pack(fill='x', padx=8, pady=(0, 6))
        self.send_btn = RoundButton(bar, '发送', self.send, primary=True,
                                    width=66, height=28)
        self.send_btn.pack(side='right')
        self.icon_btn = {}
        for kind, tip in (('emoji', '表情'), ('image', '图片'), ('file', '文件')):
            btn = _IconBtn(bar, kind, lambda k=kind: self._on_icon(k))
            btn.pack(side='left', padx=(0, 2))
            self.icon_btn[kind] = btn

    # ---------------- 会话数据 ----------------

    def convs(self):
        """群聊永远排第一，后面是我以外的每个人（不在线也列着，微信也这样）。"""
        out = [(CONV_ALL, '全体群聊')]
        for r in ROLES:
            if r != self.me:
                out.append((r, ROLE_NAME.get(r, r)))
        return out

    def visible_convs(self):
        """搜索框有字时只留命中的会话（名字或任意一条消息里含这个词）。"""
        q = (self.query or '').strip().lower()
        if not q:
            return self.convs()
        out = []
        for key, name in self.convs():
            if q in name.lower():
                out.append((key, name))
                continue
            for e in self.entries(key):
                if q in (e.get('text') or '').lower():
                    out.append((key, name))
                    break
        return out

    def name_of(self, key):
        return '全体群聊' if key == CONV_ALL else ROLE_NAME.get(key, key)

    def entries(self, key=None):
        return self.log.get(key if key is not None else self.key) or []

    def unread_of(self, key):
        return int(self.unread.get(key) or 0)

    def last_of(self, key):
        es = self.entries(key)
        return es[-1] if es else None

    def preview_of(self, key):
        e = self.last_of(key)
        if e is None:
            return '还没有消息'
        text = e.get('text') or ''
        if e.get('kind') == 'tip':
            return text
        if e.get('who') == 'me' or key != CONV_ALL:
            return text
        return '%s：%s' % (ROLE_NAME.get(e.get('who'), ''), text)

    def history_text(self, key=None):
        """某个会话里所有条目的纯文本（探针用：不读画布，读数据）。"""
        return '\n'.join(e.get('text') or '' for e in self.entries(key))

    def kinds(self, key=None):
        return [e.get('kind') for e in self.entries(key)]

    def types(self, key=None):
        """条目类型：text / img / sticker / file / tip。"""
        return ['tip' if e.get('kind') == 'tip' else (e.get('mt') or 'text')
                for e in self.entries(key)]

    # ---------------- 搜索 ----------------

    def _on_search(self, _e=None):
        self.query = self.search.get()
        self.refresh_list()

    def _search_blur(self, _e=None):
        if not self.search.get():
            self._search_hint.place(x=26, y=3)

    # ---------------- 会话栏 ----------------

    def refresh_list(self):
        for w in list(self.list_box.winfo_children()):
            w.destroy()
        self._rows = {}
        for key, name in self.visible_convs():
            self._rows[key] = self._row(key, name)
        # 行高取所有行的自然高度：写死 64 在 125% 缩放下会把最后一行内容切掉
        # （红点只剩半个），而逐行不同高又会让列表看起来歪。所以先让每行自己量，
        # 再统一成最高的那个 —— 一致，且切不着。
        if self._rows:
            self.list_box.update_idletasks()
            h = max(r.winfo_reqheight() for r in self._rows.values())
            for r in self._rows.values():
                r.configure(height=h)
                r.pack_propagate(False)

    def _row(self, key, name):
        sel = key == self.key
        bg = SIDE_SEL if sel else SIDE_BG
        n = self.unread_of(key)
        row = tk.Frame(self.list_box, bg=bg, cursor='hand2')
        row.pack(fill='x')

        av = tk.Canvas(row, width=40, height=40, bg=bg,
                       highlightthickness=0, bd=0)
        av.pack(side='left', padx=(11, 9), pady=11)
        photo = None if key == CONV_ALL else head_photo(key, 38)
        if photo is not None:
            av.create_image(20, 20, image=photo)      # 圆形透明，不用再画圈
        else:
            av.create_oval(2, 2, 38, 38, fill=self._avatar_color(key), outline='')
            av.create_text(20, 20, text='群' if key == CONV_ALL else name[:1],
                           fill='#ffffff', font=('Microsoft YaHei UI', 12, 'bold'))

        col = tk.Frame(row, bg=bg)
        col.pack(side='left', fill='both', expand=True, pady=(9, 9),
                 padx=(0, 10))

        # 第一行：名字 + 时间（时间挤在右边，和微信一样）
        r1 = tk.Frame(col, bg=bg)
        r1.pack(fill='x')
        stamp = self._list_time((self.last_of(key) or {}).get('ts'))
        if stamp:
            tk.Label(r1, text=stamp, bg=bg, fg=MUTED, bd=0, pady=0,
                     font=FONT_XS).pack(side='right')
        name_w = LIST_W - 60 - (self._f_s.measure(stamp) + 8 if stamp else 0)
        # 两行字都必须先按像素截断：Tk 的 Label 不会自己裁 —— 它只会把行撑宽，
        # 而撑出去的部分连红点一起被父框架切掉（第一版的红点就是半个）。
        tk.Label(r1, text=self._fit(name, self._f_11, max(40, name_w)),
                 bg=bg, fg=FG, font=('Microsoft YaHei UI', 10, 'bold'),
                 anchor='w', bd=0, pady=0).pack(side='left')

        # 第二行：最后一句 + 未读红点
        r2 = tk.Frame(col, bg=bg)
        r2.pack(fill='x', pady=(3, 0))
        badge = None
        if n:
            # 红点是正圆，不能压扁：微信那个 18px 的圆里放一位数字
            s = max(18, self._f_s.metrics('linespace'))
            badge = tk.Canvas(r2, width=s + 2, height=s, bg=bg,
                              highlightthickness=0, bd=0)
            badge.pack(side='right', padx=(4, 0))
            badge.create_oval(1, 1, s + 1, s - 1, fill='#f0445a', outline='')
            badge.create_text(s / 2.0 + 1, s / 2.0,
                              text=str(n) if n < 10 else '9+',
                              fill='#ffffff', font=FONT_XS)
        # Label 默认带 1px 边框，量宽度的时候要把它算进去，否则最后一像素永远被切
        pv_w = LIST_W - 60 - 10 - (26 if badge is not None else 0) - 6
        tk.Label(r2, text=self._fit(self.preview_of(key), self._f_s, pv_w),
                 bg=bg, fg=MUTED, font=FONT_XS, anchor='w', bd=0,
                 pady=0).pack(side='left')

        for w in (row, av, col, r1, r2):
            w.bind('<Button-1>', lambda e, k=key: self.switch(k))
        for w in r1.winfo_children() + r2.winfo_children():
            w.bind('<Button-1>', lambda e, k=key: self.switch(k))
        if badge is not None:
            badge.bind('<Button-1>', lambda e, k=key: self.switch(k))
        return row

    @staticmethod
    def _fit(text, font, max_w):
        """按像素宽截断并补省略号。中文的「几个字」和「几个像素」不是一回事。"""
        text = (text or '').replace('\n', ' ')
        if font.measure(text) <= max_w:
            return text
        cut = text
        while cut and font.measure(cut + '…') > max_w:
            cut = cut[:-1]
        return cut + '…'

    @staticmethod
    def _list_time(ts):
        """会话栏右上角那串时间：今天给时:分，更早给日期（微信就是这样）。"""
        if not ts:
            return ''
        lt = time.localtime(ts)
        now = time.localtime()
        if (lt.tm_year, lt.tm_yday) == (now.tm_year, now.tm_yday):
            return time.strftime('%H:%M', lt)
        if lt.tm_year == now.tm_year:
            return time.strftime('%m/%d', lt)
        return time.strftime('%y/%m/%d', lt)

    def _avatar_color(self, who):
        if who == CONV_ALL:
            return ACCENT
        try:
            return AVATAR_COLORS[ROLES.index(who) % len(AVATAR_COLORS)]
        except ValueError:
            return ACCENT

    # ---------------- 切换与状态 ----------------

    def switch(self, key):
        if key not in [k for k, _ in self.convs()]:
            key = CONV_ALL
        self.key = key
        self.unread[key] = 0
        self.refresh_list()
        self.title.configure(text=self.name_of(key))
        self._update_state()
        self.set_note('')
        self.render()
        self.focus_input()

    def refresh_state(self):
        """服务端推了新快照（有人进 / 有人走）：在线人数得跟着变。

        从前这个数只在切会话时算一次，于是两个人上桌之后就一直停在旧数字上，
        看起来像统计坏了。
        """
        self._update_state()

    def _update_state(self):
        if self.key == CONV_ALL:
            # 自己一定算在线 —— 人就坐在这台机器前面，进程还是他把窗口点开的。
            # 从前这里写着 `r != self.me`，五个人全上桌也只显示 4/4，像是少了一个。
            online = sum(1 for r in ROLES if r == self.me or self.is_online(r))
            total = len(ROLES)
            text = '%d/%d 在线' % (online, total)
        else:
            text = '在线' if self.is_online(self.key) else '空闲'
        self.state_lb.configure(text=text)

    def on_new(self, key):
        """App 记完一笔后叫一声：列表重画，正看着的这个会话往下滚。

        未读的计数在 App 那边（`_store` 是所有记录的入口，只有那儿分得清
        「谁说的」）。这里只管画 —— 从前面板关着的时候也要记未读，托盘
        才闪得起来，而那时根本没有这个对象。
        """
        self.refresh_list()
        if key == self.key:
            self.render(scroll=True)

    # ---------------- 画消息 ----------------

    def render(self, scroll=True):
        c = self.msgs
        try:
            c.delete('all')
        except tk.TclError:
            return
        w = c.winfo_width()
        if w <= 1:
            w = WIDTH - LIST_W - 60
        y = 14
        last_ts = None
        for e in self.entries():
            ts = e.get('ts') or 0
            if last_ts is None or ts - last_ts > GAP_TIME:
                # anchor='n'：从 y 往下排，别再往上溢 —— 用默认的居中锚点，
                # 第一行时间会被画到画布上边缘之外，只剩半个字
                c.create_text(w / 2, y, text=self._clock(ts), anchor='n',
                              fill=MUTED, font=FONT_XS)
                y += 26
            last_ts = ts
            if e.get('kind') == 'tip':
                y = self._draw_tip(c, w, y, e.get('text') or '')
            else:
                y = self._draw_msg(c, w, y, e)
            y += 10
        if not self.entries():
            c.create_text(w / 2, 60, text='还没有消息', fill=MUTED, font=FONT_S)
        total = y + 8
        c.configure(scrollregion=(0, 0, w, total))
        try:
            # 内容装得下就强制回到顶部：否则上一次「滚到底」留下的偏移会挂着，
            # 第一行时间被顶出画布，看起来像渲染坏了
            if scroll and total > c.winfo_height():
                c.yview_moveto(1.0)
            elif total <= c.winfo_height():
                c.yview_moveto(0.0)
        except tk.TclError:
            pass

    def _draw_tip(self, c, w, y, text):
        """系统提示：居中的灰色小字。和消息是两种东西，一眼能分开。"""
        max_w = max(120, min(w - 90, 400))
        lines = self._wrap(text, self._f_s, max_w)
        lh = self._f_s.metrics('linespace')
        for i, l in enumerate(lines):
            c.create_text(w / 2.0, y + i * lh, text=l, anchor='n',
                          fill=MUTED, font=FONT_XS)
        return y + lh * len(lines) + 4

    def _layout(self, c, w, y, e, bw, bh):
        """算出气泡的左下右上 + 头像位置。三种消息共用这一份摆法。"""
        mine = e.get('who') == 'me'
        if mine:
            ax = w - SIDE - AVATAR
            bx = ax - 8 - bw
        else:
            ax = SIDE
            bx = ax + AVATAR + 10
        top = y
        if not mine and self.key == CONV_ALL:
            c.create_text(bx + 1, y, text=ROLE_NAME.get(e.get('who'), ''),
                          anchor='nw', fill=MUTED, font=FONT_XS)
            top = y + self._f_s.metrics('linespace') + 2
        self._avatar(c, ax, top + min(bh, AVATAR) / 2.0, e.get('who'))
        return mine, bx, top

    def _draw_msg(self, c, w, y, e):
        mt = e.get('mt') or 'text'
        if mt == 'emoji':
            return self._draw_emoji(c, w, y, e)
        if mt == 'img':
            return self._draw_image(c, w, y, e)
        if mt == 'sticker':
            return self._draw_sticker(c, w, y, e)
        if mt == 'file':
            return self._draw_file(c, w, y, e)

        max_w = max(80, min(BUBBLE_MAX, w - 2 * (SIDE + AVATAR + 10)))
        lines = self._wrap(e.get('text') or '', self._f, max_w)
        lh = self._f.metrics('linespace')
        tw = max(self._f.measure(l) for l in lines)
        bw = tw + BUBBLE_PAD * 2
        bh = lh * len(lines) + BUBBLE_PAD * 2 - 1
        mine, bx, top = self._layout(c, w, y, e, bw, bh)

        _rr(c, bx, top, bx + bw, top + bh, 10,
            fill=BUBBLE_ME if mine else BUBBLE_OTH, outline='' if mine else LINE,
            width=1)
        for i, l in enumerate(lines):
            c.create_text(bx + BUBBLE_PAD, top + BUBBLE_PAD + i * lh, text=l,
                          anchor='nw', fill=BUBBLE_ME_FG if mine else FG,
                          font=FONT)
        return top + bh

    def _draw_emoji(self, c, w, y, e):
        """表情：一张透明底的小图，不套气泡 —— 微信里表情就是光秃秃的。"""
        box = media.EMOJI_BOX
        ph = media.emoji_photo(e.get('char') or '', box)
        if ph is None:
            return self._fallback(c, w, y, e, e.get('char') or '[表情]')
        bw, bh = ph.width(), ph.height()
        mine, bx, top = self._layout(c, w, y, e, bw, bh)
        c.create_image(bx + bw / 2.0, top + bh / 2.0, image=ph)
        return top + bh

    def _draw_image(self, c, w, y, e):
        ph = media.photo(e.get('img'), media.IMG_BOX)
        if ph is None:
            return self._fallback(c, w, y, e, '[图片打不开]')
        pad = 3
        bw, bh = ph.width() + pad * 2, ph.height() + pad * 2
        mine, bx, top = self._layout(c, w, y, e, bw, bh)   # 图片两种人都不套底色
        # 图片不套绿气泡：微信里图片就是图片，套一层自己的底色会像「图片被框住了」
        _rr(c, bx, top, bx + bw, top + bh, 8,
            fill=BUBBLE_OTH, outline=LINE, width=1)
        c.create_image(bx + bw / 2.0, top + bh / 2.0, image=ph)
        # 点图看大图：卡片本身记不住原图，所以按索引去 log 里取
        tag = 'img:%d' % id(e)
        c.create_rectangle(bx, top, bx + bw, top + bh, outline='',
                           fill='', tags=(tag,))
        c.tag_bind(tag, '<Button-1>', lambda ev, it=e: self._open_image(it))
        return top + bh

    def _draw_sticker(self, c, w, y, e):
        ph = media.photo(e.get('img'), media.STK_BOX)
        if ph is None:
            return self._fallback(c, w, y, e, '[表情打不开]')
        bw, bh = ph.width(), ph.height()
        mine, bx, top = self._layout(c, w, y, e, bw, bh)
        # 表情包本身就是一张画好的卡片，不再套气泡
        c.create_image(bx + bw / 2.0, top + bh / 2.0, image=ph)
        return top + bh

    def _draw_file(self, c, w, y, e):
        """文件卡片：左边一个文档图标，右边名字 + 大小。微信就是这个样子。"""
        mine = e.get('who') == 'me'
        name = e.get('name') or '文件'
        bw = min(240, max(150, w - 2 * (SIDE + AVATAR + 10)))
        bh = 60
        mine, bx, top = self._layout(c, w, y, e, bw, bh)
        _rr(c, bx, top, bx + bw, top + bh, 10,
            fill=BUBBLE_ME if mine else BUBBLE_OTH, outline='' if mine else LINE,
            width=1)

        ix, iy = bx + 10, top + bh / 2.0
        ic = '#33383f' if mine else '#8a8f99'
        c.create_rectangle(ix, iy - 15, ix + 22, iy + 15, outline=ic, width=1.2)
        c.create_line(ix + 14, iy - 15, ix + 22, iy - 7, ix + 22, iy - 15,
                      fill=ic, width=1.2)
        for dy in (-5, 0, 5):
            c.create_line(ix + 5, iy + dy, ix + 17, iy + dy, fill=ic, width=1)

        tx = ix + 32
        avail = bx + bw - 12 - tx
        c.create_text(tx, top + 17, text=self._fit(name, self._f_s, avail),
                      anchor='nw', fill=FG, font=FONT_S)
        c.create_text(tx, top + 36, text=human_size(e.get('size')),
                      anchor='nw', fill=SUB if not mine else '#3f4a35',
                      font=FONT_XS)

        tag = 'file:%d' % id(e)
        c.create_rectangle(bx, top, bx + bw, top + bh, outline='',
                           fill='', tags=(tag,))
        c.tag_bind(tag, '<Button-1>', lambda ev, it=e: self._open_file(it))
        return top + bh

    def _fallback(self, c, w, y, e, text):
        """图片解不开时的退路：至少让人看见这儿原来有个东西。"""
        e = dict(e, mt='text', text=text)
        return self._draw_msg(c, w, y, e)

    # ---- 点开媒体 ----

    def _open_file(self, e):
        """用系统默认程序打开。文件不在（被删了/没存下来）就提示。"""
        path = e.get('path') or ''
        if not path or not os.path.exists(path):
            self.set_note('这个文件不在了：%s' % (e.get('name') or ''))
            return
        try:
            os.startfile(path)
        except (AttributeError, OSError):
            self.set_note('打不开这个文件，路径：%s' % path)

    def _open_image(self, e):
        """点图片看大图：另开一个小窗，原比例但不超出屏幕。"""
        im = media.decode(e.get('img'))
        if im is None:
            self.set_note('这张图打不开')
            return
        top = tk.Toplevel(self.top)
        top.title('图片')
        top.configure(bg='#111111')
        sw = top.winfo_screenwidth() * 0.7
        sh = top.winfo_screenheight() * 0.7
        k = min(1.0, sw / max(1, im.width), sh / max(1, im.height))
        if k < 1.0:
            im = im.resize((max(1, int(im.width * k)),
                            max(1, int(im.height * k))), Image.LANCZOS)
        ph = ImageTk.PhotoImage(im, master=None)
        cv = tk.Label(top, image=ph, bg='#111111', bd=0)
        cv.image = ph
        cv.pack()
        top.bind('<Button-1>', lambda ev: top.destroy())
        top.bind('<Escape>', lambda ev: top.destroy())

    def _avatar(self, c, x, cy, who):
        role = self.me if who in ('me', self.me) else who
        photo = head_photo(role, AVATAR) if role else None
        if photo is not None:
            c.create_image(x + AVATAR / 2.0, cy, image=photo)
            return
        col = self._avatar_color(role or who)
        c.create_oval(x, cy - AVATAR / 2.0, x + AVATAR, cy + AVATAR / 2.0,
                      fill=col, outline='')
        ch = '我' if role == self.me else ROLE_NAME.get(who, '?')[:1]
        c.create_text(x + AVATAR / 2.0, cy, text=ch, fill='#ffffff', font=FONT_M)

    @staticmethod
    def _wrap(text, font, max_w):
        """按像素宽度硬折行。中文没有空格，不能靠 Text 的 word wrap。"""
        lines, cur = [], ''
        for ch in (text or ''):
            if ch == '\n':
                lines.append(cur)
                cur = ''
                continue
            if cur and font.measure(cur + ch) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
        return lines

    @staticmethod
    def _clock(ts):
        return time.strftime('%H:%M', time.localtime(ts or time.time()))

    def _wheel(self, e):
        try:
            self.msgs.yview_scroll(-1 * int(e.delta / 120), 'units')
        except tk.TclError:
            pass

    def _on_resize(self, e):
        if abs(e.width - self._canvas_w) < 2:
            return
        self._canvas_w = e.width
        self.render(scroll=False)

    # ---------------- 输入区 ----------------

    def bind_paste(self, w=None):
        """把 Ctrl+V 挂到窗口里每一个控件上。

        Tk 的绑定沿 bindtags 走，而 bindtags 里没有父控件这一环 —— 只在 Toplevel
        上绑，焦点在输入框里时根本收不到。所以老老实实递归一遍，
        免得「有时候能粘有时候不能」这种最难查的毛病。
        """
        w = self.top if w is None else w
        for seq in ('<Control-v>', '<Control-V>'):
            try:
                w.bind(seq, self.on_paste)
            except tk.TclError:
                pass
        try:
            kids = w.winfo_children()
        except tk.TclError:
            return
        for child in kids:
            self.bind_paste(child)

    def on_paste(self, _e=None):
        """Ctrl+V。返回 'break' = 这一下归我，None = 交回系统去粘文字。

        剪贴板里是截图 / 复制的图片 / 资源管理器里复制的文件，就直接发出去；
        是纯文字就什么也不做 —— 输入框本来就能粘，抢过来只会有害。
        """
        if self.on_media is None or not self.alive():
            return None
        items = media.clipboard_items()
        if not items:
            return None
        extra = len(items) - media.PASTE_MAX
        items = items[:media.PASTE_MAX]
        note = ''
        for i, (mt, payload) in enumerate(items):
            # 一次粘贴只让第一件占冷却额度：不然第二件必然撞上「还要等 5 秒」
            why = self.on_media(mt, payload, i > 0)
            if why:
                note = why
                break
        if not note and extra > 0:
            note = '一次最多发 %d 个，多出来的没发' % media.PASTE_MAX
        self.set_note(note)
        return 'break'

    def _on_icon(self, kind):
        if kind == 'emoji':
            return self._toggle_stickers()
        if self.on_media is None:
            return
        if kind == 'image':
            types = [('图片', '*.png *.jpg *.jpeg *.gif *.bmp *.webp'),
                     ('所有文件', '*.*')]
            title = '选一张图片'
        else:
            types = [('所有文件', '*.*')]
            title = '选一个文件'
        path = filedialog.askopenfilename(parent=self.top, title=title,
                                          filetypes=types)
        if not path:
            return
        self._send_media('img' if kind == 'image' else 'file', path)

    def _send_media(self, mt, payload):
        why = self.on_media(mt, payload) if self.on_media else ''
        if why:
            self.set_note(why)
        else:
            self.set_note('')

    def _toggle_stickers(self):
        if self._pop is not None:
            self._pop_closed()
            return
        groups = self.picker_groups()
        if not any(g[1] for g in groups):
            self.set_note('没有可用的表情')
            return
        btn = self.icon_btn['emoji']
        self._pop = _PickerPop(self.top, groups, self._send_media,
                               btn.winfo_rootx(), btn.winfo_rooty())

    def picker_groups(self):
        """表情面板的三页：[('表情', items, 格子, 列数), ('我的', ...), ('他人', ...)]。

        一项是 (mt, payload, 缩略图)：mt='emoji' 时 payload 是那个字符，
        mt='sticker' 时是卡片 PNG 的路径。

        为什么分三页而不是混在一格格里：它们是三种不同的东西 ——
        通用反应（大家都一样）、我自己那张脸、别人那张脸。
        「他人」这页是把另外三个角色的卡片收在一起：想替别人演一下、
        或者单纯觉得那张图好笑，都能直接发出去（发出去就是一张图片）。
        """
        emojis = []
        for ch in media.EMOJI_CHARS:
            ph = media.emoji_photo(ch, 34)
            if ph is not None:
                emojis.append(('emoji', ch, ph))

        def cards_of(role):
            out = []
            for path in media.sticker_paths(assets_dir(), role):
                ph = media.thumb(path, 56)
                if ph is not None:
                    out.append(('sticker', path, ph))
            return out

        mine = cards_of(self.me)
        others = []
        for role in ROLES:
            if role != self.me:
                others.extend(cards_of(role))
        return [('表情', emojis, 46, 6), ('我的', mine, 68, 4),
                ('他人', others, 68, 5)]

    def _pop_closed(self, _e=None):
        if self._pop is not None:
            try:
                self._pop.destroy()
            except tk.TclError:
                pass
            self._pop = None

    def set_note(self, text):
        self.note.configure(text=text or ' ')

    def note_text(self):
        return self.note.cget('text')

    def send(self):
        text = self.field.get().strip()
        if not text:
            return
        # 回调返回空 = 发出去了；返回一句话 = 没发出去（冷却 / 离线 / 对方不在线）
        why = self.on_send(text)
        if why:
            self.set_note(why)
            return
        self.field.entry.delete(0, 'end')
        self.set_note('')

    # ---------------- 显示与关闭 ----------------

    def show(self):
        self._place(2)
        self.top.deiconify()
        self.top.lift()
        self._top_on = True
        try:
            self.top.attributes('-topmost', True)
            self.top.focus_force()
        except tk.TclError:
            pass

    # ---------------- 和别的窗口的层级 ----------------
    #
    # 面板以前是「永远置顶」的。结果是：用户点了微信也翻不上来 —— 这么大一块
    # 压在人家窗口上面，根本没法并排干活（用户实测反馈）。
    # 现在只有「面板自己在前台」时才置顶；切到别的窗口（微信、浏览器，甚至
    # 桌宠本体）就退回普通层让位。再点回面板时立刻插回置顶带 —— 桌宠本身是
    # 置顶的，面板不插队就会被它盖住。

    def hwnd(self):
        """面板的顶层句柄。winfo_id() 给的是 Tk 的子窗口，要先升到根。"""
        try:
            return root_of(int(self.top.winfo_id()))
        except (tk.TclError, ValueError):
            return 0

    def owns(self, hwnd):
        """这个前台窗口算不算「面板自己」：面板本体，或者它的表情浮层。"""
        r = root_of(hwnd)
        if not r:
            return False
        if r == self.hwnd():
            return True
        pop = self._pop
        if pop is not None:
            try:
                if r == root_of(int(pop.winfo_id())):
                    return True
            except (tk.TclError, ValueError):
                pass
        return False

    def follow(self, fg):
        """跟着前台窗口走：是我就置顶，不是就退成普通窗口。返回现在是否置顶。"""
        on = self.owns(fg)
        if on == self._top_on:
            return on
        self._top_on = on
        try:
            self.top.attributes('-topmost', on)
        except tk.TclError:
            return on
        if on:
            try:
                self.top.lift()
            except tk.TclError:
                pass
        else:
            # 表情浮层是另一个独立置顶窗，面板都让位了它还浮在上面很怪
            self._pop_closed()
        return on

    def focus_input(self):
        try:
            self.field.focus()
            self.field.entry.icursor('end')
        except tk.TclError:
            pass

    def alive(self):
        try:
            return self.top is not None and bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        self._pop_closed()
        super().close()
        if self.on_close is not None:
            cb, self.on_close = self.on_close, None
            cb()
