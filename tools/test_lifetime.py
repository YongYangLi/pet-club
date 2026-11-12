# -*- coding: utf-8 -*-
"""Qt 对象生命周期的防回归检查。

1.4.0 的打包版在托盘菜单点任意一项后堆损坏（WER 报告 0xc0000374）：
菜单的 aboutToHide 把 App 对 QMenu 的唯一 Python 引用清成 None，
_close_menu 又 deleteLater —— shiboken 删一次、Qt 的延迟删除再删一次。
这里把「信号派发栈上不许丢引用」钉成断言。

跑法：py tools/test_lifetime.py
"""
import ast
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

from petclub.app import App  # noqa: E402
from petclub.claim import ClaimDialog  # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + str(detail) if detail and not ok else '')),
          flush=True)


def main():
    print('--- 源码里不许再出现 deleteLater 调用 ---')
    bad = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'petclub', '*.py'))):
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=path)
        # 只认真实的调用节点：注释、docstring 里提到这个名字不算数，
        # 但写在任意表达式里的 .deleteLater() 一个也跑不掉。
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'deleteLater'):
                bad.append('%s:%d' % (os.path.basename(path), node.lineno))
    chk('所有 QObject 只由 Python 引用计数析构', not bad, bad)

    print('\n--- 菜单关掉以后引用还在 ---')
    app = App('127.0.0.1', 1)
    last = None
    for i in range(6):
        app.tray_right()
        m = app._menu
        chk('第 %d 轮菜单建出来了' % (i + 1), m is not None)
        if m is None:
            break
        m.close()                     # 真实点击时 Qt 先发 aboutToHide
        app.qapp.processEvents()
        chk('第 %d 轮关掉后 App 仍攥着 QMenu' % (i + 1),
            app._menu is not None)
        chk('第 %d 轮关掉后没有残留的刷新定时器' % (i + 1),
            not hasattr(app, '_menu_timer') and not hasattr(app, '_menu_probes'))
        last = m
    chk('最后一轮的对象就是当前引用', app._menu is last)
    chk('上一份菜单多留了一拍', app._menu_prev is not None)
    chk('关闭状态用 _menu_open 记录', app._menu_open is False)

    print('\n--- 不认领也能退出 ---')
    dlg = ClaimDialog(None, {}, '')
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    chk('认领窗有「退出」按钮', '退出' in texts, texts)
    QTimer.singleShot(0, dlg.reject)
    chk('点退出让 exec() 返回 0（App 据此退出进程）', dlg.exec() == 0)

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for name in FAIL:
        print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
