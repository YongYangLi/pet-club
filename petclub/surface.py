# -*- coding: utf-8 -*-
"""逐像素透明的分层窗口渲染层（Windows）。

「分层窗口」（WS_EX_LAYERED + UpdateLayeredWindow）是唯一能做出真正抗锯齿边缘的
透明方案：给 Windows 一张 alpha 已预乘的 BGRA 位图，它按 alpha 逐像素合成到桌面。
色键（-transparentcolor）做不到 —— 那是窗口级的，任何等于键色的像素都会被挖掉，
多只宠物重叠时会互相啃出矩形破洞。

移植自 desk-pet-lite，保留三个踩过坑的点：
  1. HWND 必须「设置后读回校验」——猜错句柄会让分层静默失效（黑块 / 不显示）
  2. RGB 必须预乘 alpha，否则半透明边缘发白
  3. 落点坐标必须是 int —— Image.paste 与 UpdateLayeredWindow 都不收浮点
"""

import ctypes
import sys
from ctypes import wintypes

from PIL import Image, ImageChops

IS_WIN = sys.platform == 'win32'

if IS_WIN:
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

    WS_EX_LAYERED = 0x00080000
    WS_EX_TOOLWINDOW = 0x00000080
    GWL_EXSTYLE = -20
    ULW_ALPHA = 0x02
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01
    BI_RGB = 0
    SPI_GETWORKAREA = 0x0030

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [('BlendOp', ctypes.c_ubyte),
                    ('BlendFlags', ctypes.c_ubyte),
                    ('SourceConstantAlpha', ctypes.c_ubyte),
                    ('AlphaFormat', ctypes.c_ubyte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [('bmiHeader', BITMAPINFOHEADER),
                    ('bmiColors', wintypes.DWORD * 3)]

    class RECT(ctypes.Structure):
        _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                    ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

    _GetWindowLong = getattr(user32, 'GetWindowLongPtrW', None) or user32.GetWindowLongW
    _SetWindowLong = getattr(user32, 'SetWindowLongPtrW', None) or user32.SetWindowLongW

    # 64 位下必须显式声明签名：HWND / HDC 是 64 位指针，ctypes 默认按 c_int 传会截断，
    # 结果是句柄错掉、分层窗口静默失效。
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                             ctypes.c_void_p, wintypes.UINT]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetWindowLong.restype = ctypes.c_ssize_t
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SetWindowLong.restype = ctypes.c_ssize_t
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL

    def _make_layered(wid):
        """把窗口设为分层窗口，返回真正生效的 HWND（失败返回 None）。

        Tk 的 toplevel 在 Windows 上通常就是单个顶层窗口，winfo_id() 直接可用；
        但某些版本外面还套了一层 wrapper，所以逐个候选试，并「设置后读回校验」。
        猜错句柄会导致分层静默失效（窗口变黑块或不显示），所以必须验证。
        """
        cands = []
        parent = user32.GetParent(wid)
        if parent:
            cands.append(parent)
        if wid not in cands:
            cands.append(wid)
        for h in cands:
            style = _GetWindowLong(h, GWL_EXSTYLE)
            _SetWindowLong(h, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
            if _GetWindowLong(h, GWL_EXSTYLE) & WS_EX_LAYERED:
                return h
        return None

    def work_area():
        """主显示器工作区（排除任务栏），失败则回退全屏。"""
        r = RECT()
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right, r.bottom
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    # ---------------- 置顶带 ----------------
    #
    # Windows 的「置顶」不是一条等级，而是一整条带子：所有 WS_EX_TOPMOST 的窗口
    # 挤在里面，谁最后被激活谁排前面。Tk 的 -topmost 只在设置那一刻插一次队，
    # 之后浏览器、资源管理器、任何一个自己置顶的程序被激活，就会插到我们前面，
    # 而且再也没有人把我们放回去 —— 这就是「勾着始终置顶还是被盖住」的成因。
    # 所以需要能主动再插一次队，并且能先问一句「我现在是不是被压着」。

    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_NOOWNERZORDER = 0x0200
    GA_ROOT = 2

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

    def raise_top(hwnd):
        """把窗口重新插回置顶带最前面：不挪位置、不改尺寸、不抢焦点。"""
        if not hwnd:
            return False
        return bool(user32.SetWindowPos(
            hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER))

    def root_of(hwnd):
        """任意窗口 → 它所在的顶层窗口。Tk 的 winfo_id() 给的是子窗口，
        要比前台句柄就得先升到根，否则永远比自己。"""
        if not hwnd:
            return 0
        root = user32.GetAncestor(wintypes.HWND(int(hwnd)), GA_ROOT)
        return int(root or hwnd)

    def owner_at(x, y, me):
        """屏幕点 (x, y) 上压着的窗口属于谁。

        返回 'me'（就是我们这个窗口，露着）/ 'own'（同一进程的别的窗口：
        聊天面板、右键菜单、别的宠物）/ 'other'（别人的程序盖在上面）。

        (x, y) 必须取不透明像素：分层窗口的透明部分不参与命中测试，
        取到透明处会一路穿透到桌面，被误判成「被盖住了」。
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

    def foreground():
        """当前前台窗口的句柄，用来发现「用户切到别的应用了」。"""
        return user32.GetForegroundWindow()

    def visible(hwnd):
        return bool(user32.IsWindowVisible(hwnd))

else:
    def work_area():
        return 0, 0, 1280, 720

    def raise_top(hwnd):
        return False

    def root_of(hwnd):
        return 0

    def owner_at(x, y, me):
        return 'me'

    def foreground():
        return None

    def visible(hwnd):
        return False


class LayeredSurface:
    """把一个 Toplevel 变成逐像素透明的分层窗口。"""

    def __init__(self, top):
        if not IS_WIN:
            raise RuntimeError('逐像素透明仅支持 Windows')
        top.update_idletasks()
        self.hwnd = _make_layered(top.winfo_id())
        if not self.hwnd:
            raise RuntimeError('无法把窗口设为分层窗口')
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self._bmp = None
        self._bits = None
        self._size = None

    def _ensure(self, w, h):
        if self._size == (w, h):
            return
        if self._bmp:
            gdi32.DeleteObject(self._bmp)
        bi = BITMAPINFO()
        hdr = bi.bmiHeader
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth = w
        hdr.biHeight = -h               # 负值 = 自上而下，与 PIL 的行序一致
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        self._bmp = gdi32.CreateDIBSection(self.screen_dc, ctypes.byref(bi), 0,
                                           ctypes.byref(bits), None, 0)
        gdi32.SelectObject(self.mem_dc, self._bmp)
        self._bits = bits
        self._size = (w, h)

    def _push(self, x, y):
        w, h = self._size
        pt_dst = wintypes.POINT(int(x), int(y))
        size = wintypes.SIZE(int(w), int(h))
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        return user32.UpdateLayeredWindow(
            self.hwnd, self.screen_dc, ctypes.byref(pt_dst), ctypes.byref(size),
            self.mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)

    def set_image(self, img, x, y):
        """上传一张 RGBA 图（未预乘）并同时设定窗口位置。"""
        w, h = img.size
        self._ensure(w, h)
        # alpha 预乘：分层窗口要求 RGB 已经乘过 alpha，否则半透明边缘会发白
        r, g, b, a = img.split()
        rgb = ImageChops.multiply(Image.merge('RGB', (r, g, b)),
                                  Image.merge('RGB', (a, a, a)))
        data = Image.merge('RGBA', rgb.split() + (a,)).tobytes('raw', 'BGRA')
        ctypes.memmove(self._bits, data, len(data))
        return self._push(int(x), int(y))

    def move(self, x, y):
        """位图不变，只挪窗口（省掉一次预乘与上传）。"""
        if self._size is None:
            return
        self._push(int(x), int(y))
