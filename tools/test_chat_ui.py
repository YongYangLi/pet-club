# -*- coding: utf-8 -*-
"""聊天窗口重排版的防回归检查。

覆盖：圆角不透明外框（标题带与会话栏同色）、右侧白色会话平面、
窗口三按钮（关窗 ≠ 退程序）、可缩放与最大化、微信式多行输入区、
工具与发送都在输入卡片内、表情悬停高清预览、输入卡片里的快捷互动，
以及「文件先落进输入框、点发送才发」这条待发附件的路。

跑法：py tools/test_chat_ui.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl          # noqa: E402
from PySide6.QtGui import (QColor, QFontMetrics, QKeyEvent, QMouseEvent,  # noqa: E402
                           QPalette, QPixmap, QWheelEvent)
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402
from PIL import Image                                            # noqa: E402

from petclub import actions, media                               # noqa: E402
from petclub.app import App                                      # noqa: E402
from petclub.chat import (BUBBLE_MAX, CARD_BG, CELL, CELL_GAP, CHIP_H,  # noqa: E402
                          CHROME, COLS, EMOJI_FAMILY, LIST_W, MIN_H, MIN_W,
                          PREVIEW_BOX, SEL_BG, SEL_FG, TALK_BG, BubbleText,
                          ChatWindow, Composer, CONV_ALL, ImageViewer, MeBar,
                          StatePill, ToolIcon)
from petclub.config import ROLES, assets_dir                     # noqa: E402

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

    dpr = float(qapp.primaryScreen().devicePixelRatio())
    print('--- 圆角外框 / 一体化结构 ---')
    chat = ChatWindow(None, me='p1', on_action=lambda k, t: calls.append((k, t)))
    chat.show()
    qapp.processEvents()
    qapp.processEvents()
    chk('窗口不半透明（分层窗口的任务栏缩略图会花）',
        chat._flat and chat._shadow is None
        and not chat.testAttribute(Qt.WA_TranslucentBackground))
    chk('外框是 12px 圆角、没有阴影留白',
        'border-radius: 12px' in chat.card.styleSheet()
        and chat._outer.contentsMargins().left() == 0,
        chat.card.styleSheet())
    chk('顶部标题带和会话栏是同一个底色',
        CHROME in chat.side.styleSheet()
        and 'transparent' in chat.titlebar.styleSheet())
    chk('首帧外框就是外框色（不会停在初始化的白底）',
        chat.card.palette().color(QPalette.Window) == QColor(CHROME)
        and chat.grab().toImage().pixelColor(200, 20) == QColor(CHROME),
        (chat.card.palette().color(QPalette.Window).name(),
         chat.grab().toImage().pixelColor(200, 20).name()))
    chk('在线状态是浅灰胶囊 + 状态点，不再是绿底标签',
        isinstance(chat.state_lb, StatePill)
        and chat.state_lb.styleSheet() == ''
        and chat.state_lb.text().endswith('在线')
        and chat.state_lb.width() > 0,
        (type(chat.state_lb).__name__, chat.state_lb.styleSheet(),
         chat.state_lb.text()))
    chk('顶部是一条横跨窗口的标题带',
        chat.titlebar is not None and chat.titlebar.height() >= 36
        and chat.titlebar.width() >= chat.width() - 4)
    chk('三颗窗口按钮都在标题带上',
        all(w.parentWidget() is chat.titlebar
            for w in (chat.min_btn, chat.max_btn, chat.close_btn)))
    chk('左会话栏与右会话区直接相邻，中间没有间隙',
        chat.side.width() == LIST_W
        and chat.side.x() + chat.side.width() == chat.talk.x(),
        (chat.side.geometry(), chat.talk.geometry()))
    chk('搜索框旁边没有多余的「+」发起会话按钮',
        not hasattr(chat, 'add_btn') and not hasattr(chat, '_add_menu'))
    chk('右侧是一块白色会话平面',
        TALK_BG in chat.talk.styleSheet()
        and 'transparent' in chat.head_bar.styleSheet()
        and TALK_BG in chat.scroll.styleSheet()
        and TALK_BG in chat.inbox.styleSheet())
    chk('会话标题行只有底部一条细线',
        'border-bottom' in chat.head_bar.styleSheet()
        and 'border-radius' not in chat.head_bar.styleSheet())
    chk('消息区不是圆角卡片',
        'border-radius' not in chat.scroll.styleSheet()
        and 'border: none' in chat.scroll.styleSheet())
    chk('输入区只有顶部一条细线',
        'border-radius: 0' in chat.inbox.styleSheet()
        and 'border-top: 1px solid' in chat.inbox.styleSheet()
        and 'border: 1px' not in chat.inbox.styleSheet(),
        chat.inbox.styleSheet())
    chk('会话行铺满侧栏（左右各 8px 留白，圆角胶囊）',
        chat._rows[CONV_ALL].width() == chat.side.width() - 16,
        (chat._rows[CONV_ALL].width(), chat.side.width()))
    chk('会话栏底部有「我是谁」', not chat.me_av.pixmap().isNull())

    print('\n--- 微信式输入区 / 工具图标 ---')
    b = chat.send_btn
    fm = QFontMetrics(b.font())
    chk('「发送」文字横向放得下',
        fm.horizontalAdvance('发送') + 16 < b.width(),
        (fm.horizontalAdvance('发送'), b.width()))
    chk('「发送」文字纵向放得下', fm.height() + 8 < b.height(),
        (fm.height(), b.height()))
    chk('发送按钮显式清掉了继承来的 padding', 'padding: 0' in b.styleSheet())
    chk('输入框换成多行 QTextEdit', isinstance(chat.field, Composer))
    line = QFontMetrics(chat.field.font()).lineSpacing()
    chk('输入框有好几行高度', chat.field.height() >= line * 3,
        (chat.field.height(), line))
    chk('空输入框自绘 placeholder', bool(chat.field._hint))

    sent = []
    chat.on_send = lambda t: sent.append(t)
    chat.field.setText('你好')
    chat.field.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return,
                                       Qt.NoModifier))
    qapp.processEvents()
    chk('Enter 直接发送', sent == ['你好'], sent)
    chk('发送后清空输入框', chat.field.text() == '')
    chat.field.setText('第一行')
    chat.field.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return,
                                       Qt.ShiftModifier))
    qapp.processEvents()
    chk('Shift+Enter 换行不发送',
        '\n' in chat.field.text() and '第一行' in chat.field.text()
        and len(sent) == 1,
        (chat.field.text(), sent))
    chat.field.clear()
    chat.field.setText('第二段')
    chat.field.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return,
                                       Qt.ControlModifier))
    qapp.processEvents()
    chk('Ctrl+Enter 也是发送', sent == ['你好', '第二段'], sent)
    chat.field.clear()

    chk('图片和文件合并成一颗按钮',
        set(chat.icon_btn) == {'emoji', 'file'}, list(chat.icon_btn))
    chk('合并后的按钮提示里同时有图片和文件',
        '图片' in chat.icon_btn['file'].toolTip()
        and '文件' in chat.icon_btn['file'].toolTip())
    chk('工具图标是自绘矢量而不是字符',
        all(isinstance(v, ToolIcon) for v in chat.icon_btn.values()))
    chk('工具图标不小于 30px', chat.icon_btn['emoji'].width() >= 30)

    card = chat.composer_card
    emo = chat.icon_btn['emoji'].mapTo(card, QPoint(0, 0))
    fil = chat.icon_btn['file'].mapTo(card, QPoint(0, 0))
    snd = b.mapTo(card, QPoint(0, 0))
    fld = chat.field.mapTo(card, QPoint(0, 0))
    chk('输入区是一张浅灰圆角卡片',
        'border-radius: 14px' in card.styleSheet()
        and CARD_BG in card.styleSheet(), card.styleSheet())
    chk('表情和文件都在同一张输入卡片里',
        card is chat.icon_btn['file'].parentWidget() and card is b.parentWidget())
    chk('两颗图标在左下角',
        emo.y() > card.height() * 0.5 and fil.y() > card.height() * 0.5
        and emo.x() < card.width() * 0.3 and fil.x() < card.width() * 0.3,
        (emo, fil, card.size()))
    chk('发送在右下角',
        snd.y() > card.height() * 0.5 and snd.x() > card.width() * 0.6,
        (snd, card.size()))
    chk('多行文本框在图标上方', fld.y() < emo.y() and fld.y() >= 0)

    sent_media = []
    chat.on_media = lambda mt, p: sent_media.append((mt, p))

    class FakeDrop:
        def __init__(self, paths):
            self.paths = paths

        def mimeData(self):
            return self

        def hasUrls(self):
            return True

        def urls(self):
            return [QUrl.fromLocalFile(p) for p in self.paths]

        def acceptProposedAction(self):
            pass

    drop = FakeDrop(['a.png', 'b.txt'])
    chat.field.dragEnterEvent(drop)
    chat.field.dropEvent(drop)
    qapp.processEvents()
    chk('拖进来的文件先落进输入框（不立刻发）',
        sent_media == [] and len(chat._attach) == 2
        and [it['mt'] for it in chat._attach] == ['img', 'file'],
        (sent_media, chat._attach))
    chk('附件条显示出来了', chat.attach_host.isVisible()
        and len(chat._attach_chips) == 2)
    chk('输入框没把文件路径当文字插进去', chat.field.text() == '')
    chat.field.setText('一起看')
    chat.send()
    qapp.processEvents()
    chk('点发送才真的发出去，文字在前附件在后',
        sent == ['你好', '第二段', '一起看']
        and sent_media == [('img', 'a.png'), ('file', 'b.txt')],
        (sent, sent_media))
    chk('发完附件条清空', chat._attach == [] and not chat.attach_host.isVisible())

    sent_media[:] = []
    chat.stage_paths(['a.png', 'b.txt'])
    chat.send()
    qapp.processEvents()
    chk('只发附件也全部照发（没有冷却挡着）',
        sent_media == [('img', 'a.png'), ('file', 'b.txt')], sent_media)

    chat.stage_paths(['c.txt'])
    chk('附件可以单独移除',
        len(chat._attach) == 1 and sent_media[-1] != ('file', 'c.txt'))
    chat._unstage(chat._attach[0])
    qapp.processEvents()
    chk('移除后附件条收起', chat._attach == [] and not chat.attach_host.isVisible())

    chk('文件按钮直接开系统文件框（没有二级菜单）',
        hasattr(chat, '_pick_files') and not hasattr(chat, '_file_menu'))

    bar = chat.act_bar
    chk('快捷互动在输入卡片里，输入框上方',
        bar.parentWidget() is card
        and bar.mapTo(card, QPoint(0, 0)).y() < fld.y(),
        (bar.geometry(), fld))
    chk('快捷条没有底层背景色', 'background: transparent' in bar.styleSheet()
        and 'border' not in bar.styleSheet(), bar.styleSheet())
    chk('快捷条没有阴影 / 没有边框卡片', bar.graphicsEffect() is None)
    chip = chat._chips['wave']
    chk('快捷互动是紧凑的小药丸',
        chip.height() == CHIP_H and chip.width() < 60
        and chip.font().pointSize() <= 8,
        (chip.size(), chip.font().pointSize()))

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
    chk('窄窗口下所有互动都摆出来（不再藏进「更多」）',
        narrow == len(actions.CHAT_ORDER), (narrow, len(actions.CHAT_ORDER)))
    chk('没有「更多」按钮了', not hasattr(live, 'more_btn'))
    rows = [r.count() - 1 for r in live._act_rows]
    chk('窄窗口下分成两行', len([n for n in rows if n > 0]) == 2, rows)
    chk('两行时工具行变高', live.act_bar.height() == CHIP_H * 2 + 4,
        live.act_bar.height())

    live.resize(1500, 820)
    qapp.processEvents()
    live._reflow_actions()
    wide = len(visible_chips(live))
    chk('窗口拉宽后全部摆得下', wide == len(actions.CHAT_ORDER), wide)
    rows = [r.count() - 1 for r in live._act_rows]
    chk('宽窗口收成一行', len([n for n in rows if n > 0]) == 1, rows)
    chk('一行时工具行只有一颗药丸高', live.act_bar.height() == CHIP_H,
        live.act_bar.height())

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
    chk('快捷条覆盖除「传个话」以外的全部指令',
        set(live._chips) == set(actions.CHAT_ORDER), set(live._chips))
    chk('快捷条里没有「传个话」（人已经在窗口里了）',
        'say' not in live._chips)
    chk('快捷条接的是 App 的互动执行', live.on_action == app.do_action)
    chk('群聊下目标提示正确', '全体群聊' in live.target_lb.text())
    live.switch('p2')
    qapp.processEvents()
    chk('切到单聊后目标提示跟着变', '胡大神' in live.target_lb.text())
    chk('切会话后快捷条刷新目标', live._action_target() == 'p2')

    print('\n--- 没有冷却：点了就动 ---')
    calls2 = []
    nocd = ChatWindow(None, me='p1',
                      on_action=lambda k, t: calls2.append((k, t)))
    qapp.processEvents()
    chk('快捷键一律可点（没有禁用态）',
        all(b.isEnabled() for b in nocd._chips.values()))
    chk('chip 上没有倒计时角标', not hasattr(nocd._chips['wave'], '_cd'))
    chk('chip 不再有 set_cd 接口', not hasattr(nocd._chips['wave'], 'set_cd'))
    nocd._on_action('heart')
    chk('私聊/群聊目标透传正确', calls2[-1] == ('heart', None), calls2[-1:])
    nocd.switch('p3')
    nocd._on_action('pat')
    chk('切到单聊后目标变成对方', calls2[-1] == ('pat', 'p3'), calls2[-1:])
    chk('点互动不在提示行上写「已发送」', nocd.note.text() == '', nocd.note.text())
    nocd._on_action('sleep')
    nocd._on_action('jump')
    chk('连点两次都真的下发（不排队也不拦）',
        [k for k, _t in calls2[-2:]] == ['sleep', 'jump'], calls2[-2:])

    print('\n--- 左下角头像：换身份 / 退出 / 同步远程互动 ---')
    seen = []
    toggles = []

    def _on_remote(on):
        toggles.append(on)
        return on                   # 和 App 一样：回传切换后的状态

    me_win = ChatWindow(None, me='p1',
                        on_leave=lambda: seen.append('leave'),
                        on_quit=lambda: seen.append('quit'),
                        remote_on=True, on_remote=_on_remote,
                        remote_hint=lambda: '开关说明')
    qapp.processEvents()
    chk('「我是谁」那一条点得动', isinstance(me_win.me_bar, MeBar))
    chk('鼠标样式是手指（看得出能点）',
        me_win.me_bar.cursor().shape() == Qt.PointingHandCursor)
    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(6, 6),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    me_win.me_bar.mousePressEvent(press)
    chk('点一下弹出菜单，里面是换身份 + 退出',
        me_win._me_menu is not None and me_win._me_menu.isVisible(),
        me_win._me_menu)
    chk('菜单第一项写清当前身份',
        me_win._me_menu.actions()[0].text().startswith('猥琐'),
        me_win._me_menu.actions()[0].text())
    remote_item = [a for a in me_win._me_menu.actions()
                   if a.text() == '同步远程互动']
    chk('菜单里有「同步远程互动」，默认勾着',
        len(remote_item) == 1 and remote_item[0].isCheckable()
        and remote_item[0].isChecked(),
        [a.text() for a in me_win._me_menu.actions()])
    chk('这一项的说明来自 App（和托盘同一条文案）',
        remote_item[0].toolTip() == '开关说明', remote_item[0].toolTip())
    remote_item[0].trigger()
    chk('点一下就交给 App 去落盘', toggles == [False], toggles)
    chk('本地状态跟着 App 的返回值走', me_win.remote_on is False)
    me_win._me_menu.hide()
    me_win._show_me_menu()
    remote_item = [a for a in me_win._me_menu.actions()
                   if a.text() == '同步远程互动']
    chk('重开菜单时勾选跟着状态走',
        len(remote_item) == 1 and not remote_item[0].isChecked())
    me_win._me_menu.hide()
    me_win._on_leave()
    chk('换身份接到 App.do_leave', seen == ['leave'], seen)
    me_win._on_quit()
    chk('退出接到 App.quit', seen == ['leave', 'quit'], seen)

    print('\n--- 表情放大与悬停预览 ---')
    pop = chat._picker_pop()
    qapp.processEvents()
    cell = chat._pick_grid.itemAt(0).widget()
    # 第一格那张自定义贴图是可选的：有就多一格、而且是图片，没有就只排字符
    doge = media.doge_path(assets_dir())
    has_doge = os.path.exists(doge)
    chk('表情页 = 内置 emoji（有贴图时多一格）',
        chat._pick_grid.count() == len(media.EMOJI_CHARS) + (1 if has_doge else 0),
        chat._pick_grid.count())
    if has_doge:
        chk('第一格是贴图，不是字符',
            cell._pix is not None and cell._text == '', (cell._pix, cell._text))
    else:
        chk('没有贴图时第一格就是字符 emoji',
            cell._pix is None and cell._text == media.EMOJI_CHARS[0], cell._text)
    chk('表情格放大到 %dpx' % CELL, cell.width() >= 50 and cell.height() >= 50)
    chat._show_preview(cell)
    qapp.processEvents()
    pv = chat._preview.big.pixmap()
    chk('悬停弹出放大预览', chat._preview.isVisible())
    chk('预览里是一张真正放大的图',
        not pv.isNull() and pv.width() >= 100, pv.width())
    chk('预览按屏幕物理像素出图（不糊）',
        pv.width() >= PREVIEW_BOX * dpr - 1
        and pv.devicePixelRatio() >= dpr - 0.01,
        (pv.width(), pv.devicePixelRatio(), dpr))
    chk('预览逻辑尺寸不小于 %dpx' % PREVIEW_BOX,
        pv.width() / pv.devicePixelRatio() >= PREVIEW_BOX)
    chk('emoji 走系统彩色字体而不是中文 UI 字体',
        'Emoji' in EMOJI_FAMILY or EMOJI_FAMILY == 'Sans Serif', EMOJI_FAMILY)
    chk('预览是自绘位图而不是 QLabel 文字', chat._preview.big.text() == '')
    chk('预览下方有说明文字', '点一下发送' in chat._preview.cap.text())
    chk('拾取面板样式没套到子标签上',
        '#pick' in chat._pop.styleSheet() and chat._pop.objectName() == 'pick')
    chat._hide_preview()
    chk('离开后预览收起', not chat._preview.isVisible())

    chat._fill_page('others')
    qapp.processEvents()
    # 他人 = 除我以外的每个身份各一套八张，人数变了这里跟着变，不写死 32
    want = (len(ROLES) - 1) * len(media.STICKER_POSES)
    chk('「他人」页是 %d 张贴纸' % want, chat._pick_grid.count() == want,
        chat._pick_grid.count())
    card = chat._pick_grid.itemAt(0).widget()
    chk('贴纸格子存的是原图而不是 48px 缩略图',
        card._pix is not None and max(card._pix.width(), card._pix.height()) >= 200,
        (card._pix.width(), card._pix.height()))
    chat._show_preview(card)
    qapp.processEvents()
    chk('贴纸也能放大预览', not chat._preview.big.pixmap().isNull())
    chk('贴纸预览同样按屏幕物理像素出图',
        chat._preview.big.pixmap().width() >= PREVIEW_BOX * dpr - 1)
    chat._close_pop()
    chk('关掉弹层后预览一起收起', not chat._preview.isVisible())

    e = {'mt': 'emoji', 'char': '\U0001F600', 'text': '\U0001F600', 'who': 'p2'}
    lb = chat._content(e, False)
    chk('聊天记录里的 emoji 用大字号', lb.font().pointSize() >= 28,
        lb.font().pointSize())

    print('\n--- 长文字换行与选中配色 ---')
    url = 'https://example.com/' + 'a' * 160
    bub = BubbleText(url, False)
    bub.ensurePolished()
    chk('没有空格的长串也会换行（宽度封顶）',
        bub.width() <= BUBBLE_MAX
        and bub.height() > QFontMetrics(bub.font()).height() * 2,
        (bub.size(), BUBBLE_MAX))
    bp = bub.palette()
    chk('气泡选中背景是浅紫',
        bp.color(QPalette.ColorRole.Highlight).name() == SEL_BG,
        bp.color(QPalette.ColorRole.Highlight).name())
    chk('气泡选中文字保持深色',
        bp.color(QPalette.ColorRole.HighlightedText).name() == SEL_FG,
        bp.color(QPalette.ColorRole.HighlightedText).name())

    # 一句二十来字的中文，放宽后应该还是一行（旧版到 10 个字就折了）
    roomy = BubbleText('稳，AI在开发代码呢，你先忽略我发的话', True)
    one_line = QFontMetrics(roomy.font()).height() + 20
    chk('二十来字的中文一句话放得进一行', roomy.height() <= one_line,
        (roomy.size(), one_line))
    chk('气泡上限已经从 320 放宽', BUBBLE_MAX >= 400, BUBBLE_MAX)
    roomy.set_max_width(220)
    chk('窗口变窄时气泡跟着收（不横着戳出去）',
        roomy.width() <= 220 and roomy.height() > one_line,
        (roomy.size(), one_line))
    fp = chat.field.palette()
    chk('输入框选中文字也不会被染成白色',
        fp.color(QPalette.ColorRole.HighlightedText).name() == SEL_FG,
        fp.color(QPalette.ColorRole.HighlightedText).name())

    print('\n--- 图片预览：原图 + 置顶 ---')
    src = Image.new('RGB', (1200, 800), (30, 40, 60))
    b64img, _w, _h = media.encode_pil(src)
    full_im = media.full(b64img)
    chk('点开的图按原始尺寸解码（不是 240 的缩略图）',
        full_im is not None and full_im.width == 1200,
        full_im.size if full_im else None)
    chk('气泡里的缩略图仍然只缩到 240',
        media.thumb(b64img, limit=240).width == 240,
        media.thumb(b64img, limit=240).size)
    viewer = ImageViewer()
    chk('预览窗自带置顶（不会被聊天窗/人物压住）',
        bool(viewer.windowFlags() & Qt.WindowStaysOnTopHint))
    viewer._pix = QPixmap(400, 300)
    viewer._base, viewer._zoom = 1.0, 1.0
    viewer.resize(400, 300)
    wheel = QWheelEvent(QPointF(200, 150), QPointF(200, 150), QPoint(0, 0),
                        QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                        Qt.ScrollPhase.ScrollUpdate, False)
    viewer.wheelEvent(wheel)
    chk('滚轮能放大预览', viewer._zoom > 1.0, viewer._zoom)
    for _ in range(30):
        viewer.wheelEvent(wheel)
    chk('放大有上限（不会糊成马赛克）',
        viewer._zoom <= ImageViewer.MAX_ZOOM, viewer._zoom)
    viewer.hide()

    print('\n--- 表情面板翻页后的排版 ---')
    p2 = chat._picker_pop()
    qapp.processEvents()
    emoji_h = p2.height()
    emoji_box = chat._pick_box.size()
    chat._fill_page('mine')
    qapp.processEvents()
    mine_box = chat._pick_box.size()
    first = chat._pick_grid.itemAt(0).widget()
    chk('「我的」页格子区跟着缩到一行', mine_box.height() == CELL,
        (emoji_box, mine_box))
    chk('「我的」页第一格从左上角开始（不贴着底边）',
        first.geometry().topLeft() == QPoint(0, 0), first.geometry())
    chk('「我的」页弹层跟着收矮', p2.height() < emoji_h,
        (p2.height(), emoji_h))
    chat._fill_page('others')
    qapp.processEvents()
    rows = (want + COLS - 1) // COLS
    chk('「他人」页恢复 %d 行' % rows,
        chat._pick_box.height() == rows * CELL + (rows - 1) * CELL_GAP,
        chat._pick_box.size())
    chat._fill_page('emoji')
    qapp.processEvents()
    chk('翻回表情页尺寸复原',
        chat._pick_box.size() == emoji_box and p2.height() == emoji_h,
        (chat._pick_box.size(), p2.height(), emoji_box, emoji_h))
    chat._close_pop()

    chk('点图之前没有预览窗（用的时候才建）', chat._viewer is None)
    chk('预览浮层常驻引用', chat._preview is not None)

    print('\n--- 消息区贴底滚动 ---')
    tall_log = {CONV_ALL: [{'kind': 'msg', 'mt': 'text', 'who': 'me',
                            'text': '第 %d 条消息' % i,
                            'ts': time.time() + i} for i in range(30)]}
    tall = ChatWindow(None, me='p1', log=tall_log, is_online=lambda r: True)
    tall.resize(720, 480)
    tall.lift()
    tall.render()
    QTest.qWait(150)
    sb = tall.scroll.verticalScrollBar()
    chk('打开就停在最新一条', sb.value() == sb.maximum() and sb.maximum() > 0,
        (sb.value(), sb.maximum()))

    tall.field.setText('刚发出去的一条')
    tall.send()
    tall.log[CONV_ALL].append({'kind': 'msg', 'mt': 'text', 'who': 'me',
                               'text': '刚发出去的一条', 'ts': time.time()})
    tall.on_new(CONV_ALL)
    QTest.qWait(150)
    chk('发完自己那条仍然贴底（不会只露半截）',
        sb.value() == sb.maximum(), (sb.value(), sb.maximum()))
    tall.hide()

    print('\n--- 桌宠大小三档 ---')
    sizes = []
    szwin = ChatWindow(None, me='p1', on_pet_size=sizes.append,
                       pet_size='large')
    szwin._show_me_menu()
    sub = szwin._me_menu.actions()[2].menu()
    labels = [a.text() for a in sub.actions()]
    chk('头像菜单里能改桌宠大小', labels == ['小', '默认', '大'], labels)
    chk('当前档位带勾',
        [a.text() for a in sub.actions() if a.isChecked()] == ['大'])
    sub.actions()[0].trigger()
    chk('选「小」回调到 App', sizes == ['small'], sizes)
    szwin._me_menu.close()
    szwin.hide()

    chat.hide()
    nocd.hide()
    me_win.hide()
    pop.hide()
    live.hide()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for name in FAIL:
        print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
