# -*- coding: utf-8 -*-
"""连一下线上服务端，问它「你是谁、几个版本」。

只读：连上 → 读第一条 state → 断开。不认领身份、不发消息、不影响任何人的桌宠。

存在的理由很具体：客户端发新版而服务端忘了重启时，症状是「图片/表情包发出去，
对方什么都没收到」，而两边看起来都像最新的 —— 服务端以前根本不报自己的版本。
现在握手消息里带了 v，这个脚本就是那把尺子。

    py tools/check_server.py
    py tools/check_server.py --host 47.94.21.34 --port 8765
"""

import argparse
import json
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import config          # noqa: E402


def ask(host, port, timeout=6.0):
    """返回 (state dict, err)。err 非空时 state 为 None。"""
    try:
        s = socket.create_connection((host, port), timeout)
    except OSError as e:
        return None, '连不上 %s:%d（%s）' % (host, port, getattr(e, 'strerror', e))
    try:
        s.settimeout(timeout)
        f = s.makefile('rb')
        line = f.readline()
    except OSError as e:
        return None, '连上了但没收到握手（%s）' % (e,)
    finally:
        try:
            s.close()
        except OSError:
            pass
    if not line:
        return None, '连接被立刻关闭'
    try:
        return json.loads(line.decode('utf-8')), ''
    except (ValueError, UnicodeDecodeError):
        return None, '握手不是 JSON：%r' % line[:120]


def main(argv=None):
    ap = argparse.ArgumentParser(description='看线上服务端在跑哪个版本')
    ap.add_argument('--host', default=config.SERVER_HOST)
    ap.add_argument('--port', type=int, default=config.SERVER_PORT)
    args = ap.parse_args(argv)

    state, err = ask(args.host, args.port)
    if err:
        print(err)
        return 1

    players = state.get('players') or {}
    ver = state.get('v')
    online = [players[r]['name'] for r in sorted(players)
              if isinstance(players.get(r), dict) and players[r].get('name')]
    print('地址   %s:%d' % (args.host, args.port))
    print('在线   %s / %s' % (state.get('online'), state.get('total')))
    print('在场   %s' % ('、'.join(online) if online else '（没人）'))
    if ver:
        print('版本   %s' % ver)
        if ver != config.VERSION:
            print('       和客户端 %s 不一致 —— 不一定是错（协议向后兼容），'
                  '但如果新功能不对，先看这里。' % config.VERSION)
        return 0

    print('版本   （没报 —— 这是个**不认识媒体消息**的老服务端）')
    print('       症状：图片/表情包发出去，对方什么都收不到；表情收到会变成黑白字符。')
    print('       处理：把 server.py 传上去，重启服务。')
    return 2


if __name__ == '__main__':
    sys.exit(main())
