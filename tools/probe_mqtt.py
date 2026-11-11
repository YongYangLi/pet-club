# -*- coding: utf-8 -*-
"""探测公共 MQTT broker 能不能连、能不能用。

MQTT 方案的第一步不是写代码，而是确认"到底连不连得上" ——
公共 broker 大多在境外，国内网络未必通。这个脚本用标准库手写
一个最小的 MQTT 3.1.1 CONNECT，发出去看有没有 CONNACK 回来。

跑法：python tools/probe_mqtt.py
"""

import socket
import struct
import sys
import time

BROKERS = [
    ('test.mosquitto.org', 1883),
    ('broker.emqx.io', 1883),
    ('broker.hivemq.com', 1883),
    ('mqtt.eclipseprojects.io', 1883),
]

# 对照组：确认沙箱本身有外网，免得把所有失败都怪到 broker 头上
BASELINE = ('www.baidu.com', 443)


def encode_len(n):
    """MQTT 的"剩余长度"变长编码。"""
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            return bytes(out)


def connect_packet(client_id):
    """MQTT 3.1.1 CONNECT。clean session + keepalive 30s。"""
    vh = b'\x00\x04MQTT' + bytes([0x04, 0x02]) + struct.pack('>H', 30)
    cid = client_id.encode('utf-8')
    body = vh + struct.pack('>H', len(cid)) + cid
    return bytes([0x10]) + encode_len(len(body)) + body


def probe(host, port, timeout=5.0):
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return False, '连不上：%s' % e, 0.0
    try:
        s.sendall(connect_packet('petclub-probe'))
        s.settimeout(timeout)
        data = s.recv(16)
        dt = time.time() - t0
        if len(data) >= 4 and data[0] == 0x20:
            rc = data[3]
            if rc == 0:
                return True, 'CONNACK 成功', dt
            return False, 'CONNACK 拒绝，返回码 %d' % rc, dt
        return False, '收到非预期数据 %r' % data[:16], dt
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e), time.time() - t0
    finally:
        try:
            s.close()
        except OSError:
            pass


def main():
    print('=== 对照组：沙箱本身有没有外网 ===')
    ok, msg, dt = probe(*BASELINE)
    if ok:
        print('[ok]   %s:%d 可达 —— %s' % (BASELINE[0], BASELINE[1], msg))
    else:
        # 443 上跑 HTTPS，我们发 MQTT 报文当然不会被正确回应；
        # 但"连上了"这件事本身就说明网络通。
        reachable = '连不上' not in msg
        print('[%s] %s:%d —— %s' % ('ok' if reachable else 'FAIL',
                                    BASELINE[0], BASELINE[1], msg))
        if not reachable:
            print('\n沙箱没有外网，后面的结论没有参考价值，先解决网络。')
            return 1

    print('\n=== 公共 MQTT broker ===')
    good = []
    for host, port in BROKERS:
        ok, msg, dt = probe(host, port)
        print('[%s] %-26s %s%s' % ('ok' if ok else 'FAIL', '%s:%d' % (host, port),
                                   msg, ('  %.0fms' % (dt * 1000)) if ok else ''))
        if ok:
            good.append((host, port))

    print()
    if good:
        print('可用：%s' % '、'.join('%s:%d' % b for b in good))
    else:
        print('没有一个公共 broker 连得上 —— 这条路在当前网络下走不通。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
