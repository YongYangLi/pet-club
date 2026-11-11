# -*- coding: utf-8 -*-
"""Windows 上 Qt 自己搞不定的几件事：工作区、前台窗口、z 序。

Qt 负责画，这几件事 Qt 没有等价物（或者说等价的代价更大），所以还是直调
user32 —— 但**只是查询和小动作**，没有任何渲染、没有消息泵、没有窗口类：

  work_area()      屏幕工作区（排除任务栏），角色落地要用
  foreground()     当前前台窗口，用来发现「用户切到别的应用了」
  owner_at(x, y)   屏幕这一点上压着谁的窗口，用来判断角色是不是被盖住了
  raise_top(hwnd)  重新插回置顶带最前面
  set_topmost()    改置顶属性（不重建窗口）

非 Windows 一律给安全的退化值：桌面宠物只在 Windows 上有完整实现，
但这几个函数被 import 时不至于炸。
"""
import ctypes
import sys
from ctypes import wintypes

IS_WIN = sys.platform == 'win32'

if IS_WIN:
    user32 = ctypes.WinDLL('user32', use_last_error=True)

    SPI_GETWORKAREA = 0x0030
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    GA_ROOT = 2
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_NOOWNERZORDER = 0x0200

    class RECT(ctypes.Structure):
        _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                    ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

    # 句柄是 64 位指针，显式声明签名，否则 ctypes 会按 c_int 截断
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                             ctypes.c_void_p, wintypes.UINT]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    MY_PID = _kernel32.GetCurrentProcessId()

    def work_area():
        """主显示器工作区（排除任务栏），失败则回退全屏。物理像素。"""
        r = RECT()
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right, r.bottom
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    def root_of(hwnd):
        """任意窗口 → 它所在的顶层窗口。"""
        if not hwnd:
            return 0
        root = user32.GetAncestor(wintypes.HWND(int(hwnd)), GA_ROOT)
        return int(root or hwnd)

    def foreground():
        return user32.GetForegroundWindow()

    def owner_at(x, y, me):
        """屏幕点 (x, y) 上压着的窗口属于谁。

        返回 'me'（就是我们这个窗口，露着）/ 'own'（同进程的别的窗口：
        聊天面板、菜单、别的宠物）/ 'other'（别人的程序盖在上面）。
        (x, y) 必须取不透明像素 —— 透明处会穿透到桌面，被误判成「被盖住」。
        """
        h = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not h:
            return 'other'
        root = root_of(h)
        if root == me:
            return 'me'
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        return 'own' if pid.value == MY_PID else 'other'

    def raise_top(hwnd):
        """插回置顶带最前面：不挪位置、不改尺寸、不抢焦点。"""
        if not hwnd:
            return False
        return bool(user32.SetWindowPos(
            wintypes.HWND(int(hwnd)), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER))

    def set_topmost(hwnd, on):
        """改置顶属性，**不重建窗口**。

        Qt 的 setWindowFlag(WindowStaysOnTopHint) 会销毁并重建原生窗口 ——
        输入框焦点、滚动位置全丢，每秒调两次根本没法用。
        """
        if not hwnd:
            return False
        return bool(user32.SetWindowPos(
            wintypes.HWND(int(hwnd)),
            wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))

    def visible(hwnd):
        return bool(user32.IsWindowVisible(int(hwnd)))

else:
    def work_area():
        return 0, 0, 1280, 720

    def root_of(hwnd):
        return 0

    def foreground():
        return None

    def owner_at(x, y, me):
        return 'me'

    def raise_top(hwnd):
        return False

    def set_topmost(hwnd, on):
        return False

    def visible(hwnd):
        return False
