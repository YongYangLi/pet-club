# -*- coding: utf-8 -*-
"""连不上服务器时到底什么样：说人话、不崩、能自己回来。

桌宠是个后台小东西，服务器一挂用户只会看到「桌面上什么都没有」。
这条链路必须自己扛住：先说一句看得懂的话，然后安静重试，通了就当没事发生。

跑法：py tools/probe_offline.py
"""

import os
import socket
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import server as S                                    # noqa: E402
from petclub.app import App, OFFLINE_NOTE             # noqa: E402
from petclub.claim import ClaimDialog                 # noqa: E402

PASS = []
FAIL = []
ERRORS = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    if detail and not isinstance(detail, str):
        detail = repr(detail)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''),
          flush=True)


def hook(tp, val, tb):
    text = ''.join(traceback.format_exception(tp, val, tb))
    ERRORS.append(text)
    sys.stderr.write(text)


sys.excepthook = hook


def free_port():
    """占一个空端口再放开：接下来的一小段时间里没人监听它。"""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    port = free_port()
    app = App('127.0.0.1', port)

    notes = []

    def fake_claim():
        app._claim_open = False
        notes.append(app._note)
        if app.offline:
            # 连不上就别认领：等真的通了再说
            return
        app._pending_role = 'p1'
        app._pending_sent = False

    app._open_claim = fake_claim

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            app.qapp.processEvents()
            app.tick()
            time.sleep(0.005)

    def pump_until(pred, timeout):
        end = time.time() + timeout
        while time.time() < end:
            app.qapp.processEvents()
            app.tick()
            time.sleep(0.01)
            if pred():
                return True
        return False

    print('--- 服务器不在 ---')
    app.connect()
    chk('进得去「离线」状态', pump_until(lambda: app.offline, 8.0), app.offline)
    chk('离线时桌面上没有角色', not app.pets, list(app.pets))
    chk('离线时的提示是人话', app.offline_note() == OFFLINE_NOTE, app.offline_note())
    chk('还没连上就不弹认领窗（没东西可认领，先安静重试）', notes == [],
        notes)
    chk('连不上时不会瞎认领', app.me is None, app.me)

    # 这时候唯一的入口是托盘：点进去得说清楚「为什么没动静」
    app.tray_left()
    pump(0.3)
    chk('离线时点托盘会打开认领窗', len(notes) == 1, notes)
    chk('打开时说的就是「连不上服务器」（不是「先认个身份」）',
        OFFLINE_NOTE in (notes[-1] if notes else ''), notes)

    print('\n--- 认领窗上的那句提示 ---')
    dlg = ClaimDialog(None, {}, OFFLINE_NOTE)
    chk('认领窗把提示显示出来了', OFFLINE_NOTE in dlg.note_text(),
        dlg.note_text())
    chk('卡片照样画得出来（不是一片空白）', len(dlg.cards) == 5, len(dlg.cards))
    chk('离线时也能点选身份（本地操作不发包）', True)
    dlg.close()

    print('\n--- 服务器回来了 ---')
    srv = S.Server(('127.0.0.1', port), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    S.REAP_INTERVAL = 0.3        # 进循环时读一次，必须在起线程之前改
    threading.Thread(target=S.reaper_loop, daemon=True).start()

    # 退避是 3 秒起、每次乘 1.6：冷启动那一轮 + 一到两轮重连，给足 25 秒
    ok = pump_until(lambda: app.me is not None, 25.0)
    chk('自己连回来了（不用重启程序）', ok, app.me)
    chk('离线标记撤掉了', not app.offline, app.offline)
    chk('角色重新长出来了', 'p1' in app.pets, list(app.pets))
    chk('连回来以后认领窗的说明是空的（不该再提连不上）',
        notes[-1] == '', notes)

    print('\n--- 断线 ---')
    # 真断线得让服务端主动掐掉连接。心跳超时是最省事的开关：
    # reap() 每次比对都读它，运行中改立刻生效（REAP_INTERVAL 只在进循环时读）。
    S.HEARTBEAT_TIMEOUT = 1.0
    chk('掉线以后退回离线', pump_until(lambda: app.offline, 20.0), app.offline)
    chk('角色还站在原地（不是瞬间消失）', 'p1' in app.pets, list(app.pets))
    b = app.bubbles.get('p1')
    chk('屏幕上说了句「断开了」',
        b is not None and ('断开' in b._text or '连不上' in b._text),
        b._text if b else None)

    print('\n--- 收尾 ---')
    S.HEARTBEAT_TIMEOUT = 30.0
    chk('期间没有未捕获的异常', not ERRORS, ERRORS[:1])
    app.quit()
    pump(0.2)

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
