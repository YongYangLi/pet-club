# -*- coding: utf-8 -*-
"""生成文档用的界面预览图（docs/preview/）。

用 `widget.grab()` 而不是截屏：锁屏、远程桌面、多显示器都不影响，
拿到的就是这个控件自己画出来的那张位图。

    py tools/shot_ui.py

不出网、不连服务器：起一个 App 然后把会话记录直接填进去。
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from PySide6.QtWidgets import QApplication                 # noqa: E402

import petclub.app as APP                                  # noqa: E402
from petclub.chat import CONV_ALL                          # noqa: E402
from petclub.claim import ClaimDialog                      # noqa: E402
from petclub.config import ROLE_NAME, ROLES                # noqa: E402

OUT = os.path.join(ROOT, 'docs', 'preview')

SAMPLES = [
    (CONV_ALL, 'tip', '胡大神', '胡大神 跟杨大师打了个招呼', 0),
    (CONV_ALL, 'msg', 'p2', '今晚谁还在？', 60),
    (CONV_ALL, 'msg', 'me', '我在，刚吃完饭', 120),
    (CONV_ALL, 'msg', 'p3', '等我十分钟，马上到', 200),
    (CONV_ALL, 'tip', '你', '你 提醒大家喝了口水', 260),
    (CONV_ALL, 'msg', 'p4', '哈哈哈哈这个表情包好使', 320),
    ('p2', 'msg', 'p2', '你那边能听到吗', 400),
    ('p2', 'msg', 'me', '能，很清楚', 460),
    ('p3', 'msg', 'p3', '我先撤了，明天见', 520),
]


def save(widget, name):
    widget.adjustSize() if hasattr(widget, 'adjustSize') else None
    pm = widget.grab()
    path = os.path.join(OUT, name)
    pm.save(path)
    print('  %s  %dx%d' % (name, pm.width(), pm.height()), flush=True)
    return path


def seed(app):
    """填一点会话记录：群聊热闹、单聊有来有回、还有一条系统提示。"""
    base = time.time() - 3600
    for key, kind, who, text, off in SAMPLES:
        app.chat_log.setdefault(key, []).append({
            'kind': kind, 'who': who, 'text': text, 'ts': base + off})


def main():
    os.makedirs(OUT, exist_ok=True)
    qapp = QApplication.instance() or QApplication([])

    app = APP.App('127.0.0.1', 1)      # 不 connect：这里只要界面
    app.me = {'role': ROLES[0]}
    app.players = {ROLES[0]: {'role': ROLES[0]}, ROLES[1]: {'role': ROLES[1]},
                   ROLES[2]: None, ROLES[3]: None, ROLES[4]: None}
    seed(app)

    print('聊天面板')
    chat = app.open_chat(CONV_ALL)
    chat.resize(chat.width(), chat.height())
    qapp.processEvents()
    save(chat, 'chat_top.png')

    # 多聊几句再截一张：滚到底那版用来看气泡、时间分隔、系统提示
    for i in range(6):
        app.cd.reset()
        app.chat_log[CONV_ALL].append({
            'kind': 'msg', 'who': 'p3', 'text': '第 %d 句：这张图我发你看' % (i + 1),
            'ts': time.time() + i})
    chat.render()
    qapp.processEvents()
    save(chat, 'chat.png')

    print('表情拾取面板')
    pop = chat._picker_pop()
    chat._pop = pop
    pop.show()
    qapp.processEvents()
    save(pop, 'chat_stickers.png')
    chat._fill_page('others')
    qapp.processEvents()
    save(pop, 'chat_cards.png')
    pop.close()
    chat._pop = None

    print('右键菜单')
    menu = app.build_self_menu()
    menu.adjustSize()
    qapp.processEvents()
    save(menu, 'menu.png')
    menu.close()

    print('认领窗')
    players = {ROLES[1]: {'role': ROLES[1]}, ROLES[3]: {'role': ROLES[3]}}
    dlg = ClaimDialog(None, players, '')
    dlg.show()
    qapp.processEvents()
    dlg._pick(ROLES[0])
    qapp.processEvents()
    save(dlg, 'claim.png')
    dlg.close()

    dlg2 = ClaimDialog(None, players, '「%s」刚被人认领了，换一个吧'
                       % ROLE_NAME[ROLES[0]])
    dlg2.show()
    qapp.processEvents()
    save(dlg2, 'claim_rejected.png')
    dlg2.close()

    app._closing = True
    for p in app.pets.values():
        p.win.hide()
    print('完成：%s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
