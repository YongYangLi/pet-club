# -*- coding: utf-8 -*-
"""托盘图标。

【为什么要自己写 ctypes，不用 pystray】
  pystray 要多装一个包、多一条 PyInstaller 隐藏依赖，而它做的事归根到底就是
  Shell_NotifyIcon 三行调用。桌宠是个 23MB 的单文件 exe，为一个图标再加一层
  依赖不划算。这里只用到 user32 的几个函数，和 surface.py 里那套 ctypes 同源。

【图标怎么收到点击】
  Shell_NotifyIcon 是把消息 POST 给某个窗口的。所以要先有一个窗口：
  一个 HWND_MESSAGE 的隐藏窗口（不显示、不进任务栏、不参与 Z 序）。

  消息谁来派发都行，两条路都留着：
    · Tk 在 Windows 上的消息泵会 DispatchMessage 到任何窗口 —— 这时走 WndProc；
    · 万一它没派发（Tk 内部实现不保证），主线程 tick 里的 PeekMessage 按 hwnd
      过滤，自己把消息抠出来。
  两条路都只是把事件塞进 self._events，真正的活儿留给主线程的 poll()。

【为什么回调里不直接开窗口】
  WndProc 是在 DispatchMessage 里被调进来的，上下文是 Windows 的消息派发，
  在这里开 Tk 窗口属于「在别人的栈上干重活」，很容易出怪事。统一入队、
  下一帧在主线程 tick 里处理，和网络层那条纪律一样。

【这个模块只认消息，不认界面】
  左键开聊天、右键开菜单都是调用方（App）的事。托盘只管「谁被点了、
  点的时候鼠标在哪儿」—— 所以这里一个 tkinter 都不需要。

【未读怎么提示】
  托盘图标没有「气泡」可用（气泡通知会进操作中心、还带声音，对一句聊天
  太重），所以用最老实的办法：两张图标交替闪 —— 原图标 / 右上角带红点的。
  `blink(True)` 交给调用方按节拍调，闪的节奏归界面（App），这里只管换图。
"""

import ctypes
import os
from ctypes import wintypes

from .surface import IS_WIN

# ---- 常量（WinUser.h / ShellApi.h） ----

WM_NULL = 0x0000
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 20       # 自定的回调消息号，只要在 WM_APP 之后就不会撞系统消息
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B         # 新版 Windows 的右键通知

PM_REMOVE = 0x0001
HWND_MESSAGE = -3

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

TIP_MAX = 127                   # szTip 是 128 个 WCHAR，得留一个给结尾的 0


if IS_WIN:
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    WNDPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASS(ctypes.Structure):
        _fields_ = [('style', wintypes.UINT),
                    ('lpfnWndProc', WNDPROC),
                    ('cbClsExtra', ctypes.c_int),
                    ('cbWndExtra', ctypes.c_int),
                    ('hInstance', wintypes.HINSTANCE),
                    ('hIcon', wintypes.HICON),
                    ('hCursor', wintypes.HANDLE),
                    ('hbrBackground', wintypes.HBRUSH),
                    ('lpszMenuName', wintypes.LPCWSTR),
                    ('lpszClassName', wintypes.LPCWSTR)]

    class NOTIFYICONDATA(ctypes.Structure):
        """Vista+ 的完整形状（976 字节）。字段顺序不能动，cbSize 得是它的大小。"""
        _fields_ = [('cbSize', wintypes.DWORD),
                    ('hWnd', wintypes.HWND),
                    ('uID', wintypes.UINT),
                    ('uFlags', wintypes.UINT),
                    ('uCallbackMessage', wintypes.UINT),
                    ('hIcon', wintypes.HICON),
                    ('szTip', wintypes.WCHAR * 128),
                    ('dwState', wintypes.DWORD),
                    ('dwStateMask', wintypes.DWORD),
                    ('szInfo', wintypes.WCHAR * 256),
                    ('uVersion', wintypes.UINT),
                    ('szInfoTitle', wintypes.WCHAR * 64),
                    ('dwInfoFlags', wintypes.DWORD),
                    ('guidItem', ctypes.c_byte * 16),
                    ('hBalloonIcon', wintypes.HICON)]

    class POINT(ctypes.Structure):
        _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]

    # 参数类型必须显式声明：64 位下光靠 ctypes 猜，句柄会被截成 32 位
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = wintypes.LPARAM
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                  wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HICON
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                          ctypes.POINTER(NOTIFYICONDATA)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL


def cursor_pos():
    """鼠标现在在屏幕哪儿。拿不到就返回 None（调用方自己兜底）。"""
    if not IS_WIN:
        return None
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return pt.x, pt.y


class Tray:
    """一个托盘图标。

    icon：.ico 路径（找不到就用系统的默认图标，图标总比没有强）。
    alert：有未读时交替显示的那张图标；给不出来就退化成「只换 tip」。
    on_left / on_right：`(x, y) -> None`，在**主线程**里被调用。
    """

    def __init__(self, icon=None, tip='pet-club', on_left=None, on_right=None,
                 alert=None):
        self.on_left = on_left
        self.on_right = on_right
        self.ok = False
        self.hwnd = None
        self._events = []
        self._nid = None
        self._icon = None           # 常态图标句柄
        self._alert = None          # 未读图标句柄（可能为 None）
        self._shown = None          # 现在挂的是哪一个，避免重复 NIM_MODIFY
        self._class_atom = None
        self._wndproc = None            # 必须一直拿着：被 GC 掉窗口就崩
        if not IS_WIN:
            return
        try:
            self._create(icon, tip, alert)
            self.ok = True
        except Exception as e:          # 托盘起不来不该拖垮整个程序
            print('[pet-club] 托盘图标起不来：%r' % (e,))
            self._teardown()

    # ---------------- 建 ----------------

    def _create(self, icon, tip, alert=None):
        hinst = kernel32.GetModuleHandleW(None)
        name = 'pet-club-tray-%d' % os.getpid()
        self._wndproc = WNDPROC(self._dispatch)

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = name
        self._class_atom = user32.RegisterClassW(ctypes.byref(wc))
        if not self._class_atom:
            # 同一个进程里第二次注册同名类会失败，但那说明窗口还在，不该算错
            err = ctypes.get_last_error()
            if err not in (0, 1410):    # 1410 = ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(err)

        self.hwnd = user32.CreateWindowExW(
            0, name, name, 0, 0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE),
            None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        self._icon = self._load_icon(icon)
        # 未读图标拿不到不算失败：闪不起来而已，图标本身还是好的
        self._alert = self._load_icon(alert) if alert else None

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._icon
        nid.szTip = (tip or 'pet-club')[:TIP_MAX]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._nid = nid
        self._shown = self._icon

    @staticmethod
    def _load_icon(path):
        if path and os.path.exists(path):
            h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return h
        # 退回系统的「应用程序」图标：MAKEINTRESOURCE 就是把整数当指针传
        return user32.LoadIconW(None, ctypes.cast(
            ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR))

    # ---------------- 换图 ----------------

    @property
    def can_blink(self):
        """有没有第二张图可以换。没有的话调用方别白闪。"""
        return bool(self.ok and self._alert)

    def blink(self, on):
        """这一拍该显示哪张图标。on=True 是「有未读」那张。

        返回现在挂的是不是未读图标。同一张连着设不会真的调系统 ——
        闪烁每秒要调两次，没必要每次都让资源管理器重画。
        """
        if not self.ok:
            return False
        want = self._alert if (on and self._alert) else self._icon
        if want is None or want == self._shown:
            return bool(on and self._alert)
        nid = self._nid
        nid.hIcon = want
        nid.uFlags = NIF_ICON
        if shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            self._shown = want
        return bool(on and self._alert)

    def set_tip(self, text):
        """悬停提示。只在文案真的变了的时候才通知系统。"""
        if not self.ok:
            return
        text = (text or 'pet-club')[:TIP_MAX]
        if self._nid.szTip == text:
            return
        self._nid.szTip = text
        self._nid.uFlags = NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    # ---------------- 收消息 ----------------

    def _dispatch(self, hwnd, msg, wp, lp):
        """窗口过程。只入队，不碰 Tk —— 见模块开头的说明。"""
        if msg == WM_TRAYICON:
            self._events.append(int(lp))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def poll(self):
        """主线程每帧调一次：把攒下的点击取出来执行。"""
        if not IS_WIN or not self.hwnd:
            return
        # 万一 Tk 的消息泵没替我们派发，这里自己把属于这个窗口的消息抠出来
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, PM_REMOVE):
            if msg.message == WM_TRAYICON:
                self._events.append(int(msg.lParam))
        while self._events:
            self._handle(self._events.pop(0))

    def _handle(self, lp):
        if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            cmd = self.on_left
        elif lp in (WM_RBUTTONUP, WM_CONTEXTMENU):
            cmd = self.on_right
        else:
            return
        if cmd is None:
            return
        pos = cursor_pos() or (0, 0)
        try:
            cmd(*pos)
        except Exception as e:          # 面板/菜单炸了不该带走图标
            print('[pet-club] 托盘响应失败：%r' % (e,))

    # ---------------- 撤 ----------------

    def close(self):
        if self.ok and self._nid is not None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
        self.ok = False
        self._teardown()

    def _teardown(self):
        if self.hwnd:
            try:
                user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None
        self._nid = None
