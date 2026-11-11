# -*- coding: utf-8 -*-
"""认领界面与传话小窗。

界面是纯手绘的 —— Windows 上 tk 的默认 Button / Radiobutton 是灰蓝渐变的老式外观，
和桌面宠物这种「轻量、低噪音」的调性不搭。这里统一成：
白底、1px 浅灰描边、单一强调色、圆角、无立体感，窗口做成无边框卡片。

角色卡片直接把素材里的 wave.png 摆出来选 —— 认身份这件事，看图比看名字直观。
服务器地址是写死的（见 config.SERVER_HOST），所以这里没有「填地址」这一步。
"""

import os
import tkinter as tk

from PIL import Image, ImageOps, ImageTk

from .config import ROLE_NAME, ROLES, assets_dir

# ---------------- 配色 ----------------
WINDOW = '#ffffff'
BORDER = '#e4e7ef'
FG = '#111827'
SUB = '#6b7280'
MUTED = '#9ca3af'
LINE = '#e5e7eb'
FIELD = '#f7f8fb'

ACCENT = '#5b5bf0'
ACCENT_DOWN = '#4a4ade'
ACCENT_SOFT = '#f1f2ff'
ACCENT_EDGE = '#b9bbff'

OK_FG = '#0f9d76'
OK_BG = '#e9f8f2'
TAKEN_FG = '#a8aeba'
TAKEN_BG = '#f1f2f5'

ERR = '#e5484d'

FONT = ('Microsoft YaHei UI', 10)
FONT_M = ('Microsoft YaHei UI', 10, 'bold')
FONT_T = ('Microsoft YaHei UI', 19, 'bold')
FONT_S = ('Microsoft YaHei UI', 9)
FONT_XS = ('Microsoft YaHei UI', 8)

CARD_W, CARD_H = 134, 208
CARD_GAP = 13
PAD_X = 28                  # 卡片窗内容区左右内边距（认领窗和标题栏共用）
EDGE_W = 1                  # 卡片窗外面那圈描边（body 的 padx）


# ---------------- 绘图小工具 ----------------

def _rr(canvas, x0, y0, x1, y1, r, **kw):
    """圆角矩形。Tk 没有原生圆角，用 smooth 多边形近似 —— 半径小的时候足够圆。"""
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
           x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
           x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


class CardWindow:
    """无边框卡片窗：顶部一条可拖动的标题栏 + 右下角关闭。

    去掉系统标题栏是为了不像 2005 年的安装程序；代价是要自己补上拖动和关闭。
    """

    def __init__(self, parent, width):
        self.parent = parent
        self.width = width

        top = tk.Toplevel(parent)
        self.top = top
        top.withdraw()
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        top.configure(bg=BORDER)

        self.body = tk.Frame(top, bg=WINDOW)
        self.body.pack(fill='both', expand=True, padx=1, pady=1)

        head = tk.Frame(self.body, bg=WINDOW)
        head.pack(fill='x', padx=18, pady=(14, 0))
        self.head = head

        dot = tk.Canvas(head, width=18, height=18, bg=WINDOW,
                        highlightthickness=0, bd=0)
        dot.create_oval(3, 3, 15, 15, fill=ACCENT, outline='')
        dot.create_oval(7, 7, 11, 11, fill='#ffffff', outline='')
        dot.pack(side='left')

        brand = tk.Label(head, text='pet-club', bg=WINDOW, fg=SUB,
                         font=('Microsoft YaHei UI', 10, 'bold'))
        brand.pack(side='left', padx=(7, 0))

        close = tk.Canvas(head, width=26, height=26, bg=WINDOW,
                          highlightthickness=0, bd=0, cursor='hand2')
        close.create_text(13, 12, text='✕', fill=MUTED,
                          font=('Microsoft YaHei UI', 11))
        close.pack(side='right')
        close.bind('<Button-1>', lambda e: self.cancel())
        close.bind('<Enter>', lambda e: close.itemconfig(1, fill=FG))
        close.bind('<Leave>', lambda e: close.itemconfig(1, fill=MUTED))
        self._close = close

        for w in (head, dot, brand):
            w.bind('<Button-1>', self._drag_start)
            w.bind('<B1-Motion>', self._drag)

        self.content = tk.Frame(self.body, bg=WINDOW)
        self.content.pack(fill='both', expand=True, padx=PAD_X, pady=(18, 24))

        self._dx = self._dy = 0
        top.bind('<Escape>', lambda e: self.cancel())

    # ---- 拖动 ----

    def _drag_start(self, e):
        self._dx = e.x_root - self.top.winfo_x()
        self._dy = e.y_root - self.top.winfo_y()

    def _drag(self, e):
        self.top.geometry('+%d+%d' % (e.x_root - self._dx, e.y_root - self._dy))

    # ---- 显示 ----

    def _place(self, ydiv=3):
        """按内容算出高度，摆到屏幕中间（水平永远居中）。

        ydiv 控制纵向位置：2 = 正中间，3 = 偏上（认领窗）。
        坐标直接用 self.width 和算出来的 h，**不要回头去读 winfo_width()** ——
        窗口还没映射的时候那个值不是刚设的宽度（实测读到 720，窗口就偏左了）。
        """
        self.top.update_idletasks()
        h = self.body.winfo_reqheight() + 2
        sw = self.top.winfo_screenwidth()
        sh = self.top.winfo_screenheight()
        x = max(0, (sw - self.width) // 2)
        y = max(0, (sh - h) // ydiv)
        self.top.geometry('%dx%d+%d+%d' % (self.width, h, x, y))

    def _show(self, ydiv=3):
        """模态显示：摆好 + 置顶 + 抢焦点 + 独占输入。"""
        self._place(ydiv)
        self.top.deiconify()
        try:
            self.top.lift()
            self.top.focus_force()
            self.top.grab_set()
        except tk.TclError:
            pass

    def wait(self):
        self.top.wait_window()

    def cancel(self):
        self.close()

    def close(self):
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class RoundButton(tk.Canvas):
    """圆角按钮。primary=实心强调色，ghost=浅灰底。"""

    def __init__(self, parent, text, cmd, primary=True, width=None, height=38):
        w = width or (len(text) * 15 + 36)
        super().__init__(parent, width=w, height=height, bg=WINDOW,
                         highlightthickness=0, bd=0, cursor='hand2')
        self.cmd = cmd
        self.primary = primary
        self.w, self.h = w, height
        self._enabled = True
        self._hover = False
        self._bg = _rr(self, 1, 1, w - 1, height - 1, height // 2,
                       fill=self._fill(), outline='')
        self._tx = self.create_text(w / 2, height / 2 + 1, text=text,
                                    fill='#ffffff' if primary else FG, font=FONT_M)
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
        self.bind('<Button-1>', self._on_press)
        self.bind('<ButtonRelease-1>', self._on_release)

    def _fill(self):
        if not self._enabled:
            return '#c9cbf2' if self.primary else '#f3f4f7'
        if self.primary:
            return ACCENT_DOWN if self._hover else ACCENT
        return '#eceef3' if self._hover else FIELD

    def _repaint(self):
        self.itemconfig(self._bg, fill=self._fill())
        self.itemconfig(self._tx,
                        fill='#ffffff' if self.primary else (FG if self._enabled else MUTED))

    def set_enabled(self, on):
        if on == self._enabled:
            return
        self._enabled = bool(on)
        self.configure(cursor='hand2' if on else 'arrow')
        self._repaint()

    def _on_enter(self, _e=None):
        if self._enabled:
            self._hover = True
            self._repaint()

    def _on_leave(self, _e=None):
        self._hover = False
        self._repaint()

    def _on_press(self, _e=None):
        if self._enabled:
            self.move(self._tx, 0, 1)

    def _on_release(self, e=None):
        self.move(self._tx, 0, -1)
        if not (self._enabled and self.cmd):
            return
        # 鼠标拖出按钮再松开不该触发（和原生按钮一致）
        if e is not None and hasattr(e, 'x') and not (0 <= e.x <= self.w
                                                      and 0 <= e.y <= self.h):
            return
        self.cmd()


def _preview(role, dim=False):
    """把 wave.png 缩放成卡片用的预览图。素材缺失就返回 None，卡片退化成纯文字。"""
    p = os.path.join(assets_dir(), 'roles', role, 'wave.png')
    box_w, box_h = CARD_W - 22, 122
    try:
        im = Image.open(p).convert('RGBA')
        im.thumbnail((box_w, box_h), Image.LANCZOS)
        if dim:
            grey = ImageOps.grayscale(im)
            alpha = im.split()[3].point(lambda v: int(v * 0.45))
            im = Image.merge('RGBA', (grey, grey, grey, alpha))
        return im
    except (OSError, ValueError):
        return None


class _RoleCard(tk.Canvas):
    """身份卡片：立绘 + 名字 + 状态。已认领的灰掉且不可点。"""

    def __init__(self, parent, role, photo, dim_photo, on_pick):
        super().__init__(parent, width=CARD_W, height=CARD_H, bg=WINDOW,
                         highlightthickness=0, bd=0, cursor='hand2')
        self.role = role
        self.photo = photo
        self.dim_photo = dim_photo
        self.on_pick = on_pick
        self.taken = False
        self.selected = False
        self._hover = False
        self._img = None

        self.bind('<Button-1>', self._click)
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.redraw()

    def _click(self, _e=None):
        if not self.taken:
            self.on_pick(self.role)

    def _enter(self, _e=None):
        if not self.taken:
            self._hover = True
            self.redraw()

    def _leave(self, _e=None):
        self._hover = False
        self.redraw()

    def set_state(self, taken=None, selected=None):
        if taken is not None:
            self.taken = taken
        if selected is not None:
            self.selected = selected
        self.configure(cursor='arrow' if self.taken else 'hand2')
        self.redraw()

    def redraw(self):
        self.delete('all')
        if self.taken:
            bg, edge, name_fg = TAKEN_BG, '#eceef2', TAKEN_FG
        elif self.selected:
            bg, edge, name_fg = ACCENT_SOFT, ACCENT, ACCENT
        elif self._hover:
            bg, edge, name_fg = '#fafaff', ACCENT_EDGE, FG
        else:
            bg, edge, name_fg = '#ffffff', LINE, FG

        _rr(self, 1.5, 1.5, CARD_W - 1.5, CARD_H - 1.5, 16, fill=bg,
            outline=edge, width=2 if self.selected else 1)

        im = self.dim_photo if self.taken else self.photo
        if im is not None:
            self._img = ImageTk.PhotoImage(im)
            # 和桌面一致：底部居中，脚踩在同一条线上
            self.create_image(CARD_W / 2, 12 + 122, image=self._img, anchor='s')
        else:
            _rr(self, CARD_W / 2 - 26, 46, CARD_W / 2 + 26, 118, 10,
                fill='#eceef5' if self.taken else '#e8eaff', outline='')
            self.create_text(CARD_W / 2, 82, text=ROLE_NAME.get(self.role, '?')[:1],
                             fill=MUTED, font=('Microsoft YaHei UI', 18, 'bold'))

        self.create_text(CARD_W / 2, 156, text=ROLE_NAME.get(self.role, self.role),
                         fill=name_fg, font=('Microsoft YaHei UI', 11, 'bold'))

        pill_fg, pill_bg, label = ((TAKEN_FG, '#e6e8ee', '已认领') if self.taken
                                  else (OK_FG, OK_BG, '可认领'))
        _rr(self, CARD_W / 2 - 30, 172, CARD_W / 2 + 30, 194, 11,
            fill=pill_bg, outline='')
        self.create_text(CARD_W / 2, 183, text=label, fill=pill_fg, font=FONT_XS)


class _Field:
    """带描边的输入框。聚焦时描边变强调色。"""

    def __init__(self, parent, value='', width=None):
        self.box = tk.Frame(parent, bg=LINE)
        self.box.pack(fill='x')
        inner = tk.Frame(self.box, bg=WINDOW)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        self.entry = tk.Entry(inner, font=FONT, relief='flat', bg=WINDOW, fg=FG,
                              insertbackground=FG, highlightthickness=0, bd=0)
        self.entry.pack(fill='x', ipady=8, padx=8)
        if value:
            self.entry.insert(0, value)
        self.entry.bind('<FocusIn>', lambda e: self.box.configure(bg=ACCENT))
        self.entry.bind('<FocusOut>', lambda e: self.box.configure(bg=LINE))

    def get(self):
        return self.entry.get()

    def focus(self):
        self.entry.focus_set()
        self.entry.select_range(0, 'end')


class ClaimDialog(CardWindow):
    """第二步：认领身份。几张立绘里挑一个空着的。

    已被认领的身份显示为灰色且不可点 —— 「不能重复认领」在界面上就看得见，
    不用等提交后被服务端拒一次才知道。
    """

    def __init__(self, parent, players=None, note='', on_poll=None):
        super().__init__(parent,
                         len(ROLES) * CARD_W + (len(ROLES) - 1) * CARD_GAP
                         + 2 * PAD_X + 2 * EDGE_W)
        self.result = None
        self._players = players or {}
        self._selected = None
        self.on_poll = on_poll
        self._poll_id = None

        tk.Label(self.content, text='认领一个身份', bg=WINDOW, fg=FG,
                 font=FONT_T).pack(anchor='w')
        self.sub = tk.Label(self.content, text='', bg=WINDOW, fg=SUB, font=FONT_S,
                            justify='left', wraplength=self.width - 60)
        self.sub.pack(anchor='w', pady=(6, 0))

        row = tk.Frame(self.content, bg=WINDOW)
        row.pack(pady=(18, 0))          # 居中：左右边距由窗口宽度均分
        self.cards = {}
        for i, role in enumerate(ROLES):
            photo = _preview(role)
            dim = _preview(role, dim=True)
            c = _RoleCard(row, role, photo, dim, self._pick)
            c.grid(row=0, column=i, padx=(0 if i == 0 else CARD_GAP, 0))
            self.cards[role] = c

        self.note = tk.Label(self.content, text=note or ' ', bg=WINDOW, fg=ERR,
                             font=FONT_S, justify='left',
                             wraplength=self.width - 60, height=1, anchor='w')
        self.note.pack(fill='x', pady=(16, 0))

        btns = tk.Frame(self.content, bg=WINDOW)
        btns.pack(fill='x', pady=(10, 0))
        self.ok_btn = RoundButton(btns, '认领这个身份', self.ok, primary=True,
                                  width=140)
        self.ok_btn.pack(side='right')
        RoundButton(btns, '退出', self.cancel, primary=False, width=76).pack(
            side='right', padx=(0, 8))
        self.ok_btn.set_enabled(False)

        self.set_players(self._players)
        self.top.protocol('WM_DELETE_WINDOW', self.cancel)
        self.top.bind('<Return>', lambda e: self.ok())
        self._show(ydiv=3)
        if self.on_poll:
            self._poll_id = self.top.after(400, self._poll_loop)

    def _poll_loop(self):
        """认领窗是模态的，主循环此刻停着 —— 自己定时去拉一次状态，
        别人先认领 / 刚好离开时，卡片能当场变色，不用等提交被拒。"""
        self._poll_id = None
        try:
            self.on_poll()
        except tk.TclError:
            return
        except Exception:
            return
        if self.top.winfo_exists():
            self._poll_id = self.top.after(400, self._poll_loop)

    def close(self):
        if self._poll_id is not None:
            try:
                self.top.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        super().close()

    # ---- 状态 ----

    def set_players(self, players):
        self._players = players or {}
        sel = self._selected
        for role, card in self.cards.items():
            taken = bool(self._players.get(role))
            card.set_state(taken=taken, selected=(role == sel and not taken))
        if sel and self._players.get(sel):
            # 刚选好就被别人抢走了 —— 界面立刻退回未选中，别等提交
            self._selected = None
            self.ok_btn.set_enabled(False)
            self._warn('「%s」刚被别人认领了，换一个吧' % ROLE_NAME.get(sel, sel))
        else:
            self.ok_btn.set_enabled(bool(sel))
        online = sum(1 for v in self._players.values() if v)
        if online >= len(ROLES):
            text = '身份都被认领了　·　在线 %d/%d，等有人离开' % (online, len(ROLES))
        else:
            text = '已被认领的不能再选　·　在线 %d/%d' % (online, len(ROLES))
        self.sub.configure(text=text)

    def _pick(self, role):
        if self._players.get(role):
            return
        self._selected = role
        for r, card in self.cards.items():
            card.set_state(selected=(r == role))
        self.note.configure(text=' ')
        self.ok_btn.set_enabled(True)

    def _warn(self, text):
        self.note.configure(text=text)

    def set_note(self, text):
        """外部改那一行提示（比如刚掉线）—— 和校验错误共用同一行。"""
        self.note.configure(text=text)

    def note_text(self):
        """现在显示的提示（含校验错误）。只读，给调用方判断「要不要清掉」。"""
        return self.note.cget('text')

    def ok(self):
        role = getattr(self, '_selected', None)
        if not role:
            return self._warn('先选一个身份')
        if self._players.get(role):
            return self._warn('这个身份刚被别人认领了，换一个吧')
        self.result = role
        self.close()

    def show(self):
        self.wait()
        return self.result
