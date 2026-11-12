# -*- coding: utf-8 -*-
"""服务端逻辑自测：身份唯一性裁决 + 僵尸释放 + 状态广播 + 事件转发。

直接在同一进程里起服务端，省去端口和子进程的麻烦。
跑法：python tools/test_server.py
"""

import collections
import json
import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server as S      # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


class C:
    """极简客户端：后台线程收消息进缓冲，主线程按类型取。

    ⚠️ 不能拿 socket.makefile() 的对象直接读并设超时 —— 一旦读超时，
    那个文件对象就永久作废，后面所有读都拿不到东西（第一版就是这么挂的）。
    收消息必须交给独立线程，主线程只从缓冲区里挑。
    """

    def __init__(self, port):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=10)
        # ⚠️ 连上后必须切回阻塞模式：create_connection 留下的 10 秒超时会一直在，
        # 空闲超过 10 秒 recv 就抛 TimeoutError，收线程静默死掉，
        # 之后所有消息都"收不到"（这个坑藏得极深）。
        self.s.settimeout(None)
        self.f = self.s.makefile('rb')
        self.buf = collections.deque()
        self.cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.f:
                line = line.strip()
                if line:
                    msg = json.loads(line.decode('utf-8'))
                    with self.cv:
                        self.buf.append(msg)
                        self.cv.notify_all()
        except Exception:
            pass

    def send(self, **kw):
        self.s.sendall((json.dumps(kw, ensure_ascii=False) + '\n').encode('utf-8'))

    def wait(self, t, timeout=3):
        """等某类消息。缓冲区里不匹配的消息**保留**，等的过程中不丢。"""
        end = time.time() + timeout
        with self.cv:
            while True:
                for i, m in enumerate(self.buf):
                    if m.get('t') == t:
                        del self.buf[i]
                        return m
                left = end - time.time()
                if left <= 0:
                    return None
                self.cv.wait(left)

    def drain(self, t):
        """把缓冲里所有某类消息清掉，返回剩下最新的那条。"""
        last = None
        with self.cv:
            keep = collections.deque()
            for m in self.buf:
                if m.get('t') == t:
                    last = m
                else:
                    keep.append(m)
            self.buf = keep
        return last

    def recv(self, timeout=3):
        end = time.time() + timeout
        with self.cv:
            while not self.buf:
                left = end - time.time()
                if left <= 0:
                    return None
                self.cv.wait(left)
            return self.buf.popleft()

    def close(self):
        # 先 shutdown 再 close：shutdown 立刻给对端一个 FIN，服务端马上就能释放身份。
        # （不要先关 makefile —— 收线程正阻塞在那个文件的 recv 上，关它会卡住。）
        try:
            self.s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.s.close()
        except OSError:
            pass


def claimed(players):
    return [r for r, v in (players or {}).items() if v]


def main():
    # ⚠️ 巡查间隔必须在启动巡查线程**之前**调小：
    # 循环一进来就把 REAP_INTERVAL 读进 wait()，这一觉睡下去再改要等下一轮。
    S.HEARTBEAT_TIMEOUT = 30.0      # 这个每次 reap 调用时才读，随时改都生效
    S.REAP_INTERVAL = 0.2
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    port = srv.server_address[1]
    serve = threading.Thread(target=srv.serve_forever, daemon=True)
    serve.start()

    # S.reaper_loop 是 while True 的常驻线程，测试跑完也不会退。它留在进程里，
    # 一旦在解释器 finalize 时正好写 stdout，就会撞 BufferedWriter 的锁直接
    # abort。测试自己起的线程自己收：包一层带 Event 的循环，末尾 set + join。
    stop_reaper = threading.Event()

    def reap():
        while not stop_reaper.wait(S.REAP_INTERVAL):
            try:
                S.REG.reap()
            except Exception as ex:      # 巡查线程绝不能死
                S.log('巡查出错：%r' % (ex,))

    reaper = threading.Thread(target=reap, daemon=True)
    reaper.start()

    # ---- 1. 一连上就告诉现状（没有房间码，也没有 query 也能拿到）----
    a = C(port)
    st = a.wait('state')
    check('连上即收到身份状态', st is not None, repr(st))
    check('所有身份初始都是空闲',
          st and all(v is None for v in st['players'].values()), repr(st))

    # ---- 2. 认领身份 ----
    a.send(t='join', role='p1')
    j = a.wait('joined')
    check('认领 p1 成功', j and j['role'] == 'p1', repr(j))
    st = a.wait('state')
    check('认领后广播里 p1 已被认领',
          st and claimed(st['players']) == ['p1'], repr(st))

    # ---- 2b. 同一条连接不能重复认领 ----
    a.send(t='join', role='p2')
    check('同一连接重复认领 → 报错', (a.wait('error') or {}).get('msg') is not None)

    # ---- 3. 核心：身份不能重复认领 ----
    b = C(port)
    b.wait('state')
    b.send(t='join', role='p1')
    rej = b.wait('reject')
    check('重复认领同一个身份 → 拒绝',
          rej and rej.get('reason') == 'role_taken' and rej.get('role') == 'p1',
          repr(rej))
    check('拒绝消息里带上最新占用情况（界面可直接刷新）',
          rej and rej.get('players', {}).get('p1') is not None, repr(rej))
    check('被拒后不应收到 joined', b.wait('joined', timeout=0.5) is None)

    # ---- 4. 换个空闲身份就能进 ----
    b.send(t='join', role='p2')
    check('换一个空闲身份可以认领', b.wait('joined') is not None)
    st = a.wait('state')
    check('老成员收到新成员的广播',
          st and sorted(claimed(st['players'])) == ['p1', 'p2'], repr(st))
    check('没被认领的身份仍显示空闲',
          st and st['players']['p3'] is None, repr(st))

    # ---- 5. 并发抢占：只能有一个人赢 ----
    wins = []
    conns = []
    lock = threading.Lock()

    def grab(i):
        try:
            cc = C(port)
            cc.send(t='join', role='p3')
            mm = cc.wait('joined', timeout=5)
            if mm is None:
                cc.wait('reject', timeout=1)
            with lock:
                wins.append(bool(mm))
                conns.append(cc)        # 先留着连接，关掉就等于主动释放身份
        except Exception as ex:
            with lock:
                wins.append(False)
            print('   并发线程异常：%r' % (ex,))

    ts = [threading.Thread(target=grab, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check('6 个并发抢同一身份，恰好 1 人成功',
          wins.count(True) == 1, '成功数=%d %r' % (wins.count(True), wins))

    # 抢占成功的那条连接已经关掉，等它被清理干净再往下测
    for cc in conns:
        cc.close()
    st = None
    for _ in range(30):
        probe = C(port)
        st = probe.wait('state')
        probe.close()
        if st and st['players']['p3'] is None:
            break
        time.sleep(0.1)
    check('抢到身份的人断开后，身份自动释放',
          st is not None and st['players']['p3'] is None, repr(st))

    # ---- 6. 事件转发（发给指定身份）----
    b.send(t='event', kind='wave', to='p1', text='你好呀')
    ev = a.wait('event')
    check('互动事件送达目标身份',
          ev and ev['from'] == 'p2' and ev['kind'] == 'wave'
          and ev['text'] == '你好呀', repr(ev))
    check('事件不应回传给自己', b.wait('event', timeout=0.5) is None)

    # ---- 7. 没认领身份不能发事件 ----
    z = C(port)
    z.wait('state')
    z.send(t='event', kind='wave')
    check('没认领身份发互动 → 报错', (z.wait('error') or {}).get('msg') is not None)

    # ---- 8. 主动释放身份，别人可认领 ----
    b.send(t='leave')
    check('释放身份返回确认', b.wait('left') is not None)
    e = C(port)
    e.wait('state')
    e.send(t='join', role='p2')
    check('原占用者释放后，身份可被他人认领', e.wait('joined') is not None)

    # ---- 9b. 图片 / 表情 / 文件：原样转发 ----
    # e 现在是 p2，a 还是 p1
    blob = 'iVBORw0KGgo='                     # 一小段合法 base64，内容不重要
    e.send(t='event', kind='say', to='p1', text='', mt='img', img=blob)
    ev = a.wait('event')
    check('图片消息能转发', ev and ev.get('mt') == 'img' and ev.get('img') == blob,
          repr(ev))
    check('图片消息带上了收件人', ev and ev.get('to') == 'p1', repr(ev))

    e.send(t='event', kind='say', to='p1', text='这是文字')
    ev = a.wait('event')
    check('不带 mt 的老消息当文字处理',
          ev and ev.get('mt') == 'text' and ev.get('text') == '这是文字', repr(ev))

    e.send(t='event', kind='say', to='p1', text='😄', mt='emoji')
    ev = a.wait('event')
    check('表情只靠一个字符就能转发',
          ev and ev.get('mt') == 'emoji' and ev.get('text') == '😄', repr(ev))

    e.send(t='event', kind='say', to='p1', text='', mt='file', img=blob,
           name='周报.xlsx', size=1234)
    ev = a.wait('event')
    check('文件名和大小一起转过去',
          ev and ev.get('mt') == 'file' and ev.get('name') == '周报.xlsx'
          and ev.get('size') == 1234, repr(ev))

    e.send(t='event', kind='say', to='p1', text='', mt='img',
           img='x' * (S.MAX_IMG + 10))
    check('超大附件被拒', '太大' in ((e.wait('error') or {}).get('msg') or ''))

    e.send(t='event', kind='say', to='p1', text='', mt='img', img='这不是base64!!')
    check('非法 base64 被拒',
          '格式' in ((e.wait('error') or {}).get('msg') or ''))

    e.send(t='event', kind='say', to='p1', text='', mt='img')
    check('只标了类型却不肯给内容 → 报错',
          (e.wait('error') or {}).get('msg') is not None)

    e.send(t='event', kind='say', to='p1', text='', mt='emoji')
    check('空表情被拒', (e.wait('error') or {}).get('msg') is not None)

    # ---- 9. 僵尸占用：心跳超时后自动释放 ----
    old_to = S.HEARTBEAT_TIMEOUT
    S.HEARTBEAT_TIMEOUT = 0.6
    try:
        f = C(port)
        f.wait('state')
        f.send(t='join', role='p3')
        check('僵尸测试：先认领 p3', f.wait('joined') is not None)

        time.sleep(1.6)          # 不发心跳，等巡查把他清掉
        g = C(port)
        st = g.wait('state')
        check('心跳超时后身份被自动释放', st and st['players']['p3'] is None,
              repr(st))

        g.send(t='join', role='p3')
        check('释放后的身份可以重新认领', g.wait('joined', timeout=3) is not None)
        g.close()
    finally:
        S.HEARTBEAT_TIMEOUT = old_to

    # ---- 10. 非法输入 ----
    h = C(port)
    h.wait('state')
    h.send(t='join', role='p9')
    check('不存在的身份被拒', (h.wait('reject') or {}).get('reason') == 'bad_role')

    i2 = C(port)
    i2.wait('state')
    i2.send(t='join', role=None)
    check('身份为空被拒', (i2.wait('reject') or {}).get('reason') == 'bad_role')

    for x in (a, b, e, f, g, h, i2, z):
        x.close()
    srv.shutdown()
    srv.server_close()
    serve.join(2)
    stop_reaper.set()
    reaper.join(2)
    # Handler 也是 daemon 线程，finally 里还会 log('连接断开')。到这里已经不需要
    # 服务端日志了，掐掉它，免得有落后的线程把 print 拖进解释器 finalize。
    S.log = lambda *_a, **_kw: None

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败项：')
        for n in FAIL:
            print('  - %s' % n)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
