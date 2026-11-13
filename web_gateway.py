# -*- coding: utf-8 -*-
"""pet-club 网页版网关：HTTP 静态站 + WebSocket ↔ 裸 TCP 桥接。

桌面端仍然直连 server.py 的 8765；浏览器连这里的 8766。
网关不解析业务消息，只做四件事：

  1. 把 web/ 和 assets/ 用 HTTP 发出去
  2. 把 /api/meta 里的角色、动作、台词给前端（台词只保留 actions.py 一份）
  3. 每条 WebSocket 对应一条到 127.0.0.1:8765 的 TCP，双向转发 JSON 行
  4. 替浏览器给上游发心跳，并检测浏览器假死

为什么不用 aiohttp / websockets：
  这个项目的服务端一直是「纯标准库、单文件、拷过去就能跑」。网页版也沿用
  这条规则，服务器上不需要额外 pip install。WebSocket 只实现文本帧、
  分片、ping/pong、close 这几件浏览器实际会用到的事。

启动：
    python web_gateway.py
    python web_gateway.py --host 0.0.0.0 --port 8766 --upstream 127.0.0.1:8765
"""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import unquote, urlparse

from petclub import actions
from petclub import __version__ as WEB_VERSION
import server as game_server

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, 'web')
ASSETS_DIR = os.path.join(ROOT, 'assets')

# 发布脚本写进 web/build.json 的版本标识。前端靠它判断自己是不是过期了：
# 页面 HTML 里有 __BUILD__（渲染时替换），服务端在 /api/version 里说当前的，
# 两个不一样就说明用户手里是旧页面。
BUILD_FILE = os.path.join(WEB_DIR, 'build.json')
_build_cache = {'key': None, 'value': None}
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_MSG = 8 * 1024 * 1024       # 单条消息上限，给以后的图片留余量
UPSTREAM_PING = 5.0             # 给服务端发应用层 ping，避免 30 秒心跳超时
WS_PING = 10.0                  # 给浏览器发协议 ping
WS_TIMEOUT = 30.0               # 浏览器多久不回 pong 就认为它死了

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
}


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def read_build():
    """读 web/build.json，按 mtime 缓存一次 —— 发布时直接覆盖文件，不重启进程。"""
    try:
        st = os.stat(BUILD_FILE)
    except OSError:
        return {'version': WEB_VERSION, 'build': 'dev'}
    key = (st.st_mtime_ns, st.st_size)
    if _build_cache['key'] == key:
        return _build_cache['value']
    try:
        with open(BUILD_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    value = {
        'version': str(data.get('version') or WEB_VERSION),
        'build': str(data.get('build') or 'dev'),
    }
    _build_cache['key'] = key
    _build_cache['value'] = value
    return value


def render_index():
    """把版本号和 build 号填进 index.html 的占位符。

    为什么不在前端用 JS 填：页面必须先知道自己这一份的 build 号，
    才能跟服务端比。等 JS 跑起来再问服务端，问到的永远是「最新」，
    旧页面会一直觉得自己是新的。
    """
    path = os.path.join(WEB_DIR, 'index.html')
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    b = read_build()
    html = html.replace('__VERSION__', b['version']).replace('__BUILD__', b['build'])
    return html.encode('utf-8')


def file_etag(path):
    """用 mtime+size 做 ETag。文件被覆盖上传后 mtime 一定变，够用。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return '"%x-%x"' % (st.st_mtime_ns, st.st_size)


def build_meta():
    """前端需要的静态数据。台词、动作只从 actions.py 取，不另写一份。"""
    b = read_build()
    return {
        'roles': list(game_server.ROLES),
        'role_names': dict(game_server.ROLE_NAMES),
        'actions': actions.ACTIONS,
        'order': list(actions.ORDER),
        'chat_order': list(actions.CHAT_ORDER),
        'tip': actions.TIP,
        'voice': actions.VOICE,
        'media_line': actions.MEDIA_LINE,
        'bubble_sec': 2.6,
        'ground_margin': 34,
        'gather_gap': 0.10,
        'gravity': 2400.0,
        'rise_speed': 2400.0,
        'crawl_body_per_sec': 1.5,
        'crawl_frame_sec': 0.16,
        'pet_size_factor': {'small': 0.78, 'default': 1.0, 'large': 1.3},
        'server_version': getattr(game_server, 'SERVER_VERSION', ''),
        'web_version': b['version'],
        'build': b['build'],
    }


def safe_path(base, rel):
    """把 URL 路径收进 base 目录，挡住 ../../。"""
    rel = unquote(rel or '').replace('\\', '/').lstrip('/')
    base_abs = os.path.abspath(base)
    p = os.path.abspath(os.path.normpath(os.path.join(base_abs, rel)))
    try:
        inside = os.path.commonpath([base_abs, p]) == base_abs
    except ValueError:
        inside = False
    return p if inside else None


class WsBridge:
    """一条浏览器 WebSocket ↔ 一条服务端 TCP。"""

    def __init__(self, conn, rfile, wfile, upstream):
        self.conn = conn
        self.rfile = rfile
        self.wfile = wfile
        self.upstream = upstream
        self.up = None
        self.up_file = None
        self.up_lock = threading.Lock()
        self.ws_lock = threading.Lock()
        self.stopped = threading.Event()
        self.last_pong = time.monotonic()

    # ---------------- WebSocket 帧 ----------------

    def _read_exact(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.rfile.read(n - len(buf))
            if not chunk:
                raise EOFError('ws closed')
            buf += chunk
        return buf

    def read_frame(self):
        head = self._read_exact(2)
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F

        if length == 126:
            length = struct.unpack('!H', self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self._read_exact(8))[0]
        if length > MAX_MSG:
            self.send_close(1009)
            raise EOFError('message too large')
        if not masked:
            # 浏览器必须带掩码；不带说明不是正常客户端
            self.send_close(1002)
            raise EOFError('unmasked frame')

        mask = self._read_exact(4)
        payload = bytearray(self._read_exact(length))
        for i in range(length):
            payload[i] ^= mask[i % 4]
        return fin, opcode, bytes(payload)

    def send_frame(self, opcode, payload):
        if self.stopped.is_set():
            return
        length = len(payload)
        head = bytearray([0x80 | opcode])
        if length < 126:
            head.append(length)
        elif length < 65536:
            head.append(126)
            head.extend(struct.pack('!H', length))
        else:
            head.append(127)
            head.extend(struct.pack('!Q', length))
        try:
            with self.ws_lock:
                self.wfile.write(bytes(head) + payload)
                self.wfile.flush()
        except (OSError, ValueError):
            self.stop()

    def send_text(self, text):
        self.send_frame(0x1, text.encode('utf-8'))

    def send_close(self, code=1000):
        try:
            self.send_frame(0x8, struct.pack('!H', code))
        except Exception:
            pass

    # ---------------- 上游 TCP ----------------

    def send_up(self, text):
        """浏览器消息 → 服务端。一行一条 JSON，和原来的协议一致。"""
        if self.up is None:
            return
        # JSON 里不该有裸换行；有的话会破坏服务端的按行读取
        text = text.replace('\r', '').replace('\n', '')
        try:
            with self.up_lock:
                self.up.sendall((text + '\n').encode('utf-8'))
        except OSError:
            self.stop()

    def up_reader(self):
        try:
            for raw in self.up_file:
                if self.stopped.is_set():
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    text = raw.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                self.send_text(text)
        except (OSError, ValueError):
            pass
        finally:
            if not self.stopped.is_set():
                self.send_close(1000)
            self.stop()

    def heartbeat(self):
        """给上游发业务心跳，给浏览器发 WebSocket ping。"""
        last_up = 0.0
        last_ws = 0.0
        while not self.stopped.wait(1.0):
            now = time.monotonic()
            if now - last_up >= UPSTREAM_PING:
                self.send_up(json.dumps({'t': 'ping'}, ensure_ascii=False))
                last_up = now
            if now - last_ws >= WS_PING:
                self.send_frame(0x9, b'petclub')
                last_ws = now
            if now - self.last_pong > WS_TIMEOUT:
                log('浏览器心跳超时，断开')
                self.stop()
                break

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        try:
            if self.up is not None:
                self.up.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            if self.up is not None:
                self.up.close()
        except OSError:
            pass
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    # ---------------- 主循环 ----------------

    def run(self):
        try:
            self.up = socket.create_connection(self.upstream, timeout=8)
            self.up.settimeout(None)
            self.up_file = self.up.makefile('rb')
        except OSError as e:
            log('连不上上游 %s:%d：%r' % (self.upstream[0], self.upstream[1], e))
            self.send_close(1011)
            return

        threading.Thread(target=self.up_reader, daemon=True).start()
        threading.Thread(target=self.heartbeat, daemon=True).start()

        frag_opcode = None
        frag = bytearray()
        try:
            while not self.stopped.is_set():
                fin, opcode, payload = self.read_frame()

                if opcode == 0x8:                 # close
                    self.send_close(1000)
                    break
                if opcode == 0x9:                 # ping
                    self.send_frame(0xA, payload)
                    continue
                if opcode == 0xA:                 # pong
                    self.last_pong = time.monotonic()
                    continue

                if opcode in (0x1, 0x2):
                    if not fin:
                        frag_opcode = opcode
                        frag = bytearray(payload)
                        continue
                    self._deliver(opcode, payload)
                    continue

                if opcode == 0x0:                 # continuation
                    frag.extend(payload)
                    if len(frag) > MAX_MSG:
                        self.send_close(1009)
                        break
                    if fin and frag_opcode is not None:
                        self._deliver(frag_opcode, bytes(frag))
                        frag_opcode = None
                        frag = bytearray()
                    continue
                # 其他未知帧忽略
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.send_close(1000)
            self.stop()
            try:
                if self.up_file is not None:
                    self.up_file.close()
            except Exception:
                pass

    def _deliver(self, opcode, payload):
        if opcode == 0x2:                     # 浏览器不会发二进制，忽略
            return
        try:
            text = payload.decode('utf-8')
        except UnicodeDecodeError:
            self.send_close(1007)
            self.stop()
            return
        self.send_up(text)


class WebHandler(BaseHTTPRequestHandler):
    server_version = 'pet-club-web/' + WEB_VERSION
    protocol_version = 'HTTP/1.1'

    def handle_one_request(self):
        """浏览器关页面时是直接断 TCP，不是礼貌的 keep-alive 结束。
        默认实现会把这个当成异常打一整段 traceback；这里收掉，只保持安静。"""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, OSError):
            self.close_connection = True

    # ---- 基础输出 ----

    def _send_bytes(self, data, ctype, cache='no-cache', status=200, etag=None):
        # 带着上次的 ETag 回来问，说明文件没动，回 304 让浏览器用本地副本。
        # 没有这一条时，no-cache 的语义是「每次都得重新下整份」——
        # 每次刷新白跑 164 KB，网络差的时候很肉。
        if etag and self.headers.get('If-None-Match') == etag:
            try:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', cache)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
            return
        try:
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', cache)
            if etag:
                self.send_header('ETag', etag)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _serve_file(self, path, cache='no-cache'):
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        ext = os.path.splitext(path)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(path)[0] or 'application/octet-stream'
        self._send_bytes(data, ctype, cache=cache, etag=file_etag(path))

    # ---- 路由 ----

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/ws':
                self._ws()
                return
            if path == '/api/meta':
                data = json.dumps(build_meta(), ensure_ascii=False).encode('utf-8')
                self._send_bytes(data, 'application/json; charset=utf-8', cache='no-store')
                return
            if path == '/api/version':
                # 前端轮询就看这一个，绝不能被任何一层缓存挡住。
                data = json.dumps(read_build(), ensure_ascii=False).encode('utf-8')
                self._send_bytes(data, 'application/json; charset=utf-8', cache='no-store')
                return
            if path in ('/', '/index.html'):
                # HTML 每次现渲染：里面带着这一份的 build 号，缓存住就失去意义了。
                try:
                    data = render_index()
                except OSError:
                    self.send_error(404)
                    return
                self._send_bytes(data, 'text/html; charset=utf-8', cache='no-store')
                return
            if path.startswith('/static/'):
                self._serve_file(safe_path(WEB_DIR, path[len('/static/'):]))
                return
            if path.startswith('/assets/'):
                self._serve_file(safe_path(ASSETS_DIR, path[len('/assets/'):]),
                                 cache='public, max-age=3600')
                return
            if path == '/favicon.ico':
                self._serve_file(os.path.join(ASSETS_DIR, 'app.ico'),
                                 cache='public, max-age=3600')
                return
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # ---- WebSocket 升级 ----

    def _ws(self):
        if self.headers.get('Upgrade', '').lower() != 'websocket':
            self.send_error(400, 'websocket only')
            return
        key = self.headers.get('Sec-WebSocket-Key')
        if not key:
            self.send_error(400, 'missing key')
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode('ascii')).digest()).decode('ascii')
        self.send_response(101)
        self.send_header('Upgrade', 'websocket')
        self.send_header('Connection', 'Upgrade')
        self.send_header('Sec-WebSocket-Accept', accept)
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            return
        self.close_connection = True
        upstream = getattr(self.server, 'upstream', ('127.0.0.1', 8765))
        WsBridge(self.connection, self.rfile, self.wfile, upstream).run()

    def log_message(self, fmt, *args):
        # 静态资源请求太多，不刷屏；真正的错误仍然打出来
        if args and str(args[0]).startswith(('4', '5')):
            log('%s %s' % (self.address_string(), fmt % args))


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """CentOS 8 自带 Python 3.6 没有 http.server.ThreadingHTTPServer（3.7+），
    这里自己拼一个，保证服务器上不需要额外升级 Python。"""
    daemon_threads = True


class WebServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, addr, upstream):
        self.upstream = upstream
        super().__init__(addr, WebHandler)


def parse_upstream(value):
    if ':' not in value:
        return value, 8765
    host, port = value.rsplit(':', 1)
    try:
        return host, int(port)
    except ValueError:
        return value, 8765


def main():
    ap = argparse.ArgumentParser(description='pet-club 网页版网关')
    ap.add_argument('--host', default='0.0.0.0', help='监听地址（默认所有网卡）')
    ap.add_argument('--port', type=int, default=8766, help='网页端口（默认 8766）')
    ap.add_argument('--upstream', default='127.0.0.1:8765',
                    help='服务端地址 host:port（默认 127.0.0.1:8765）')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    upstream = parse_upstream(args.upstream)
    with WebServer((args.host, args.port), upstream) as srv:
        log('pet-club 网页版已启动：http://%s:%d/' % (args.host, args.port))
        log('上游服务端：%s:%d' % upstream)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            log('收到中断，退出')


if __name__ == '__main__':
    main()
