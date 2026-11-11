# -*- coding: utf-8 -*-
"""自己画的右键菜单。

【为什么不直接用 tk.Menu】
  Windows 上弹一个 tk.Menu 走的是系统那套 TrackPopupMenu —— 菜单显示期间它自己
  转事件循环，Tk 的 after 一个都不触发（实测：弹出期间定时器不跑、桌宠也冻住）。
  也就是说菜单里那句「（5 秒）」是弹出那一刻的快照，盯着它看到 0 也不会变，
  等满 5 秒再点还是灰的。这就是「倒计时不自动刷新」的根因，靠 tk.Menu 改不掉。

  自己画就全在自己手里：定时器照跑，倒计时每秒往下走，冷却一过同一份菜单里那
  一条当场变成可点。顺手还有两个好处：样式和认领窗统一（白底、1px 描边、
  圆角高亮），不再有系统灰蓝菜单混在桌宠里；菜单开着的时候角色还能继续动。

【怎么收起来】
  Esc / 点中某一项 / 窗口失焦（点了别的程序）/ 调用方 close()。
  桌宠窗口是 overrideredirect 的，点它不会让菜单失焦 —— 那一下由 App 在角色的
  按下事件里先 close()，见 App.on_pet_press。

【一份菜单就是一个对象】
  不弹出来的时候它只是个「行列表」，labels() / items() 就能取到文案和能不能点 ——
  探针不用真弹窗也能验菜单内容。
"""

import time
import tkinter as tk

from .surface import work_area
from .ui import ACCENT_SOFT, BORDER, FG, LINE, MUTED, SUB, WINDOW

FONT_ITEM = ('Microsoft YaHei UI', 9)
FONT_HEAD = ('Microsoft YaHei UI', 8)

PAD_X = 12              # 行内左右留白
PAD_Y = 4               # 每个可点行的上下留白
TICK_MS = 200           # 倒计时刷新节拍：够跟手，也不至于一直在重排
GAP = 8                 # 菜单和它要躲开的那个窗口之间的空隙


class PopMenu:
    """一个右键菜单。链式建行 → popup() → 自己管刷新和关闭。"""

    def __init__(self, root, width=196, on_close=None):
        self.root = root
        self.width = width
        self.on_close = on_close
        self.top = None
        self._rows = []         # 行模型：{'kind':'head'|'item'|'sep', ...}
        self._views = []        # 与 _rows 同序的控件（弹出来之后才有）
        self._tick_id = None
        self._open = False
        self._now = time.monotonic

    # ---------------- 建 ----------------

    def head(self, text):
        """灰标题行：不可点，用来写「对谁」「在线几个人」这类说明。"""
        self._rows.append({'kind': 'head', 'text': text})
        return self

    def sep(self):
        self._rows.append({'kind': 'sep'})
        return self

    def item(self, text, cmd=None, probe=None):
        """可点的一行。

        probe(now) -> (文案, 能不能点)：冷却里的「（3 秒）」就是这么来的，
        每 TICK_MS 重算一次，所以数字会自己往下走。
        不给 probe 就是固定文案、永远可点。
        """
        self._rows.append({'kind': 'item', 'text': text,
                           'cmd': cmd, 'probe': probe})
        return self

    # ---------------- 内容查询（探针用，不用弹窗） ----------------

    def state(self, row, now=None):
        now = self._now() if now is None else now
        if row['kind'] != 'item':
            return row.get('text') or '', False
        if row['probe'] is not None:
            return row['probe'](now)
        return row['text'], row['cmd'] is not None

    def labels(self, now=None):
        """所有非分隔行的文案（含灰标题）。"""
        return [self.state(r, now)[0] for r in self._rows if r['kind'] != 'sep']

    def items(self, now=None):
        """[(文案, 能不能点)]，只含可点的行。"""
        return [self.state(r, now) for r in self._rows if r['kind'] == 'item']

    def rendered(self):
        """界面上此刻真正显示的字（读控件，而不是重算）—— 验刷新用。"""
        out = []
        for v in self._views:
            if v.get('kind') in ('item', 'head'):
                out.append(v['label'].cget('text'))
        return out

    def invoke(self, label, now=None):
        """按文案点一下。冷却中返回 False（和真人点灰行一个结果）。"""
        now = self._now() if now is None else now
        for row in self._rows:
            if row['kind'] != 'item':
                continue
            text, on = self.state(row, now)
            if text == label:
                if not on:
                    return False
                self._run(row['cmd'])
                return True
        return False

    @property
    def is_open(self):
        return self._open

    # ---------------- 弹出来 ----------------

    def popup(self, x, y, avoid=None):
        """在 (x, y) 弹出。avoid 是一个矩形 (l, t, r, b)：菜单要躲开它。

        右键菜单天然出现在鼠标那儿，而鼠标正点在角色身上 —— 菜单又高又宽，
        直接压下去等于把角色整个盖住。所以给了 avoid 就挪到角色旁边。
        """
        self.close()                    # 同一时刻只留一份
        top = tk.Toplevel(self.root)
        self.top = top
        top.withdraw()
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        top.configure(bg=BORDER)

        body = tk.Frame(top, bg=WINDOW)
        body.pack(fill='both', expand=True, padx=1, pady=1)

        self._views = [self._make_row(body, row) for row in self._rows]
        top.update_idletasks()

        h = body.winfo_reqheight() + 2
        x, y = self._where(x, y, h, avoid)
        top.geometry('%dx%d+%d+%d' % (self.width, h, x, y))
        top.deiconify()
        top.lift()

        self._open = True
        self.refresh()
        top.bind('<Escape>', lambda e: self.close())
        top.bind('<FocusOut>', lambda e: self.close())
        try:
            top.focus_force()           # 失焦就关，所以得先真的拿到焦点
        except tk.TclError:
            pass
        self._tick_id = top.after(TICK_MS, self._tick)
        return self

    def _where(self, x, y, h, avoid):
        """算出菜单的左上角。

        有要躲的矩形时按「右 → 左 → 上 → 下」试，选第一个装得下的方向；
        都没地方就在鼠标那儿将就（总比跑出屏幕强）。
        """
        left, top, right, bottom = work_area()
        w = self.width

        def clamp(cx, cy):
            cx = min(max(cx, left + 2), max(left + 2, right - w - 2))
            cy = min(max(cy, top + 2), max(top + 2, bottom - h - 2))
            return cx, cy

        if avoid is None:
            return clamp(x, y - 6)

        al, at, ar, ab = avoid
        tries = (
            # (候选左上角, 装得下吗)：贴边那两侧只要横向/纵向净空够就行，
            # 另一个方向允许夹到工作区里 —— 菜单只要不压在角色身上就达到目的
            ((ar + GAP, ab - h), right - (ar + GAP) >= w),      # 右边，底边对齐
            ((al - w - GAP, ab - h), (al - GAP) - left >= w),   # 左边，底边对齐
            ((x - w // 2, at - h - GAP), (at - GAP) - top >= h),  # 上边
            ((x - w // 2, ab + GAP), bottom - (ab + GAP) >= h),  # 下边
        )
        for (cx, cy), fits in tries:
            if fits:
                return clamp(cx, cy)
        return clamp(x, y - 6)

    def _make_row(self, body, row):
        if row['kind'] == 'sep':
            f = tk.Frame(body, bg=LINE, height=1)
            f.pack(fill='x', padx=10, pady=3)
            return {'kind': 'sep'}

        if row['kind'] == 'head':
            lab = tk.Label(body, text=row['text'], bg=WINDOW, fg=SUB,
                           font=FONT_HEAD, anchor='w', justify='left',
                           wraplength=self.width - 2 * PAD_X)
            lab.pack(fill='x', padx=PAD_X, pady=(4, 1))
            return {'kind': 'head', 'label': lab, 'text': row['text']}

        frame = tk.Frame(body, bg=WINDOW)
        frame.pack(fill='x')
        lab = tk.Label(frame, text=row['text'], bg=WINDOW, fg=FG,
                       font=FONT_ITEM, anchor='w', padx=PAD_X, pady=PAD_Y)
        lab.pack(fill='x')
        view = {'kind': 'item', 'frame': frame, 'label': lab,
                'text': None, 'enabled': None}
        for w in (frame, lab):
            w.bind('<Enter>', lambda e, v=view: self._hover(v, True))
            w.bind('<Leave>', lambda e, v=view: self._hover(v, False))
            w.bind('<Button-1>', lambda e, v=view: self._click(v))
        return view

    # ---------------- 交互 ----------------

    def _row_of(self, view):
        for row, v in zip(self._rows, self._views):
            if v is view:
                return row
        return None

    def _hover(self, view, on):
        if not view.get('enabled'):
            return
        bg = ACCENT_SOFT if on else WINDOW
        view['frame'].configure(bg=bg)
        view['label'].configure(bg=bg)

    def _click(self, view):
        if not view.get('enabled'):
            return
        row = self._row_of(view)
        self._run(row['cmd'] if row else None)

    def _run(self, cmd):
        """先关菜单再执行 —— 菜单项里可能要开别的窗口（认领窗、传话窗），
        菜单还压在上面会挡住它。"""
        self.close()
        if cmd is None:
            return
        try:
            cmd()
        except Exception as e:          # 菜单项炸了不该带走整个程序
            print('[pet-club] 菜单项执行失败：%r' % (e,))

    # ---------------- 刷新与关闭 ----------------

    def refresh(self, now=None):
        if not self._open:
            return
        now = self._now() if now is None else now
        for row, view in zip(self._rows, self._views):
            if view.get('kind') != 'item':
                continue
            text, on = self.state(row, now)
            if text != view['text']:
                view['label'].configure(text=text)
                view['text'] = text
            if on != view['enabled']:
                view['enabled'] = on
                view['label'].configure(fg=FG if on else MUTED,
                                        cursor='hand2' if on else 'arrow')
                if not on:
                    self._hover(view, False)

    def _tick(self):
        self._tick_id = None
        if not self._open:
            return
        self.refresh()
        try:
            self._tick_id = self.top.after(TICK_MS, self._tick)
        except tk.TclError:
            pass

    def close(self):
        was = self._open
        self._open = False
        if self._tick_id is not None and self.top is not None:
            try:
                self.top.after_cancel(self._tick_id)
            except tk.TclError:
                pass
        self._tick_id = None
        if self.top is not None:
            try:
                self.top.destroy()
            except tk.TclError:
                pass
            self.top = None
        self._views = []
        if was and self.on_close is not None:
            cb, self.on_close = self.on_close, None
            cb()
