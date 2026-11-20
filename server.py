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
import hashlib
import hmac
import json
import os
import re
import secrets
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
SERVER_VERSION = '1.9.5'

# ---------------- 认领口令 ----------------
#
# 口令认领：客户端先选身份，服务端只拿口令和**选中的那个人**比。
# 口令只存 PBKDF2 盐 + 哈希；认领成功后签发设备令牌，之后重连只带令牌。
# 一把全切上线时由 tools/claim_codes.py --init 一次生成六条。
#
# 文件放在 data/claim.json，和用户上传的素材一样属于运行期数据。
# 测试可以用 PETCLUB_CLAIM_FILE 指到临时文件。

ROOT = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = (os.environ.get('PETCLUB_CLAIM_FILE')
             or os.path.join(ROOT, 'data', 'claim.json'))
AUTH_ITER = 80000            # PBKDF2 迭代次数：登录一次几十毫秒，爆破不划算
AUTH_MIN_LEN = 6            # 用户自定义口令的最短长度
AUTH_TOKEN_KEEP = 8         # 每个身份最多保留几台设备的令牌
FAIL_WINDOW = 600.0         # 失败计数窗口（秒）
FAIL_LIMIT = 5              # 窗口内失败几次开始锁
FAIL_COOL = 60.0            # 锁多久

_auth_lock = threading.Lock()
_fail_lock = threading.Lock()
_fails = {}                 # ip -> [失败时刻, ...]


def _auth_load():
    try:
        with open(AUTH_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {'version': 1, 'roles': {}}
    if not isinstance(data, dict):
        return {'version': 1, 'roles': {}}
    if not isinstance(data.get('roles'), dict):
        data['roles'] = {}
    return data


def _auth_save(data):
    path = AUTH_FILE
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def _hash_code(code, salt):
    return hashlib.pbkdf2_hmac('sha256', code.encode('utf-8'),
                               bytes.fromhex(salt), AUTH_ITER).hex()


def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def auth_enabled():
    return bool(_auth_load().get('roles'))


def auth_protected(role):
    return role in (_auth_load().get('roles') or {})


def auth_locked_roles():
    """哪些身份设了口令：认领页据此决定选中后要不要显示口令框。"""
    roles = _auth_load().get('roles') or {}
    return [role for role in ROLES
            if isinstance(roles.get(role), dict) and roles[role].get('hash')]


def _code_matches(item, code):
    """这条口令和某个身份存着的盐 + 哈希对不对得上。"""
    if not isinstance(item, dict) or not code:
        return False
    salt = item.get('salt')
    want = item.get('hash')
    if not salt or not want:
        return False
    try:
        got = _hash_code(code, salt)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(got, want)


def auth_code_for_role(role, code):
    """这条口令是不是这个身份的。

    认领页先选人、再校验口令，走的就是这里：口令只和选中的那个身份比，
    不会因为别人碰巧用了同一条口令而被串到别的身份去。
    """
    roles = _auth_load().get('roles') or {}
    return _code_matches(roles.get(role), code)


def auth_role_for_code(code):
    """口令 → 角色（只给不支持「先选人」的老客户端兜底）。

    新客户端带 role，按身份校验；老客户端只发口令，只能全局反查。
    两条口令相同时这里按文件顺序取最后一个命中的 —— 所以新流程不要用它。
    """
    if not code:
        return None
    roles = _auth_load().get('roles') or {}
    hit = None
    for role, item in roles.items():
        if _code_matches(item, code):
            hit = role
    return hit


def auth_role_for_token(token):
    if not token:
        return None
    want = _hash_token(token)
    roles = _auth_load().get('roles') or {}
    hit = None
    for role, item in roles.items():
        if not isinstance(item, dict):
            continue
        for h in item.get('tokens') or []:
            if isinstance(h, str) and hmac.compare_digest(want, h):
                hit = role
    return hit


def auth_issue_token(role):
    """登录成功后发一张设备令牌。调用方负责只把明文发给客户端一次。"""
    token = secrets.token_hex(16)
    with _auth_lock:
        data = _auth_load()
        item = (data.get('roles') or {}).get(role)
        if not isinstance(item, dict):
            return ''
        tokens = item.get('tokens') or []
        tokens.append(_hash_token(token))
        item['tokens'] = tokens[-AUTH_TOKEN_KEEP:]
        _auth_save(data)
    return token


def auth_set_code(role, code):
    """设置/重设口令。旧令牌全部作废 —— 换口令等于换锁。

    允许两个身份用同一条口令：认领时先选人、再按身份校验，
    不存在「反查不到是谁」的问题。
    """
    salt = secrets.token_hex(16)
    with _auth_lock:
        data = _auth_load()
        data.setdefault('roles', {})[role] = {
            'salt': salt, 'hash': _hash_code(code, salt), 'tokens': []}
        _auth_save(data)
    return True


def auth_reset(role=None):
    """删掉某身份（或全部）的口令。删掉后该身份回到先占先得。"""
    with _auth_lock:
        data = _auth_load()
        roles = data.get('roles') or {}
        if role is None:
            roles.clear()
        else:
            roles.pop(role, None)
        data['roles'] = roles
        _auth_save(data)


def auth_init(codes):
    """一次性写入六个身份的口令（只存哈希）。"""
    roles = {}
    for role, code in codes.items():
        salt = secrets.token_hex(16)
        roles[role] = {'salt': salt, 'hash': _hash_code(code, salt),
                       'tokens': []}
    with _auth_lock:
        _auth_save({'version': 1, 'roles': roles})


def _loopback(ip):
    """本机来的连接（桌面端、网页网关）不走 IP 限速。

    网页网关会把所有浏览器连接代理成 127.0.0.1，按 IP 计数会变成
    「一个人输错五次，全体网页用户一起被锁」；本机是可信边界。
    """
    return ip in ('127.0.0.1', '::1', 'localhost')


def auth_locked(ip):
    if _loopback(ip):
        return False
    now = time.monotonic()
    with _fail_lock:
        times = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = times
        return len(times) >= FAIL_LIMIT and now - times[-1] < FAIL_COOL


def auth_fail(ip):
    if _loopback(ip):
        return
    now = time.monotonic()
    with _fail_lock:
        times = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        times.append(now)
        _fails[ip] = times


def auth_ok(ip):
    with _fail_lock:
        _fails.pop(ip, None)


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

    def member_of(self, role):
        """这个身份现在谁在线；没人在线返回 None。"""
        room = self.room
        with room.lock:
            mid = room.roles.get(role)
            return room.members.get(mid) if mid else None

    def kick(self, role, msg):
        """把某个身份在线的那一位强制下线，并释放身份。

        只在「口令/令牌校验通过、且客户端明确选择强制登录」时调用。
        先发 kicked 再断开：旧设备据此回到登录页，而不是傻傻地自动重连
        再把人踢回来（那样两边会来回抢）。
        """
        room = self.room
        with room.lock:
            mid = room.roles.get(role)
            m = room.members.pop(mid, None) if mid else None
            if m is not None:
                room.roles[role] = None
        if m is None:
            return None
        if m.client is not None:
            m.client.send({'t': 'kicked', 'msg': msg})
            if m.client.member is m:
                m.client.member = None
            m.client.close()
        log('强制下线「%s」' % ROLE_NAMES.get(role, role))
        self.broadcast()
        return m

    # ---- 广播 ----

    def state_msg(self):
        room = self.room
        with room.lock:
            players = room.snapshot_locked()
            online = len(room.members)
        return {'t': 'state', 'players': players, 'online': online,
                'total': len(ROLES), 'v': SERVER_VERSION,
                'auth': auth_enabled(),
                'locked': auth_locked_roles()}

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
            freed = REG.leave(self.client.member)
            REG.unregister(self.client)
            # 连接断掉（关客户端 / 断网 / 换身份后重连）也要广播一次。
            # 只有主动发 leave 才广播的话，别人屏幕上的他就一直站着 ——
            # 要等巡查线程十几秒后才清，表现是「换身份了但那只宠物不走」。
            if freed:
                REG.broadcast()
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
            code = msg.get('code')
            code = code.strip() if isinstance(code, str) else ''
            token = msg.get('token')
            token = token.strip() if isinstance(token, str) else ''
            force = msg.get('force') is True
            ip = self.client_address[0]

            # 认证：口令即身份；令牌是「这台设备已经登录过」的凭据。
            authed = False
            if token:
                role = auth_role_for_token(token)
                if role is None:
                    return c.send({
                        't': 'reject', 'reason': 'need_auth',
                        'msg': '登录已失效，请重新输入认领口令',
                        'auth': True,
                        'players': REG.state_msg()['players']})
                authed = True
            elif code and role:
                # 新客户端：认领页先选人，口令只和**选中的这个人**比。
                # 两个人碰巧用同一条口令也不会串身份，错口令也不会全局试探。
                if role not in ROLES:
                    return c.send({'t': 'reject', 'reason': 'bad_role',
                                   'msg': '身份不合法',
                                   'auth': auth_enabled()})
                if auth_protected(role):
                    if auth_locked(ip):
                        return c.send({
                            't': 'reject', 'reason': 'too_many',
                            'msg': '口令错太多次了，等一分钟再试',
                            'auth': True})
                    if not auth_code_for_role(role, code):
                        auth_fail(ip)
                        return c.send({
                            't': 'reject', 'reason': 'bad_code',
                            'role': role, 'msg': '口令不对', 'auth': True})
                    auth_ok(ip)
                    authed = True
            elif code:
                if auth_locked(ip):
                    return c.send({
                        't': 'reject', 'reason': 'too_many',
                        'msg': '口令错太多次了，等一分钟再试',
                        'auth': True})
                role = auth_role_for_code(code)
                if role is None:
                    auth_fail(ip)
                    return c.send({
                        't': 'reject', 'reason': 'bad_code',
                        'msg': '口令不对', 'auth': True})
                auth_ok(ip)
                authed = True
            elif role and not auth_protected(role):
                pass                    # 还没配口令的身份：保持先占先得
            else:
                if role not in ROLES:
                    return c.send({'t': 'reject', 'reason': 'bad_role',
                                   'msg': '身份不合法',
                                   'auth': auth_enabled()})
                return c.send({
                    't': 'reject', 'reason': 'need_auth',
                    'role': role,
                    'msg': '该身份已启用认领口令，请更新客户端后输入口令',
                    'auth': auth_enabled(),
                    'players': REG.state_msg()['players']})

            if role not in ROLES:
                return c.send({'t': 'reject', 'reason': 'bad_role',
                               'msg': '身份不合法', 'auth': auth_enabled()})

            # 身份在线：带正确凭据 + 明确强制，才允许顶号。
            old = REG.member_of(role)
            if old is not None:
                if authed and force:
                    REG.kick(role, '你的身份已在另一台设备登录，这台已下线')
                else:
                    # 没启用口令时沿用老的 reason，老客户端靠它回认领页。
                    return c.send({
                        't': 'reject',
                        'reason': ('role_online' if auth_enabled()
                                   else 'role_taken'),
                        'role': role,
                        'client': old.kind,
                        'msg': '「%s」正在%s在线' % (
                            ROLE_NAMES.get(role, role),
                            '网页版' if old.kind == 'web' else '桌面版'),
                        'auth': auth_enabled(),
                        'players': REG.state_msg()['players']})

            m, err = REG.join(c, role, client_kind)
            if err:
                return c.send({'t': 'reject', 'reason': err, 'role': role,
                               'msg': '身份不合法', 'auth': auth_enabled()})
            out = {'t': 'joined', 'role': role,
                   'name': ROLE_NAMES.get(role, role)}
            if code:
                # 口令登录成功 → 发一张设备令牌，之后重连不再输口令。
                tok = auth_issue_token(role)
                if tok:
                    out['token'] = tok
            c.send(out)
            REG.broadcast()

        elif t == 'setcode':
            m = c.member
            if not m:
                return c.send({'t': 'error', 'msg': '还没认领身份'})
            code = msg.get('code')
            code = code.strip() if isinstance(code, str) else ''
            if len(code) < AUTH_MIN_LEN:
                return c.send({'t': 'error',
                               'msg': '口令至少 %d 位' % AUTH_MIN_LEN})
            auth_set_code(m.role, code)
            tok = auth_issue_token(m.role)
            c.send({'t': 'code_ok', 'role': m.role, 'token': tok})
            log('「%s」修改了认领口令' % ROLE_NAMES.get(m.role, m.role))

        elif t == 'ping':
            if c.member:
                c.member.last_seen = time.monotonic()
            c.send({'t': 'pong'})

        elif t == 'sprites':
            # 「房间里的素材变了」（有人传图 / 换了图 / 删了动作）。
            # 服务端不下发清单本身：图在哪、rev 是多少，各端自己去网关取，
            # 这里只负责通知 —— 免得六个身份的动作表在每条连接上存六份。
            #
            # 不带附件、不进聊天记录，也不理会伪造：最坏的结果是别人多拉
            # 一次清单，几十字节，不值得为它加签名。
            m = c.member
            if not m:
                return
            room = REG.room
            with room.lock:
                targets = [x for x in room.members.values() if x.mid != m.mid]
            log('素材变更 %s → %d 人' % (m.role, len(targets)))
            for x in targets:
                x.client.send({'t': 'sprites', 'from': m.role})

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
            # 自定义动作的**名字**。动作 key（c1、c2…）只标识「哪一张图」，
            # 名字才是「大家在做什么」：我做「比耶」，对方有同名动作就做他
            # 自己那份（图各人一份，key 各不相同）。服务端不认识动作表，
            # 只把名字原样捎上，和 kind 一样属于「转发的字段」。
            an = clean(msg.get('an'), 24)
            # to 也要带上：客户端靠它分清「对大家说的」和「专门对我说的」，
            # 聊天记录才能分别落进群聊和那个人。群发时是 None。
            payload = {'t': 'event', 'kind': kind, 'from': m.role,
                       'text': text, 'to': to if to in ROLES else None,
                       'mt': mt, 'img': img, 'name': name, 'size': size,
                       'an': an or None, 're': re_out}
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
