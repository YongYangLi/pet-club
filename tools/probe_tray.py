# -*- coding: utf-8 -*-
"""托盘：图标、闪烁、点击路由。

这里能验的是「该切哪张图、该回调谁」；「图标在右下角长得对不对」
要一双眼睛，这里不假装能测。

跑法：py tools/probe_tray.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QSystemTrayIcon        # noqa: E402

from petclub import tray as T                                     # noqa: E402

ICON = os.path.join(ROOT, 'assets', 'app.ico')
ALERT = os.path.join(ROOT, 'assets', 'app_alert.ico')

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    if detail and not isinstance(detail, str):
        detail = repr(detail)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''),
          flush=True)


def main():
    QApplication.instance() or QApplication([])   # 托盘控件要用

    print('--- 图标素材 ---')
    chk('主图标在 assets 里', os.path.exists(ICON), ICON)
    chk('闪烁用的红点图标在 assets 里', os.path.exists(ALERT), ALERT)

    hits = []
    t = T.Tray(icon=ICON, alert=ALERT, tip='pet-club 测试',
               on_left=lambda: hits.append('L'),
               on_right=lambda: hits.append('R'))
    chk('托盘起来了（系统托盘可用）', t.ok, 'isSystemTrayAvailable()')
    chk('主图标加载成功（不是空图标）', not t._icon.isNull())
    chk('红点图标加载成功', not t._alert.isNull())
    chk('两张图都在，才谈得上闪', t.can_blink)

    print('\n--- 闪烁 ---')
    chk('闪起来时切到红点那张', t.blink(True) is True)
    chk('亮着的时候内部状态是 on', t._on)
    chk('停下时切回主图标', t.blink(False) is False)
    chk('停下后内部状态是 off', not t._on)
    chk('没有红点图时也能调用（不会崩）',
        T.Tray(icon=ICON, tip='x').blink(True) is False)

    print('\n--- 点击路由 ---')
    t._activated(QSystemTrayIcon.Trigger)
    chk('左键 → 打开聊天', hits == ['L'], hits)
    t._activated(QSystemTrayIcon.Context)
    chk('右键 → 弹菜单', hits == ['L', 'R'], hits)
    t._activated(QSystemTrayIcon.DoubleClick)
    chk('双击不触发（Qt 会先给两次 Trigger，再给一次 DoubleClick）',
        hits == ['L', 'R'], hits)
    t.set_tip('pet-club 测试 · 1 条未读')
    chk('换提示不报错', True)

    print('\n--- 菜单铺设 ---')
    fired = []
    rows = [('第一项', lambda: fired.append(1), True),
            None,
            ('灰的', None),
            ('不可点的回调', lambda: fired.append(2), False),
            ('能点的', lambda: fired.append(3), True)]
    menu, acts = T.build_menu(None, rows)
    chk('分隔行不建 action', len(acts) == 4, len(acts))
    chk('第一项可点', acts[0].isEnabled())
    chk('cmd=None 的那行是灰的', not acts[1].isEnabled(), acts[1].text())
    chk('显式 enabled=False 也是灰的', not acts[2].isEnabled())
    acts[0].trigger()
    chk('点了就回调', fired == [1], fired)
    acts[2].trigger()
    chk('灰的那行进不了回调', fired == [1], fired)
    acts[3].trigger()
    chk('最后一项也能点', fired == [1, 3], fired)
    menu.close()

    print('\n--- 收摊 ---')
    t.close()
    chk('close 以后不再 ok', not t.ok)
    chk('close 以后 blink 是空操作', t.blink(True) is False)

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
