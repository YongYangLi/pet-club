# -*- coding: utf-8 -*-
"""连不上服务器时的行为探针。

地址写死之后，「填错地址」这条路径没有了，但**服务端没开 / 部署挂了**这条还在，
而且现在的处理必须完全不打扰人：桌面上除了角色本身不再有任何常驻控件
（角落那个圆点已经去掉了），所以离线状态只能靠「一句话」说清楚 ——
还没认领身份时说在认领窗那行提示里，已经在桌面上时说在角色头顶的气泡里。
程序自己按退避重试，不弹窗、不退出。

探针流程：
  指到一个没人听的端口 → 检查不崩、不长角色、不弹窗、那句话不说地址
  → 把这句话喂给认领窗（假窗口，只验 set_note 真被调到）
  → 在该端口起服务端 → 检查自动连上、认领成功、离线标记清掉
  全程不允许弹出任何模态窗（否则进程会卡住，这正是用户最怕的状态）。

跑法：python tools/probe_offline.py
"""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import server as S                  # noqa: E402
from petclub.app import App         # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def free_port():
    """占一个空闲端口再放掉 —— 用来制造「有人没人在听」的确定状态。"""
    import socket
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class StubDialog:
    """认领窗的替身：只记 set_note 收到的那句话。

    真窗口是模态的，探针里一弹就卡住 update 循环；而这条链路上唯一要验的
    事实是「掉线时那句话真的推给了正在开着的认领窗」。
    """

    def __init__(self):
        self.text = None

    def set_note(self, text):
        self.text = text


def main():
    port = free_port()
    print('目标端口 %d（现在没人听）' % port)

    errors = []
    app = App('127.0.0.1', port)
    app.root.report_callback_exception = lambda *a: errors.append(a)

    opened = []

    def fake_claim():
        # 认领窗是模态的，探针里绝不能真弹（会卡住 update 循环）
        opened.append(1)
        app._claim_open = False
        app._pending_role = 'p1'
        app._pending_sent = False

    app._open_claim = fake_claim

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.root.update()
            time.sleep(0.02)

    def wait_for(pred, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            app.root.update()
            time.sleep(0.02)
        return pred()

    app.set_server('127.0.0.1', port)
    app._schedule()
    # ⚠️ 不能只 pump 固定两秒：Windows 对「没人在听的端口」不是立刻回 RST，
    # 首次连接可能磨两秒多才报错。等状态，别等时间。
    wait_for(lambda: app.offline, timeout=8.0)

    # ---- 连不上时 ----
    check('连不上时不抛异常', not errors,
          repr(errors[0][1] if errors and len(errors[0]) > 1 else errors[:1]))
    check('标记为离线', app.offline is True)
    check('没有长出角色（没连上就不该有角色）', not app.pets, repr(list(app.pets)))
    check('离线期间没有弹任何窗口（地址改不了，弹窗只会卡住人）',
          not opened and app._claim_dlg is None, repr(opened))

    # ---- 桌面上唯一能说话的东西：那一句提示 ----
    note = app.offline_note()
    check('离线时有一句话可说（桌面上没有别的常驻入口了）',
          isinstance(note, str) and note.strip() and '\n' not in note, repr(note))
    check('这句话写清了连不上服务器', '连不上服务器' in note, repr(note))
    check('这句话说了正在重试', '重试' in note, repr(note))
    # 这一条是回归红线：地址属于实现细节，界面上一个字都不能出现
    check('这句话不露地址（没有 IP、没有端口、没有冒号）',
          (':' not in note) and ('127.0.0.1' not in note)
          and ('%d' % port not in note), repr(note))

    # 认领窗正开着的时候掉线：这句话得推到窗里那行提示上
    stub = StubDialog()
    app._claim_dlg = stub
    app._set_offline(True)          # 已经是离线也要能推一次（重复掉线是常态）
    app._claim_dlg = None
    check('掉线时那句话推给了开着的认领窗', stub.text == note, repr(stub.text))

    # 还没连上就弹认领窗：窗里那行提示得先说明状况，别让人对着一排灰卡片发呆
    seen = []
    real_open = app._open_claim
    app._open_claim = lambda: seen.append(app._note)
    app._note = ''
    app._claim_soon()
    pump(0.3)
    app._claim_open = False
    app._note = ''
    app._open_claim = real_open
    check('离线时开的认领窗自带这句话', seen == [note], repr(seen))

    # 重试循环里不能反复闪认领窗
    pump(1.5)
    check('重试期间不会反复弹认领窗', not opened, repr(opened))

    # ---- 服务端起来了 ----
    srv = S.Server(('127.0.0.1', port), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    ok = wait_for(lambda: app.me is not None, timeout=15.0)
    check('服务端一起来就自动重连上', ok and app.me == {'role': 'p1'}, repr(app.me))
    check('恢复后角色出现在桌面上', 'p1' in app.pets, repr(list(app.pets)))
    check('离线标记被清掉', app.offline is False)
    check('离线那句话不再挂在窗口上', app._note == '', repr(app._note))

    try:
        app.quit()
    except Exception as e:
        print('退出时异常：%r' % (e,))
    srv.shutdown()

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
