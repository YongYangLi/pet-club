# -*- coding: utf-8 -*-
"""
pet-club 服务端 —— 一台服务器就是一个房间

没有房间码了：谁连上这台服务器，谁就在同一个房间里。服务端只干三件事：
  1. 身份认领裁决：p1~p6 各只能被一个人认领，先到先得
  2. 在线状态广播：谁认领了哪个身份，实时推给所有连上来的人
     （包括还没认领的 —— 认领界面要靠这个显示「已认领」）
  3. 互动事件转发

【为什么这个服务端是绕不过去的】
  A 和 B 同时点「认领 p1」时，只有第三方裁判能判定谁先到。
  让两个客户端互相问「你占了吗」必然出现双占 —— 网络有延迟、两边时钟不一致、
  消息可能同时在路上。所以占用判定必须在一个串行点上完成，这个点就是这里。

【僵尸占用】
  人掉线了但没点退出，身份会被永远占死。所以每个成员带一个 last_seen，
  巡查线程发现超过 HEARTBEAT_TIMEOUT 没心跳，就把他踢掉并**释放身份**。
  没有这段，用几天就会出现「所有人都离线却没人能认领那个身份」。

纯标准库，单文件。
  本机跑：      python server.py
  部署到公网：  python server.py --host 0.0.0.0 --port 8765
"""

import argparse
import json
import re
import socketserver
import sys
import threading
import time

# ---------------- 配置 ----------------

ROLES = ('p1', 'p2', 'p3', 'p4', 'p5', 'p6')
ROLE_NAMES = {'p1': '猥琐', 'p2': '胡大神', 'p3': '杨大师', 'p4': '婧婧',
              'p5': '雷大神', 'p6': '左大神'}

HEARTBEAT_TIMEOUT = 30.0    # 超过这么久没收到心跳 = 掉线，释放身份
REAP_INTERVAL = 5.0         # 巡查间隔
IDLE_TIMEOUT = 90.0         # 连上了却一直不发消息的连接，超时断开
MAX_TEXT = 200

# 图片/表情/文件是 base64 塞在一条 JSON 行里过来的。上限按「解码后约 2MB」算，
# 和客户端对齐：图片 300KB（media.MAX_BYTES，客户端先缩过），文件 2MB（media.MAX_FILE）。
# 两边不一致的结果是客户端认为能发、服务端悄悄丢掉，用户只看到「发出去了但对方没收到」。
MAX_IMG = 2_900_000
MAX_NAME = 80
# emoji 是唯一一种「不带附件」的媒体消息：它只传那个字符，对面用本机字体渲染。
MTYPES = ('text', 'img', 'sticker', 'emoji', 'file')
MT_WITH_IMG = ('img', 'sticker', 'file')
B64_RE = re.compile(r'^[A-Za-z0-9+/]+={0,2}$')

# 服务端自己的版本号，只做一件事：让人一眼看出**线上跑的到底是哪份代码**。
# 起因很实在 —— 客户端发了新版、服务端没重启，客户端那边表现是「图片发了对方收不到」，
# 而两边看起来都「是最新的」（客户端版本号是对的，服务端根本不会说自己是谁）。
# 现在握手消息里带一个 v，连上去看一眼就知道。
SERVER_VERSION = '1.7.0'


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def clean(s, n):
    """收敛用户输入：去多余空白、限长。非字符串一律当空。"""
    if not isinstance(s, str):
        return ''
    return ' '.join(s.split())[:n]


# ---------------- 状态 ----------------

class Client:
    """一条连接。还没认领身份的也在这里 —— 认领界面要收到房间状态。"""

    __slots__ = ('conn', 'lock', 'member')

    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.Lock()
        self.member = None

    def send(self, obj):
        """发一条 JSON 行。返回 False 表示连接已断。"""
        try:
            data = (json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8')
        except (TypeError, ValueError):
            return True
        try:
            with self.lock:
                self.conn.sendall(data)
            return True
        except OSError:
            return False

    def close(self):
        try:
            self.conn.shutdown(1)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass


class Member:
    __slots__ = ('mid', 'role', 'client', 'last_seen', 'kind')

    def __init__(self, mid, role, client, kind='desktop'):
        self.mid = mid
        self.role = role
        self.client = client
        self.last_seen = time.monotonic()
        # 客户端类型只用于聊天标题上的「网页版 / 桌面版」，不参与裁决。
        # 老客户端不发 client，按桌面版处理。
        self.kind = 'web' if kind == 'web' else 'desktop'


class Room:
    """整台服务器唯一的房间。"""

    def __init__(self):
        self.members = {}                       # mid -> Member
        self.roles = {r: None for r in ROLES}   # role -> mid
        self.lock = threading.Lock()

    def snapshot_locked(self):
        """房间当前状态。必须已持锁调用。

        players 里每个身份要么是 {'role','name'}（已被认领），要么是 None（空闲）。
        被认领 = 那个人在线（掉线的会被巡查线程清走并释放身份）。
        """
        players = {}
        for r in ROLES:
            mid = self.roles.get(r)
            m = self.members.get(mid) if mid else None
            players[r] = ({'role': r, 'name': ROLE_NAMES[r],
                          'client': m.kind} if m else None)
        return players


class Registry:
    """身份占用判定全部在这里串行完成。"""

    def __init__(self):
        self.room = Room()
        self.clients = set()
        self.lock = threading.Lock()
        self._seq = 0

    # ---- 连接登记 ----

    def register(self, client):
        with self.lock:
            self.clients.add(client)

    def unregister(self, client):
        with self.lock:
            self.clients.discard(client)

    def _new_mid(self):
        with self.lock:
            self._seq += 1
            return 'm%d' % self._seq

    # ---- 认领 / 释放 ----

    def join(self, client, role, client_kind='desktop'):
        """尝试认领身份。返回 (member, err)。err 为 None 表示成功。

        这里是唯一的「先占先得」判定点 —— 检查和写入必须在同一把锁内完成，
        否则两个并发请求会同时看到「空」然后都认领上。
        """
        if role not in ROLES:
            return None, 'bad_role'
        room = self.room
        m = Member(self._new_mid(), role, client, client_kind)
        with room.lock:
            if room.roles.get(role) is not None:
                return None, 'role_taken'       # ← 这个身份已经有人了
            room.roles[role] = m.mid
            room.members[m.mid] = m
        client.member = m
        log('认领「%s」' % ROLE_NAMES.get(role, role))
        return m, None

    def leave(self, member):
        if member is None:
            return False
        room = self.room
        with room.lock:
            room.members.pop(member.mid, None)
            if room.roles.get(member.role) == member.mid:
                room.roles[member.role] = None      # ← 释放身份，别人可以认领了
        if member.client is not None and member.client.member is member:
            member.client.member = None
        log('释放「%s」' % ROLE_NAMES.get(member.role, member.role))
        return True

    # ---- 广播 ----

    def state_msg(self):
        room = self.room
        with room.lock:
            players = room.snapshot_locked()
            online = len(room.members)
        return {'t': 'state', 'players': players, 'online': online,
                'total': len(ROLES), 'v': SERVER_VERSION}

    def broadcast(self):
        """把最新状态推给**所有**连接 —— 没认领的人也要看得到，认领界面靠它。"""
        msg = self.state_msg()
        with self.lock:
            targets = list(self.clients)
        for c in targets:
            c.send(msg)

    # ---- 巡查 ----

    def reap(self):
        """把心跳超时的成员踢掉并释放身份。"""
        now = time.monotonic()
        room = self.room
        dead = []
        with room.lock:
            for m in list(room.members.values()):
                if now - m.last_seen > HEARTBEAT_TIMEOUT:
                    dead.append(m)
            for m in dead:
                room.members.pop(m.mid, None)
                if room.roles.get(m.role) == m.mid:
                    room.roles[m.role] = None
        for m in dead:
            if m.client is not None:
                if m.client.member is m:
                    m.client.member = None
                m.client.close()
            log('「%s」心跳超时，踢出并释放' % ROLE_NAMES.get(m.role, m.role))
        if dead:
            self.broadcast()


REG = Registry()


def reaper_loop():
    # ⚠️ 调参注意：REAP_INTERVAL 是进循环时读一次就睡下去的值，
    # 运行中改它要等当前这一觉睡完才生效（测试里踩过：改了但第一轮仍是旧值）。
    # HEARTBEAT_TIMEOUT 是在 reap() 里每次比对时读的，随时改都立刻生效。
    while True:
        time.sleep(REAP_INTERVAL)
        try:
            REG.reap()
        except Exception as e:              # 巡查线程绝不能死
            log('巡查出错：%r' % (e,))


# ---------------- 连接处理 ----------------

class Handler(socketserver.BaseRequestHandler):

    def setup(self):
        self.client = Client(self.request)
        self.request.settimeout(IDLE_TIMEOUT)

    def handle(self):
        peer = self.client_address[0]
        REG.register(self.client)
        # 一连上就告诉他现在谁在，认领界面才有东西可显示
        self.client.send(REG.state_msg())
        try:
            rf = self.request.makefile('rb')
            for raw in rf:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                self.dispatch(msg)
        except (OSError, TimeoutError):
            pass
        finally:
            REG.leave(self.client.member)
            REG.unregister(self.client)
            log('连接断开 %s' % peer)

    def dispatch(self, msg):
        t = msg.get('t')
        c = self.client

        if t == 'query':
            c.send(REG.state_msg())

        elif t == 'join':
            if c.member:
                return c.send({'t': 'error', 'msg': '你已经有身份了',
                               'players': REG.state_msg()['players']})
            role = msg.get('role')
            # 老客户端不带 client，默认桌面版；新网页版带 'web'。
            client_kind = 'web' if msg.get('client') == 'web' else 'desktop'
            m, err = REG.join(c, role, client_kind)
            if err == 'role_taken':
                self.client.send({
                    't': 'reject', 'reason': 'role_taken', 'role': role,
                    'msg': '「%s」已经被认领了' % ROLE_NAMES.get(role, role),
                    'players': REG.state_msg()['players']})
                return
            if err:
                return c.send({'t': 'reject', 'reason': err, 'role': role,
                               'msg': '身份不合法'})
            c.send({'t': 'joined', 'role': role,
                    'name': ROLE_NAMES.get(role, role)})
            REG.broadcast()

        elif t == 'ping':
            if c.member:
                c.member.last_seen = time.monotonic()
            c.send({'t': 'pong'})

        elif t == 'event':
            m = c.member
            if not m:
                return c.send({'t': 'error', 'msg': '还没认领身份'})
            kind = clean(msg.get('kind'), 16)
            text = clean(msg.get('text'), MAX_TEXT)
            if not kind:
                return
            # 消息类型：text / img / sticker / file。老客户端不发 mt，当 text。
            mt = msg.get('mt')
            if mt not in MTYPES:
                mt = 'text'
            img = ''
            name = ''
            size = 0
            if mt in MT_WITH_IMG:
                img = msg.get('img')
                if not isinstance(img, str) or not img:
                    return c.send({'t': 'error', 'msg': '附件没传上来'})
                if len(img) > MAX_IMG:
                    return c.send({'t': 'error',
                                   'msg': '文件太大了（上限 2MB）'})
                if not B64_RE.match(img):
                    return c.send({'t': 'error', 'msg': '附件格式不对'})
                if mt == 'file':
                    name = clean(msg.get('name'), MAX_NAME) or '文件'
                    size = msg.get('size')
                    if not isinstance(size, int) or size < 0:
                        size = 0
            elif mt == 'emoji' and not text:
                return c.send({'t': 'error', 'msg': '表情是空的'})
            # 引用回复：只带「谁 + 一句摘要 + 类型」，不需要消息 id ——
            # 历史记录只在本机，对方的 id 和我这边的对不上，带摘要反而更稳。
            # 老客户端不发这个字段，转发出去就是 null，新客户端忽略即可。
            re_out = None
            re_in = msg.get('re')
            if isinstance(re_in, dict):
                r_who = clean(re_in.get('who'), 16)
                r_text = clean(re_in.get('text'), 120)
                r_mt = re_in.get('mt')
                if r_who in ROLES and r_text:
                    re_out = {'who': r_who, 'text': r_text,
                              'mt': r_mt if r_mt in MTYPES else 'text'}
            to = msg.get('to')
            # to 也要带上：客户端靠它分清「对大家说的」和「专门对我说的」，
            # 聊天记录才能分别落进群聊和那个人。群发时是 None。
            payload = {'t': 'event', 'kind': kind, 'from': m.role,
                       'text': text, 'to': to if to in ROLES else None,
                       'mt': mt, 'img': img, 'name': name, 'size': size,
                       're': re_out}
            room = REG.room
            with room.lock:
                if to in ROLES:
                    tgt_mid = room.roles.get(to)
                    tgt = room.members.get(tgt_mid) if tgt_mid else None
                    targets = [tgt] if tgt else []
                else:
                    targets = [x for x in room.members.values() if x.mid != m.mid]
            log('互动 %s/%s → %s 人' % (kind, mt, len(targets)))
            for x in targets:
                x.client.send(payload)

        elif t == 'leave':
            if REG.leave(c.member):
                REG.broadcast()
            c.send({'t': 'left'})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    # 默认 backlog 只有 5：几个人同时启动客户端时会排队甚至握手失败
    request_queue_size = 64


def main():
    ap = argparse.ArgumentParser(description='pet-club 服务端（一台服务器 = 一个房间）')
    ap.add_argument('--host', default='0.0.0.0', help='监听地址（默认所有网卡）')
    ap.add_argument('--port', type=int, default=8765, help='监听端口（默认 8765）')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    threading.Thread(target=reaper_loop, daemon=True).start()
    with Server((args.host, args.port), Handler) as srv:
        log('pet-club 服务端已启动：%s:%d' % (args.host, args.port))
        log('身份：%s' % '、'.join('%s=%s' % (k, v) for k, v in ROLE_NAMES.items()))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            log('收到中断，退出')


if __name__ == '__main__':
    main()
