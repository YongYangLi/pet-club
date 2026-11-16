# -*- coding: utf-8 -*-
"""认领口令 / 设备令牌 / 强制登录自测。

直接调服务端的 dispatch，不启真实端口 —— join 的裁决逻辑全在
Registry + dispatch 里，用假客户端就能覆盖。

跑法：python tools/test_auth.py
"""
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix='petclub-auth-')
os.environ['PETCLUB_CLAIM_FILE'] = os.path.join(TMP, 'claim.json')
import server as S      # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + str(detail)) if (detail and not ok) else ''),
          flush=True)


class Conn:
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(json.loads(data.decode('utf-8')))

    def shutdown(self, *_a):
        pass

    def close(self):
        self.closed = True


def client(ip='10.0.0.9'):
    """一条假连接 + 能直接调 dispatch 的假 handler。"""
    c = S.Client(Conn())
    h = types.SimpleNamespace(client=c, client_address=(ip, 5000))
    return c, h


def join(h, **fields):
    S.Handler.dispatch(h, dict(t='join', **fields))


def last(c):
    return c.conn.sent[-1] if c.conn.sent else {}


def kinds(c):
    return [m.get('t') for m in c.conn.sent]


def main():
    codes = {'p1': 'AAAA-BBBB-CCCC', 'p2': 'DDDD-EEEE-FFFF'}
    S.auth_init(codes)

    print('--- 口令即身份 ---')
    check('启用了口令', S.auth_enabled())
    check('p1 口令对得上', S.auth_role_for_code('AAAA-BBBB-CCCC') == 'p1')
    check('p2 口令对得上', S.auth_role_for_code('DDDD-EEEE-FFFF') == 'p2')
    check('错口令不对应任何身份',
          S.auth_role_for_code('AAAA-BBBB-CCCD') is None)
    check('没配口令的身份仍是先占先得', not S.auth_protected('p3'))

    print('--- 口令登录 / 设备令牌 ---')
    c1, h1 = client()
    join(h1, code='AAAA-BBBB-CCCC', client='desktop')
    check('口令登录成功', last(c1).get('t') == 'joined'
          and last(c1).get('role') == 'p1', last(c1))
    token = last(c1).get('token') or ''
    check('登录后发了设备令牌', bool(token), last(c1))
    check('令牌能反查身份', S.auth_role_for_token(token) == 'p1')

    print('--- 身份在线：拒绝 / 强制登录 ---')
    c2, h2 = client()
    join(h2, code='AAAA-BBBB-CCCC', client='web')
    check('同一身份在别处在线会被拒',
          last(c2).get('reason') == 'role_online', last(c2))
    join(h2, code='AAAA-BBBB-CCCC', client='web', force=True)
    check('勾了强制登录就能顶号', last(c2).get('t') == 'joined', last(c2))
    check('旧设备收到 kicked', 'kicked' in kinds(c1), kinds(c1))
    check('旧设备连接被断开', c1.conn.closed)

    print('--- 令牌重连 / 再顶回来 ---')
    c3, h3 = client()
    join(h3, token=token, client='desktop')
    check('令牌认得出自己', last(c3).get('role') == 'p1', last(c3))
    check('但身份被新设备占着时仍要提示',
          last(c3).get('reason') == 'role_online', last(c3))
    join(h3, token=token, client='desktop', force=True)
    check('带令牌也能强制登录', last(c3).get('t') == 'joined', last(c3))

    print('--- 自定义口令 ---')
    S.Handler.dispatch(h3, {'t': 'setcode', 'code': 'my-new-code'})
    check('改口令成功', last(c3).get('t') == 'code_ok', last(c3))
    new_token = last(c3).get('token') or ''
    check('改完发了新令牌', bool(new_token))
    check('新口令能登录', S.auth_role_for_code('my-new-code') == 'p1')
    check('旧口令失效', S.auth_role_for_code('AAAA-BBBB-CCCC') is None)
    check('旧令牌失效', S.auth_role_for_token(token) is None)
    check('新令牌有效', S.auth_role_for_token(new_token) == 'p1')
    old = S.auth_role_for_token(token)
    c4, h4 = client()
    join(h4, token=token, client='web')
    check('拿旧令牌登不进来', last(c4).get('reason') == 'need_auth', last(c4))

    print('--- 错口令 ---')
    c5, h5 = client()
    join(h5, code='nope-nope-nope', client='web')
    check('错口令被拒且不泄露身份', last(c5).get('reason') == 'bad_code'
          and 'p1' not in str(last(c5)), last(c5))

    print('--- 重置回先占先得 ---')
    S.auth_reset('p1')
    check('p1 不再受口令保护', not S.auth_protected('p1'))
    # 把在线的人放掉，再用老方式认领
    for m in list(S.REG.room.members.values()):
        S.REG.leave(m)
    c6, h6 = client()
    join(h6, role='p1', client='web')
    check('没口令后可以按角色先占先得',
          last(c6).get('t') == 'joined', last(c6))

    print('--- 未知原因统计 ---')
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
