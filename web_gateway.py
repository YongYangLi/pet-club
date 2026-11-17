# -*- coding: utf-8 -*-
"""pet-club 网页版网关：HTTP 静态站 + WebSocket ↔ 裸 TCP 桥接。

桌面端仍然直连 server.py 的 8765；浏览器连这里的 80（可用 --port 改）。
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
    python web_gateway.py --host 0.0.0.0 --port 80 --upstream 127.0.0.1:8765
"""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import socket
import ssl
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import unquote, urlparse

from petclub import actions
from petclub import config as server_config
from petclub import custom
from petclub import tokens
from petclub import __version__ as WEB_VERSION
import server as game_server

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, 'web')
ASSETS_DIR = os.path.join(ROOT, 'assets')
MAX_POST = 10 * 1024 * 1024     # 上传请求体上限：多帧动作最多 6MB 原图，base64 后约 8MB


def acme_path(root, path):
    """HTTP-01 验证文件：<root>/.well-known/acme-challenge/<文件名>。

    acme.sh 的 --webroot 传的是「站点根目录」，它会把验证文件写进
    .well-known/acme-challenge/ 子目录，所以这里要按同样的结构去找。
    文件名里不允许再出现斜杠，避免 ../ 之类的路径穿越。
    """
    if not root:
        return None
    name = unquote(path.rsplit('/', 1)[-1])
    if not name or name.startswith('.') or '/' in name or '\\' in name:
        return None
    full = os.path.join(root, '.well-known', 'acme-challenge', name)
    return full if os.path.isfile(full) else None

# 可以直接换图、但不对应任何动作的姿势。
#
# 动作表只管「能发起的指令」，站立（idle）不是指令 —— 它是角色的默认姿态。
# 但用户想换掉的是「我站着的样子」，这跟换打招呼的图没有区别，所以让它
# 也能走同一个上传 / 还原接口：`action` 字段收到的是姿势名本身。
LOOSE_POSES = ('idle',)

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


_theme_cache = {'key': None, 'value': b''}


def theme_css():
    """把 assets/theme.json 的颜色拼成一段 :root，追加在 style.css 末尾。

    为什么是追加而不是去改文件里的 :root：那段是给人看的默认值，追加在后面、
    同优先级，后者胜。结构不用动，单独打开 style.css 也还是对的。
    """
    path = tokens.path_of()
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return b''
    if _theme_cache['key'] == key:
        return _theme_cache['value']
    try:
        body = ':root{%s}\n' % tokens.css_vars(tokens.load())
        value = ('\n/* 下面这段由 web_gateway 从 assets/theme.json 生成，'
                 '改颜色请改那份 json */\n' + body).encode('utf-8')
    except (OSError, ValueError, TypeError):
        value = b''
    _theme_cache['key'] = key
    _theme_cache['value'] = value
    return value


def build_meta():
    """前端需要的静态数据。台词、动作只从 actions.py 取，不另写一份。

    自定义动作在这里并进 `actions`：网页端的互动条和右键菜单都是遍历
    `order` 渲染的（chat.js / menu.js），所以合并完前端自动多出几项，
    不用改渲染逻辑。自定义动作排在内置之后，不打乱原有顺序。
    """
    b = read_build()
    extra = custom.actions()
    merged = dict(actions.ACTIONS)
    merged.update(extra)
    order = list(actions.ORDER) + [k for k in custom.order() if k in extra]
    return {
        'roles': list(game_server.ROLES),
        'role_names': dict(game_server.ROLE_NAMES),
        'actions': merged,
        'order': order,
        'chat_order': [k for k in order if k != 'say'],
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
        # 桌面客户端下载地址：跟自动更新同一个桶，不再跳 GitHub。
        'download_url': getattr(server_config, 'RELEASE_DOWNLOAD_URL', ''),
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
        etag = file_etag(path)
        if ext == '.css':
            # 颜色 token 从 theme.json 来，style.css 本身没变、json 变了的时候
            # ETag 也得跟着变，否则浏览器拿 304 继续用旧配色。
            inject = theme_css()
            if inject:
                data = data + inject
                stamp = file_etag(tokens.path_of())
                if etag and stamp:
                    etag = etag[:-1] + '-' + stamp[1:]
        self._send_bytes(data, ctype, cache=cache, etag=etag)

    def _serve_acme(self, path):
        """Let's Encrypt 续期时来取验证文件；HTTPS / HTTP 两个口都走这里。"""
        full = acme_path(getattr(self.server, 'acme_webroot', ''), path)
        if not full:
            self.send_error(404)
            return
        self._serve_file(full, cache='no-store')

    # ---- 路由 ----

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path.startswith('/.well-known/acme-challenge/'):
                self._serve_acme(path)
                return
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
            if path == '/api/sprites':
                # 素材清单：有哪些自定义动作、每个角色有哪些覆盖图、各自的 rev。
                # 客户端（网页 / 桌面）靠它决定去哪张图，也靠 rev 判断要不要重下。
                data = json.dumps(custom.manifest(), ensure_ascii=False).encode('utf-8')
                self._send_bytes(data, 'application/json; charset=utf-8', cache='no-store')
                return
            if path.startswith('/sprites/'):
                # 用户上传的图。URL 带 ?v=<rev>，换图就换 URL，缓存自然失效。
                self._serve_file(safe_path(custom.data_dir(), path[len('/sprites/'):]),
                                 cache='public, max-age=31536000, immutable')
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

    # ---- 上传 ----

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self._send_bytes(data, 'application/json; charset=utf-8',
                         cache='no-store', status=status)

    def _read_json(self, limit):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            return None, 'Content-Length 不合法'
        if length <= 0:
            return None, '请求体是空的'
        if length > limit:
            return None, '请求体超过 %d KB' % (limit // 1024)
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None, '不是合法 JSON'
        if not isinstance(obj, dict):
            return None, 'JSON 顶层必须是对象'
        return obj, ''

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/sprites':
                self._upload()
                return
            if path == '/api/sprites/restore':
                self._restore()
                return
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:
            # 不留这一层的话，任何意外都会变成「连接被重置」——
            # 浏览器只显示"网络错误"，日志里一个字都没有，没法查。
            log('上传出错：%r' % e)
            try:
                self._json({'ok': False, 'error': '服务端出错：%s' % e}, 500)
            except OSError:
                self.close_connection = True

    def _upload(self):
        """收一张姿势图。

        两种模式，靠 `action` 字段区分：
          · 给了 action（内置 key 或已有自定义 key）→ 只换图，动作表不动
          · 没给                                      → 新建动作，key 由服务端发号

        `action` 也可以是一个**姿势名**（LOOSE_POSES 里的那些，比如站立
        `idle`）—— 它们不对应任何动作，但同样是角色的一张图，用户想换就换。
        """
        body, err = self._read_json(MAX_POST)
        if err:
            return self._json({'ok': False, 'error': err}, 400)
        role = str(body.get('role') or '')
        if role not in game_server.ROLES:
            return self._json({'ok': False, 'error': '身份不合法'}, 400)

        raw_frames = body.get('frames')
        frames = []
        if isinstance(raw_frames, list) and raw_frames:
            if len(raw_frames) > custom.MAX_FRAMES:
                return self._json(
                    {'ok': False, 'error': '最多 %d 帧' % custom.MAX_FRAMES}, 400)
            for i, item in enumerate(raw_frames):
                try:
                    frames.append(base64.b64decode(
                        ''.join(str(item or '').split()), validate=True))
                except (ValueError, TypeError):
                    return self._json(
                        {'ok': False, 'error': '第 %d 帧编码坏了，重试一次' % (i + 1)},
                        400)
        else:
            try:
                frames = [base64.b64decode(
                    ''.join(str(body.get('data') or '').split()),
                    validate=True)]
            except (ValueError, TypeError):
                return self._json(
                    {'ok': False, 'error': '图片编码坏了，重试一次'}, 400)

        key = str(body.get('action') or '')
        move = body.get('move')
        if key:
            spec = actions.ACTIONS.get(key) or custom.actions().get(key)
            pose = (spec or {}).get('pose')
            if not pose and key in LOOSE_POSES:
                pose = key
            if not pose:
                return self._json({'ok': False, 'error': '没有这个动作'}, 400)
            # 只有显式带了 move / dur 字段才改对应配置；不带就是保持原样。
            out, err = custom.replace_pose(role, pose, frames, body.get('fps'),
                                           move, 'move' in body,
                                           body.get('dur'), 'dur' in body)
        else:
            out, err = custom.add_action(role, frames, body.get('label'),
                                         body.get('dur'), body.get('fps'), move)
        if err:
            return self._json({'ok': False, 'error': err}, 400)
        log('素材更新 %s/%s（%d 帧，%d 字节）'
            % (role, out.get('pose') or key, len(frames),
               sum(len(f) for f in frames)))
        self._json({'ok': True, 'spec': out, 'manifest': custom.manifest()})

    def _restore(self):
        """撤销上传：自定义动作整个删掉，内置动作只删覆盖图（回到出厂）。

        没有这一步，一张传错的图就再也收不回去了 —— 那比传不上去更难受。
        """
        body, err = self._read_json(64 * 1024)
        if err:
            return self._json({'ok': False, 'error': err}, 400)
        role = str(body.get('role') or '')
        if role not in game_server.ROLES:
            return self._json({'ok': False, 'error': '身份不合法'}, 400)
        key = str(body.get('action') or '')
        if key in custom.actions():
            custom.remove_action(key)
            log('删除自定义动作 %s' % key)
        elif key in LOOSE_POSES:
            custom.restore_pose(role, key)
            log('恢复出厂图 %s/%s' % (role, key))
        else:
            spec = actions.ACTIONS.get(key)
            pose = (spec or {}).get('pose')
            if not pose:
                return self._json({'ok': False, 'error': '没有这个动作'}, 400)
            custom.restore_pose(role, pose)
            log('恢复出厂图 %s/%s' % (role, pose))
        self._json({'ok': True, 'manifest': custom.manifest()})

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
    # 爬虫/扫描器会同时开很多连接；线程无限长会吃光内存。
    max_workers = 200

    def process_request(self, request, client_address):
        if threading.active_count() > self.max_workers:
            try:
                request.close()
            except OSError:
                pass
            return
        super().process_request(request, client_address)


# TLS 握手超时：扫描器常常连上不说话，不设上限会一直占着线程。
TLS_HANDSHAKE_TIMEOUT = 8.0


class WebServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128
    tls_ctx = None

    def __init__(self, addr, upstream, acme_webroot='', tls_ctx=None):
        self.upstream = upstream
        self.acme_webroot = acme_webroot
        self.tls_ctx = tls_ctx
        super().__init__(addr, WebHandler)

    def process_request_thread(self, request, client_address):
        """TLS 握手挪到工作线程里做。

        以前是把监听 socket 直接 wrap 成 SSL：accept() 时同步握手，一个
        连上就不说话的扫描器就能把整个 accept 循环卡死，accept 队列满
        （ss 里 Recv-Q 顶到 Send-Q），整站 443 打不开，而 8766 纯 HTTP
        毫发无损 —— 线上就是这么挂的。
        """
        if self.tls_ctx is not None:
            try:
                request.settimeout(TLS_HANDSHAKE_TIMEOUT)
                request = self.tls_ctx.wrap_socket(request, server_side=True)
                request.settimeout(None)
            except (ssl.SSLError, OSError):
                try:
                    request.close()
                except OSError:
                    pass
                return
        super().process_request_thread(request, client_address)


class RedirectHandler(BaseHTTPRequestHandler):
    """HTTP 端口：ACME 验证照常回文件，其余全部 308 到 HTTPS。"""
    server_version = 'pet-club-web/' + WEB_VERSION
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith('/.well-known/acme-challenge/'):
            full = acme_path(getattr(self.server, 'acme_webroot', ''), path)
            if not full:
                self.send_error(404)
                return
            try:
                with open(full, 'rb') as f:
                    data = f.read()
            except OSError:
                self.send_error(404)
                return
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
            return
        # Host 可能带端口；跳转时换成真正的 HTTPS 端口。
        host = (self.headers.get('Host') or 'localhost').split(':', 1)[0]
        port = getattr(self.server, 'https_port', 443)
        suffix = '' if port == 443 else ':%d' % port
        target = 'https://%s%s%s' % (host, suffix, self.path)
        try:
            self.send_response(308)
            self.send_header('Location', target)
            self.send_header('Content-Length', '0')
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    do_HEAD = do_GET

    def log_request(self, code='-', size='-'):
        # 80 端口收到的几乎全是扫描器在扫随机路径，每条 308 都写一行会把
        # 日志和磁盘吃光；ACME 校验和真正的错误照样记。
        if str(code) == '308':
            return
        super().log_request(code, size)


class RedirectServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, addr, https_port=443, acme_webroot=''):
        self.https_port = https_port
        self.acme_webroot = acme_webroot
        super().__init__(addr, RedirectHandler)


def make_tls_context(certfile, keyfile):
    """只建上下文，不碰监听 socket —— 握手在 WebServer 的工作线程里做。"""
    proto = getattr(ssl, 'PROTOCOL_TLS_SERVER', ssl.PROTOCOL_TLS)
    ctx = ssl.SSLContext(proto)
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    return ctx


def web_url(scheme, host, port):
    if (scheme == 'https' and port == 443) or (scheme == 'http' and port == 80):
        return '%s://%s/' % (scheme, host)
    return '%s://%s:%d/' % (scheme, host, port)


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
    ap.add_argument('--port', type=int, default=80,
                    help='网页端口（默认 80；启用证书时通常写 443）')
    ap.add_argument('--extra-port', type=int, default=8766,
                    help='额外监听的端口：桌面端的素材同步还走 8766；0 = 不监听')
    ap.add_argument('--upstream', default='127.0.0.1:8765',
                    help='服务端地址 host:port（默认 127.0.0.1:8765）')
    ap.add_argument('--cert', default='',
                    help='TLS 证书链（fullchain.pem）；和 --key 一起给就启用 HTTPS')
    ap.add_argument('--key', default='', help='TLS 私钥（privkey.pem）')
    ap.add_argument('--acme-webroot', default='',
                    help='ACME HTTP-01 验证目录：/.well-known/acme-challenge/ 从这里读')
    ap.add_argument('--redirect-port', type=int, default=0,
                    help='再监听一个 HTTP 端口，整站 308 跳到 HTTPS（ACME 除外；0=不监听）')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    if bool(args.cert) != bool(args.key):
        log('--cert 和 --key 必须一起给')
        raise SystemExit(1)
    scheme = 'https' if args.cert else 'http'

    upstream = parse_upstream(args.upstream)
    ports = [args.port]
    if args.extra_port and args.extra_port != args.port:
        ports.append(args.extra_port)

    servers = []
    redirect = None
    tls_ctx = None
    if args.cert:
        try:
            tls_ctx = make_tls_context(args.cert, args.key)
        except (ssl.SSLError, ValueError, FileNotFoundError) as e:
            log('证书加载失败：%s' % e)
            raise SystemExit(1)

    # 主站端口必须先单独成功：它起不来就没什么可发布的。
    try:
        servers.append(WebServer((args.host, args.port), upstream,
                                 args.acme_webroot, tls_ctx=tls_ctx))
    except OSError as e:
        log('端口监听失败：%s' % e)
        log('80/443 端口需要 root 权限；也可以 --port 8766 换个端口。')
        raise SystemExit(1)

    # 素材同步端口是附加的：它被旧进程占着时，只让桌面端同步失败，
    # 绝不能把主站（80/443）一起拖下水 —— 以前就是这样整站打不开的。
    for p in ports[1:]:
        try:
            servers.append(WebServer((args.host, p), upstream,
                                     args.acme_webroot))
        except OSError as e:
            log('素材同步端口 %d 没起来：%s（主站不受影响，检查是否有旧进程占着）'
                % (p, e))

    if args.redirect_port and args.cert:
        try:
            redirect = RedirectServer((args.host, args.redirect_port),
                                      args.port, args.acme_webroot)
        except OSError as e:
            # 跳转只是方便：80 被别的程序占着时，HTTPS 本身不受影响。
            log('HTTP 跳转端口 %d 没起来：%s（HTTPS 不受影响）'
                % (args.redirect_port, e))

    log('网页版已启动：%s' % web_url(scheme, args.host,
                                    servers[0].server_address[1]))
    for s in servers[1:]:
        log('素材同步已启动：http://%s:%d/'
            % (args.host, s.server_address[1]))
    if redirect is not None:
        log('HTTP 自动跳转：http://%s:%d/ -> https://%s/'
            % (args.host, args.redirect_port,
               args.host if args.port == 443 else '%s:%d' % (args.host, args.port)))
    if args.acme_webroot:
        log('ACME 验证目录：%s' % args.acme_webroot)
    log('上游服务端：%s:%d' % upstream)

    # 第一个端口跑主循环，其余端口各开一条线程（共用同一套 HTTP 处理）。
    for s in servers[1:] + ([redirect] if redirect else []):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        log('收到中断，退出')
    finally:
        for s in servers + ([redirect] if redirect else []):
            s.server_close()


if __name__ == '__main__':
    main()
