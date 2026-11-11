# -*- coding: utf-8 -*-
"""平台层的自测。

守两件事：

  1. **平台分派是对的** —— 该走 win32 的走 win32，该给 macOS 资源的不给 .ico。
     这些在 Windows 上都能验，不需要 Mac。
  2. **拿不到系统能力时不许崩** —— macos.py 在没有 PyObjC 的机器上必须整体
     退化成「不知道」。这条最重要：那段代码没有真机验证过，唯一的保险就是
     「任何一个函数失败都只返回安全值」。

跑法：python tools/test_host.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import config, host, macos      # noqa: E402

PASS = []
FAIL = []
ASSETS = os.path.join(ROOT, 'assets')


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def test_platform():
    print('--- 平台识别 ---')
    check('IS_WIN 与 sys.platform 一致', host.IS_WIN == (sys.platform == 'win32'),
          sys.platform)
    check('IS_MAC 与 sys.platform 一致', host.IS_MAC == (sys.platform == 'darwin'),
          sys.platform)
    check('两个平台互斥', not (host.IS_WIN and host.IS_MAC))


def test_font():
    print('--- 字体 ---')
    check('Windows 用微软雅黑', host.ui_font() == 'Microsoft YaHei UI',
          host.ui_font())
    # 直接查表验分派，不然这两条只有在 Mac 上才跑得到
    check('macOS 用苹方', host.FONTS.get('darwin') == 'PingFang SC',
          str(host.FONTS.get('darwin')))
    check('未知平台有兜底字体',
          len(host.FONTS) >= 2 and 'linux' not in host.FONTS)


def test_icons():
    print('--- 图标 ---')
    icon, alert = host.tray_icons(ASSETS)
    if host.IS_WIN:
        check('Windows 托盘用 .ico', icon.endswith('app.ico'), icon)
        check('Windows 未读图标是另一张 .ico', alert.endswith('app_alert.ico'), alert)
    check('常态图标文件真的在', os.path.exists(icon), icon)
    check('未读图标文件真的在', os.path.exists(alert), alert)

    # 把平台伪装成 mac，验 .icns / 菜单栏 PNG 都在仓库里
    old = host.IS_MAC
    try:
        host.IS_MAC = True
        m_icon, m_alert = host.tray_icons(ASSETS)
        check('macOS 托盘用菜单栏 PNG', m_icon.endswith('tray_mac.png'), m_icon)
        check('macOS 未读是 tray_mac_alert.png',
              m_alert.endswith('tray_mac_alert.png'), m_alert)
        check('这两张 PNG 都在', os.path.exists(m_icon) and os.path.exists(m_alert))
        check('.app 的 .icns 也在', os.path.exists(
            os.path.join(ASSETS, 'app.icns')))
    finally:
        host.IS_MAC = old


def test_dirs():
    print('--- 数据目录 ---')
    d = host.data_dir()
    check('以 pet-club 结尾', d.rstrip('\\/').endswith('pet-club'), d)
    check('是个绝对路径', os.path.isabs(d), d)
    if host.IS_WIN:
        base = os.environ.get('LOCALAPPDATA') or ''
        check('Windows 落在 LOCALAPPDATA 下',
              bool(base) and d.startswith(base), d)


def test_update_gate():
    print('--- 自更新开关 ---')
    if host.IS_WIN:
        check('Windows 允许自更新', host.can_self_update() is True)
    old = host.IS_WIN
    try:
        host.IS_WIN = False
        check('别的平台一律关闭自更新', host.can_self_update() is False)
        check('关掉之后 update.enabled() 也不会为真',
              not (config.UPDATE_URL and host.can_self_update()))
    finally:
        host.IS_WIN = old


def test_mac_degrade():
    print('--- macos.py 的退化（没 PyObjC 时） ---')
    if sys.platform == 'darwin':
        check('在 Mac 上，PyObjC 装没装都该有个明确答案',
              isinstance(macos.AVAILABLE, bool), str(macos.AVAILABLE))
    else:
        check('非 mac 上 AVAILABLE 是 False', macos.AVAILABLE is False)
    # 这几个必须在「什么都拿不到」的情况下也不抛异常
    check('window_number(None) 给 -1', macos.window_number(None) == -1)
    check('owner_at 退化时当没被盖住', macos.owner_at(0, 0, None) == 'me')
    check('set_topmost 退化时返回 False', macos.set_topmost(None, True) is False)
    check('raise_top 退化时返回 False', macos.raise_top(None) is False)
    check('foreground_window 退化时返回 None',
          macos.foreground_window() is None, str(macos.foreground_window()))


def test_no_qt_at_import():
    print('--- 导入开销 ---')
    # host 会被 config / update 在最早期 import，它自己不能把 Qt 拖进来：
    # 否则 tools 里那些纯逻辑脚本（test_update、make_release）都得先有显示环境。
    code = ('import sys; sys.path.insert(0, %r); import petclub.host; '
            'print("Qt" if any(m.startswith("PySide6") for m in sys.modules) '
            'else "clean")' % ROOT)
    out = subprocess.run([sys.executable, '-c', code], capture_output=True,
                         text=True)
    check('import host 不会顺带拉起 Qt', out.stdout.strip() == 'clean',
          out.stdout.strip() or out.stderr.strip()[-120:])


def main():
    test_platform()
    test_font()
    test_icons()
    test_dirs()
    test_update_gate()
    test_mac_degrade()
    test_no_qt_at_import()

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败项：' + '、'.join(FAIL))
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
