# -*- coding: utf-8 -*-
"""协议探测：证明 pet-club 走的是裸 TCP，不是 HTTP/HTTPS。

为什么专门写这个：
  内网穿透工具（VS Code 端口转发、ngrok 的 http 模式等）暴露出来的都是
  https://xxx 地址，看起来"填进去就能用"，实际必然连不上。
  原因有两条，这个脚本把第一条实证出来：

   1. 我们不是 HTTPS 服务 —— 对面做 TLS 握手时，我们回的不是 ServerHello
   2. 就算拨开这层，隧道那头是 443 端口，我们的服务端默认在 8765

  顺带确认：客户端发的是明文 JSON 行，服务端也只认这个。

跑法：python tools/probe_protocol.py
"""

import os
import socket
import ssl
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server as S          # noqa: E402


def main():
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print('服务端已在 127.0.0.1:%d 上跑起来（就是 server.py 那个）\n' % port)

    # ---- 1. 用 TLS 客户端（等价于浏览器/HTTPS 隧道）去连它 ----
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection(('127.0.0.1', port), timeout=4)
    raw.settimeout(4)
    try:
        tls = ctx.wrap_socket(raw, server_hostname='tunnel.example.com')
        print('[意外] TLS 握手成功了：%s' % tls.version())
        tls.close()
    except Exception as e:
        print('[预期] TLS 握手失败：%s: %s' % (type(e).__name__, e))
        print('       → 服务端不会说 TLS，它不是 HTTPS 服务')
    finally:
        try:
            raw.close()
        except OSError:
            pass

    # ---- 2. 明文 JSON 行就能正常对话 ----
    c = socket.create_connection(('127.0.0.1', port), timeout=4)
    c.sendall(b'{"t":"query"}\n')
    data = c.recv(4096)
    print('\n[预期] 明文 JSON 请求得到响应：%s' % data.decode('utf-8', 'replace').strip())
    print('       → 它只认裸 TCP 上的明文 JSON 行')
    c.close()

    srv.shutdown()

    print('\n结论：pet-club 是自定义协议跑在裸 TCP 上，')
    print('      https:// 开头的隧道地址填进来一定连不上（会卡在 TLS 握手）。')


if __name__ == '__main__':
    main()
