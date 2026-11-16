# -*- coding: utf-8 -*-
"""桌面端「加个动作」弹层的自测。

跑法：python tools/test_dlg.py

只测交互契约：默认选谁、payload 里带什么、什么情况下不让提交、两种模式下
窗口高度是不是一样。版式对不对靠截图看（.tmp/shot_dlg.py），这里不比像素。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication             # noqa: E402

from petclub import tokens                             # noqa: E402
from petclub import theme                              # noqa: E402
from petclub.upload_dlg import UploadDialog            # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  <- ' + str(detail) if detail and not ok else '')),
          flush=True)


FACTORY = os.path.join(ROOT, 'assets', 'roles', 'p1')


def build_targets(n=9):
    out = [{'key': '', 'label': '新建动作'}]
    out.append({'key': 'idle', 'label': '站立',
                'icon': os.path.join(FACTORY, 'idle.png'), 'badge': '已换'})
    for key, label in (('wave', '挥手'), ('drink', '喝一口'), ('heart', '比心'),
                       ('jump', '跳一下'), ('sleep', '睡觉'), ('knock', '敲桌子')):
        out.append({'key': key, 'label': label,
                    'icon': os.path.join(FACTORY, key + '.png'),
                    'badge': '已换' if key == 'jump' else ''})
    out.append({'key': 'c1', 'label': '跳舞', 'icon': '', 'badge': '新增'})
    return out[:n]


def new_dialog(**kw):
    return UploadDialog('p1', (200, 240), build_targets(),
                        on_restore=kw.get('on_restore', lambda _k: None),
                        taken=kw.get('taken', ()),
                        parent=None)


def main():
    app = QApplication.instance() or QApplication([])      # noqa: F841

    # ---- 默认状态 ----
    d = new_dialog()
    chk('默认选中「新建动作」', d._key() == '')
    chk('新建时显示名字/时长', d.stack.currentIndex() == 0)
    chk('新建时按钮是「上传」', d.ok.text() == '上传')
    chk('没选图时上传不可点', not d.ok.isEnabled())
    chk('九张格子都建出来了', len(d.tiles) == 9)

    # ---- 两种模式窗口一样高 ----
    h0 = d.height()
    d.select('wave')
    chk('切到换图后高度不变', d.height() == h0, '%s -> %s' % (h0, d.height()))
    chk('换图时按钮是「换这张」', d.ok.text() == '换这张')
    chk('换图时切到说明页', d.stack.currentIndex() == 1)

    # ---- 角标 ----
    chk('已换的格子带「已换」', d.tiles['jump'].badge == '已换')
    chk('新增的格子带「新增」', d.tiles['c1'].badge == '新增')
    chk('没动过的格子没有角标', d.tiles['wave'].badge == '')

    # ---- 还原按钮 ----
    d.select('jump')
    chk('已换动作显示恢复按钮', not d.restore_btn.isHidden())
    chk('恢复按钮文案', d.restore_btn.text() == '恢复默认图')
    d.select('wave')
    chk('没换过就不显示恢复按钮', d.restore_btn.isHidden())
    d.select('c1')
    chk('自定义动作的按钮是「删掉这个动作」',
        d.restore_btn.text() == '删掉这个动作' and not d.restore_btn.isHidden())

    # ---- 提交校验 ----
    d.select('wave')
    before = d.result()
    d.accept()
    chk('没选图不让提交', d.result() == before)
    chk('状态栏说清楚原因', '先选一张图' in d.status.text(), d.status.text())

    d._png = b'\x89PNG\r\n\x1a\n'          # 只要有字节就算选过图
    d.accept()
    chk('选了图就能提交', d.result() == 1)
    p = d.payload()
    chk('换图 payload 带 action', p and p.get('action') == 'wave', p)
    chk('换图 payload 不带名字', p and 'label' not in p)

    # ---- 新建：必须起名字 ----
    d2 = new_dialog()
    d2._png = b'\x89PNG\r\n\x1a\n'
    before = d2.result()
    d2.accept()
    chk('新建没名字不让提交', d2.result() == before)
    chk('状态栏提示起名字', '名字' in d2.status.text(), d2.status.text())
    d2.name.setText('  跳舞  ')
    d2.dur.setText('3.5')
    d2.accept()
    chk('填完能提交', d2.result() == 1)
    p = d2.payload()
    chk('新建 payload 带 label', p and p.get('label') == '跳舞', p)
    chk('新建 payload 带时长', p and abs(p.get('dur') - 3.5) < 1e-6, p)

    # ---- 名字不能和已有的重复 ----
    d3 = new_dialog(taken=['跳舞', '打招呼'])
    d3._png = b'\x89PNG\r\n\x1a\n'
    d3.name.setText(' 跳舞 ')
    before = d3.result()
    d3.accept()
    chk('重名不让提交', d3.result() == before, d3.result())
    chk('状态栏说明重名', '已经有一个' in d3.status.text(), d3.status.text())
    d3.name.setText('比耶')
    d3.accept()
    chk('换个新名字就能提交', d3.result() == 1)

    # ---- 还原回调 ----
    got = []
    d3 = new_dialog(on_restore=got.append)
    d3.select('jump')
    d3._restore()
    chk('点恢复会回调动作 key', got == ['jump'], got)
    chk('点恢复后弹层关闭', d3.result() == 0)

    # ---- token 同源 ----
    raw = json.load(open(os.path.join(ROOT, 'assets', 'theme.json'),
                         encoding='utf-8'))
    chk('theme.py 的 ACCENT 来自 theme.json',
        theme.ACCENT.lower() == raw['color']['accent'].lower(),
        '%s vs %s' % (theme.ACCENT, raw['color']['accent']))
    chk('CSS 变量名是 kebab-case',
        '--accent-edge:' in tokens.css_vars(), tokens.css_vars()[:60])
    chk('token 文件读得到', os.path.isfile(tokens.path_of()))

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败的是：%s' % '、'.join(FAIL))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
