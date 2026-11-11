# -*- coding: utf-8 -*-
"""平台差异的唯一入口。

界面层（app / chat / windows）只从这里问「屏幕多大、谁在前台、这个人被谁压着」，
不再直接 import win32 —— 那样每加一个平台就要改一遍业务代码。

**坐标约定**：这里对外的屏幕坐标一律是**物理像素**（`work_area`、`owner_at`
的入参）。窗口尺寸、素材尺寸、命中点都是物理的，只有 Qt 的窗口位置是逻辑的，
换算留在 windows.py 里做，一处。

桌面宠物的核心能力在三个平台上不是同一套 API，所以每项都分工：
  Windows  → win32.py（user32，已实测）
  macOS    → macos.py（PyObjC，拿不到就退化，见那个文件开头的说明）
  其他     → 全部安全退化，能起来、位置对，但没有置顶巡检
"""
import os
import sys

from . import win32

IS_WIN = sys.platform == 'win32'
IS_MAC = sys.platform == 'darwin'

if IS_MAC:
    from . import macos as _mac
else:
    _mac = None

# 系统界面字体：中文字形要好看，三个平台各有各的正解
FONTS = {'win32': 'Microsoft YaHei UI',
         'darwin': 'PingFang SC'}


def _hwnd(widget):
    """Qt 控件 → 它所在的顶层窗口句柄（Windows）。"""
    return win32.root_of(int(widget.winId()))


# ---------------- 屏幕 ----------------

def work_area():
    """主屏工作区（排除任务栏 / 菜单栏 / Dock），物理像素，(左, 上, 右, 下)。"""
    if IS_WIN:
        return win32.work_area()
    # 非 Windows 用 Qt 自己算，比调原生 API 可靠：macOS 的 visibleFrame
    # 和 Qt 的 availableGeometry 走的是同一份数据，但 Qt 已经把「菜单栏 + Dock」
    # 和 Retina 缩放都处理好了。
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    if screen is None:
        return 0, 0, 1280, 720
    r = screen.availableGeometry()
    dpr = screen.devicePixelRatio() or 1.0
    return (int(r.left() * dpr), int(r.top() * dpr),
            int(r.right() * dpr), int(r.bottom() * dpr))


# ---------------- 前台 / 遮挡 ----------------

def foreground():
    """当前前台窗口的「令牌」。Windows 是 HWND，macOS 是窗口号。

    令牌对调用方不透明，只能原样交给 is_foreground()。拿不到就返回 None，
    调用方据此保持现状（而不是误以为「用户切走了」）。
    """
    if IS_WIN:
        return win32.foreground()
    if IS_MAC and _mac is not None:
        return _mac.foreground_window()
    return None


def is_foreground(token, *widgets):
    """这几扇窗里有没有哪一扇就是前台窗口（令牌是 foreground() 给的）。"""
    if token is None:
        return True                 # 不知道谁在前台，别乱动层级
    if IS_WIN:
        return any(_hwnd(w) == token for w in widgets if w is not None)
    if IS_MAC and _mac is not None:
        return any(_mac.window_number(w) == token
                   for w in widgets if w is not None)
    return True


def owner_at(x, y, widget):
    """屏幕物理点 (x, y) 上压着的窗口属于谁：'me' / 'own' / 'other'。"""
    if IS_WIN:
        return win32.owner_at(x, y, _hwnd(widget))
    if IS_MAC and _mac is not None:
        return _mac.owner_at(x, y, widget)
    return 'me'                     # 不知道就当没被盖住，省掉无意义的重排


def raise_top(widget):
    """把窗口插回置顶带最前面：不挪位置、不改尺寸、不抢焦点。"""
    if IS_WIN:
        return win32.raise_top(_hwnd(widget))
    if IS_MAC and _mac is not None:
        return _mac.raise_top(widget)
    return False


def set_topmost(widget, on):
    """改置顶属性，**不重建窗口**（Qt 的 setWindowFlag 会重建，见 win32 注释）。"""
    if IS_WIN:
        return win32.set_topmost(_hwnd(widget), on)
    if IS_MAC and _mac is not None:
        return _mac.set_topmost(widget, on)
    return False


# ---------------- 资源位置 ----------------

def ui_font():
    return FONTS.get(sys.platform, 'Sans Serif')


def data_dir():
    """打包后放用户数据的根目录（聊天附件等）。"""
    if IS_WIN:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    elif IS_MAC:
        base = os.path.join(os.path.expanduser('~'), 'Library',
                            'Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.join(
            os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'pet-club')


def tray_icons(assets):
    """托盘用的两张图标（常态 / 有未读）。mac 是菜单栏尺寸的 PNG。"""
    if IS_MAC:
        names = ('tray_mac.png', 'tray_mac_alert.png')
    else:
        names = ('app.ico', 'app_alert.ico')
    return (os.path.join(assets, names[0]), os.path.join(assets, names[1]))


def can_self_update():
    """这个平台能不能自己换掉自己。

    Windows 靠「退出后由一个 .bat 换文件」，已实测。
    macOS 要换的是一个 .app 目录，还要过 Gatekeeper，那条路没验证过，
    所以先关掉：宁可让用户手动下新版本，也不要写一个会删掉自己 App 的实验品。
    """
    return IS_WIN
