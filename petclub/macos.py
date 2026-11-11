# -*- coding: utf-8 -*-
"""macOS 上 Qt 自己搞不定的四件事：前台窗口、窗口层级、遮挡检测、窗口号。

**这个文件没有在有 Mac 的机器上跑过。** 写它的原则只有一条：
任何一步拿不到想要的东西，就安静地退化成「不知道」，绝不让桌宠起不来。

所以：
  · 整个模块 import 时就 try/except 包住 PyObjC —— 没装 pyobjc 也能正常
    启动，只是丢掉「被盖住后自动顶回来」这一项（Qt 的 WindowStaysOnTopHint
    已经给了 floating 层级，大部分时候够用）。
  · 每个函数各自再包一层。宁可返回 False / 'me'，也不抛异常到事件循环里。

坐标系：CGWindowList 给的是**逻辑点**（Retina 上是物理的一半），而调用方
给的是物理像素 —— 换算在这里用窗口所在屏幕的 devicePixelRatio 做。
"""
import os

AVAILABLE = False

try:
    import objc
    from AppKit import NSFloatingWindowLevel, NSNormalWindowLevel, NSWorkspace
    from Quartz import (CGWindowListCopyWindowInfo,
                        kCGNullWindowID,
                        kCGWindowBounds,
                        kCGWindowListExcludeDesktopElements,
                        kCGWindowListOptionOnScreenOnly,
                        kCGWindowNumber,
                        kCGWindowOwnerPID)

    AVAILABLE = True
except Exception:                    # noqa: BLE001 —— 缺 pyobjc / 换了 API 都走这里
    pass

_ON_SCREEN = (kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
              if AVAILABLE else 0)


def _ns_view(widget):
    """Qt 控件 → 原生 NSView。winId() 在 macOS 上就是这个指针。"""
    ptr = int(widget.winId())
    if not ptr:
        return None
    return objc.objc_object(c_void_p=ptr)


def _ns_window(widget):
    view = _ns_view(widget)
    return view.window() if view is not None else None


def window_number(widget):
    """窗口号（CGWindowID）。拿不到返回 -1。"""
    if not AVAILABLE:
        return -1
    try:
        return int(_ns_window(widget).windowNumber())
    except Exception:                # noqa: BLE001
        return -1


def _windows():
    """当前屏幕上的窗口，从前往后（最上层在前）。"""
    if not AVAILABLE:
        return []
    try:
        return list(CGWindowListCopyWindowInfo(_ON_SCREEN, kCGNullWindowID) or [])
    except Exception:                # noqa: BLE001
        return []


def foreground_window():
    """前台应用最上面那个窗口的窗口号。

    前台应用给的是 PID，要再翻一遍窗口表找它最靠前的窗口 —— macOS 没有
    「GetForegroundWindow」这种一步到位的 API。
    """
    if not AVAILABLE:
        return None
    try:
        pid = int(NSWorkspace.sharedWorkspace()
                  .frontmostApplication().processIdentifier())
    except Exception:                # noqa: BLE001
        return None
    for info in _windows():
        try:
            if int(info.get(kCGWindowOwnerPID, 0)) != pid:
                continue
        except (TypeError, ValueError):
            continue
        num = int(info.get(kCGWindowNumber, 0))
        if num:
            return num
    return None


def owner_at(x, y, widget):
    """屏幕**物理**点 (x, y) 上压着谁的窗口。返回 'me' / 'own' / 'other'。"""
    if not AVAILABLE:
        return 'me'
    try:
        dpr = float(widget.screen().devicePixelRatio() or 1.0)
        lx, ly = x / dpr, y / dpr
        mine = window_number(widget)
        my_pid = os.getpid()
        for info in _windows():      # 从前往后：第一个命中的就是压在上面的
            box = info.get(kCGWindowBounds) or {}
            try:
                bx = float(box.get('X', 0))
                by = float(box.get('Y', 0))
                bw = float(box.get('Width', 0))
                bh = float(box.get('Height', 0))
            except (TypeError, ValueError):
                continue
            if not (bx <= lx <= bx + bw and by <= ly <= by + bh):
                continue
            if int(info.get(kCGWindowOwnerPID, 0)) != my_pid:
                return 'other'
            return 'me' if int(info.get(kCGWindowNumber, 0)) == mine else 'own'
        return 'other'
    except Exception:                # noqa: BLE001
        return 'me'


def set_topmost(widget, on):
    """改窗口层级。floating 会浮在普通窗口之上，但不抢全屏应用的空间。"""
    if not AVAILABLE:
        return False
    try:
        win = _ns_window(widget)
        if win is None:
            return False
        win.setLevel_(NSFloatingWindowLevel if on else NSNormalWindowLevel)
        return True
    except Exception:                # noqa: BLE001
        return False


def raise_top(widget):
    """插回同层最前面，不激活、不抢焦点。"""
    if not AVAILABLE:
        return False
    try:
        win = _ns_window(widget)
        if win is None:
            return False
        win.setLevel_(NSFloatingWindowLevel)
        win.orderFrontRegardless()
        return True
    except Exception:                # noqa: BLE001
        return False
