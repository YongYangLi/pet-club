# -*- coding: utf-8 -*-
"""聊天窗口重排版的防回归检查。

覆盖：窗口三按钮（关窗 ≠ 退程序）、可缩放与最大化、发送按钮文字完整、
自绘工具图标、表情放大与悬停预览、悬浮快捷互动条的自适应与「更多」。

跑法：py tools/test_chat_ui.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from petclub import actions, media  # noqa: E402
from petclub.app import App  # noqa: E402
from petclub.chat import (CELL, MIN_H, MIN_W, ChatWindow, CONV_ALL,  # noqa: E402
                          ToolIcon)
from petclub.config import ROLES  # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + str(detail) if detail and not ok else '')),
          flush=True)


def visible_chips(chat):
    return [k for k, b in chat._chips.items() if b.isVisible()]


def main():
    qapp = QApplication.instance() or QApplication([])
    calls = []

    print('--- 发送按钮 / 工具图标 ---')
    chat = ChatWindow(None, me='p1', on_action=lambda k, t: calls.append((k, t)))
    chat.show()
    qapp.processEvents()
    qapp.processEvents()
    b = chat.send_btn
    fm = QFontMetrics(b.font())
    chk('「发送」文字横向放得下',
        fm.horizontalAdvance('发送') + 16 < b.width(),
        (fm.horizontalAdvance('发送'), b.width()))
    chk('「发送」文字纵向放得下', fm.height() + 8 < b.height(),
        (fm.height(), b.height()))
    chk('发送按钮显式清掉了继承来的 padding', 'padding: 0' in b.styleSheet())
    chk('三个工具图标都在',
        set(chat.icon_btn) == {'emoji', 'image', 'file'}, list(chat.icon_btn))
    chk('工具图标是自绘矢量而不是字符',
        all(isinstance(v, ToolIcon) for v in chat.icon_btn.values()))
    chk('工具图标比旧的 26px 大', chat.icon_btn['emoji'].width() >= 34)

    print('\n--- 窗口按钮：只关窗，不退程序 ---')
    app = App('127.0.0.1', 1)
    app.me = {'role': ROLES[0]}
    live = app.open_chat(CONV_ALL)
    qapp.processEvents()
    chk('右上角三个按钮齐全',
        live.min_btn is not None and live.max_btn is not None
        and live.close_btn is not None)
    chk('第三颗是关闭', live.close_btn.kind == 'close')
    chk('第一颗是最小化', live.min_btn.kind == 'min')
    chk('第二颗是最大化', live.max_btn.kind == 'max')

    live.dismiss()
    qapp.processEvents()
    chk('点关闭只是隐藏窗口', not live.isVisible())
    chk('关闭后进程没被标记退出', not app._closing)
    chk('关闭后窗口对象还在、下次能复用', app.chat is live)
    chk('隐藏后 alive() 为假', not live.alive())
    chk('隐藏后新消息会算未读', not app._watching(CONV_ALL))
    again = app.open_chat(CONV_ALL)
    qapp.processEvents()
    chk('再点托盘复用同一个窗口而不是新建', again is live and live.isVisible())

    live.minimize()
    qapp.processEvents()
    chk('最小化不退出程序', not app._closing)
    chk('最小化后 alive() 为假', not live.alive())
    live.lift()
    qapp.processEvents()
    chk('lift() 能从最小化恢复', live.isVisible() and not live.isMinimized())

    print('\n--- 缩放与快捷条自适应 ---')
    chk('窗口有最小尺寸', live.minimumWidth() >= MIN_W
        and live.minimumHeight() >= MIN_H)
    chk('右下角有拖动把手', live._grip is not None)
    live.resize(MIN_W + 20, MIN_H + 20)
    qapp.processEvents()
    live._reflow_actions()
    narrow = len(visible_chips(live))
    chk('窄窗口下摆了若干快捷按钮', narrow > 0, narrow)
    chk('窄窗口下剩下的收进更多', len(live._more_keys) == 10 - narrow,
        (narrow, len(live._more_keys)))
    chk('「更多」按钮可见', live.more_btn.isVisible())
    live._show_more()
    qapp.processEvents()
    chk('「更多」浮层弹出来了', live._more_pop.isVisible())
    chk('浮层里的按钮数 = 收起来的个数',
        live._more_grid.count() == len(live._more_keys),
        (live._more_grid.count(), len(live._more_keys)))
    live._more_pop.hide()

    live.resize(1500, 820)
    qapp.processEvents()
    live._reflow_actions()
    wide = len(visible_chips(live))
    chk('窗口拉宽后快捷按钮变多', wide > narrow, (narrow, wide))
    chk('拉宽后「更多」收得更少', len(live._more_keys) < 10 - narrow)

    live.toggle_max()
    qapp.processEvents()
    chk('最大化生效', live.isMaximized())
    chk('最大化时按钮变成还原', live.max_btn.kind == 'restore')
    chk('最大化时贴边（阴影留白归零）',
        live._outer.contentsMargins().left() == 0)
    live.toggle_max()
    qapp.processEvents()
    chk('再点一次还原', not live.isMaximized() and live.max_btn.kind == 'max')

    print('\n--- 快捷互动真的发出去 ---')
    chk('快捷条覆盖全部互动指令', set(live._chips) == set(actions.ORDER))
    chk('快捷条接的是 App 的互动执行', live.on_action == app.do_action)
    chk('快捷条接的是 App 的冷却状态', live.action_state == app.action_state)
    chk('群聊下目标提示正确', '全体群聊' in live.target_lb.text())
    live.switch('p2')
    qapp.processEvents()
    chk('切到单聊后目标提示跟着变', '胡大神' in live.target_lb.text())
    chk('切会话后快捷条刷新目标', live._action_target() == 'p2')

    print('\n--- 冷却状态 ---')
    state = {'on': False, 'cd': 4}
    cool = ChatWindow(None, me='p1', on_action=lambda k, t: calls.append((k, t)),
                      action_state=lambda k, t: ('X', state['on'], state['cd']))
    qapp.processEvents()
    chk('冷却中的 chip 不可点', not cool._chips['wave'].isEnabled())
    chk('剩余秒数画成角标', cool._chips['wave']._cd == 4)
    chk('角标不算进文字宽度（文字仍是原标签）',
        cool._chips['wave'].text() == actions.get('wave')['label'])
    state.update(on=True, cd=0)
    cool.refresh_actions()
    chk('冷却结束恢复可点', cool._chips['wave'].isEnabled())
    cool._on_action('heart')
    chk('私聊/群聊目标透传正确', calls[-1] == ('heart', None), calls[-1:])
    cool.switch('p3')
    cool._on_action('pat')
    chk('切到单聊后目标变成对方', calls[-1] == ('pat', 'p3'), calls[-1:])

    print('\n--- 表情放大与悬停预览 ---')
    pop = chat._picker_pop()
    qapp.processEvents()
    cell = chat._pick_grid.itemAt(0).widget()
    chk('表情页铺满内置 emoji',
        chat._pick_grid.count() == len(media.EMOJI_CHARS),
        chat._pick_grid.count())
    chk('表情格放大到 %dpx' % CELL, cell.width() >= 50 and cell.height() >= 50)
    chat._show_preview(cell)
    qapp.processEvents()
    chk('悬停弹出放大预览', chat._preview.isVisible())
    chk('预览里是一张真正放大的图',
        not chat._preview.big.pixmap().isNull()
        and chat._preview.big.pixmap().width() >= 100)
    chk('预览下方有说明文字', '点一下发送' in chat._preview.cap.text())
    chat._hide_preview()
    chk('离开后预览收起', not chat._preview.isVisible())

    chat._fill_page('others')
    qapp.processEvents()
    chk('「他人」页是 32 张贴纸', chat._pick_grid.count() == 32,
        chat._pick_grid.count())
    card = chat._pick_grid.itemAt(0).widget()
    chat._show_preview(card)
    qapp.processEvents()
    chk('贴纸也能放大预览', not chat._preview.big.pixmap().isNull())
    chat._close_pop()
    chk('关掉弹层后预览一起收起', not chat._preview.isVisible())

    e = {'mt': 'emoji', 'char': '\U0001F600', 'text': '\U0001F600', 'who': 'p2'}
    lb = chat._content(e, False)
    chk('聊天记录里的 emoji 用大字号', lb.font().pointSize() >= 28,
        lb.font().pointSize())

    chk('「更多」浮层常驻引用', chat._more_pop is not None)
    chk('预览浮层常驻引用', chat._preview is not None)

    chat.hide()
    cool.hide()
    pop.hide()
    live.hide()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for name in FAIL:
        print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
