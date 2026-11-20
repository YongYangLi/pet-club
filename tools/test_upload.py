# -*- coding: utf-8 -*-
"""桌面端上传姿势的自测（不开窗口）。

跑法：python tools/test_upload.py

三件事：
  1. 归一化：去白底 / 裁透明边 / 不放大 / 透明图不误伤
  2. 姿势名 → 动作 key：多个动作共用一张图时只出现一次
  3. 「我的动作」列什么、上传时能选什么

第 1 条最要紧：同一个算法在网页版（Canvas）和桌面端（PIL）各有一份，
两边尺寸不一致的话，同一张图从两个入口上去会让角色换姿势时上下跳。
"""

import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image                       # noqa: E402

from petclub import upload                  # noqa: E402
from petclub.app import App                 # noqa: E402

PASS = []
FAIL = []


def chk(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  <- ' + str(detail) if detail and not ok else '')),
          flush=True)


def _open(png):
    return Image.open(io.BytesIO(png)).convert('RGBA')


def _white_block(w=300, h=400, x0=120, x1=180, y0=150, y1=320):
    im = Image.new('RGB', (w, h), (255, 255, 255))
    for x in range(x0, x1):
        for y in range(y0, y1):
            im.putpixel((x, y), (200, 40, 40))
    return im


def _tmp(im, name='t.png'):
    d = tempfile.mkdtemp(prefix='petclub-up-')
    p = os.path.join(d, name)
    im.save(p)
    return p


def test_normalize():
    p = _tmp(_white_block())
    png, w, h = upload.normalize(p, 81, 210)
    out = _open(png)
    chk('白底被去掉、只留人物', (w, h) == (60, 170), (w, h))
    chk('输出尺寸不超过角色包围盒', w <= 81 and h <= 210, (w, h))
    # 1.9.4 起画布一律撑成角色包围盒（内容底部居中），所以「有没有被放大」
    # 要看内容的包围盒，而不是整张画布。
    cw, ch = out.getbbox()[2] - out.getbbox()[0], out.getbbox()[3] - out.getbbox()[1]
    chk('小图不放大（比例和原图一致）', (cw, ch) == (60, 170) and out.size == (81, 210),
        (out.size, (cw, ch)))

    # 人物内部的白色（眼睛高光、白衣服）连不到边缘，不能被清掉
    im = _white_block(x0=100, x1=200, y0=100, y1=300)
    for x in range(140, 161):
        for y in range(180, 201):
            im.putpixel((x, y), (255, 255, 255))
    png, w, h = upload.normalize(_tmp(im), 200, 240)
    out = _open(png)
    chk('人物内部的白色保留下来', out.getpixel((w // 2, h // 2))[3] == 255,
        out.getpixel((w // 2, h // 2)))

    # 背景不是纯白（偏灰）也要能去
    im = Image.new('RGB', (200, 300), (238, 238, 238))
    for x in range(80, 120):
        for y in range(120, 260):
            im.putpixel((x, y), (30, 160, 90))
    png, w, h = upload.normalize(_tmp(im), 200, 240)
    chk('浅灰底也能去掉', (w, h) == (40, 140), (w, h))

    # 已经抠好的透明图：不要再动（尤其不能把全透明区域当背景反复处理）
    im = Image.new('RGBA', (200, 300), (0, 0, 0, 0))
    for x in range(70, 130):
        for y in range(100, 280):
            im.putpixel((x, y), (10, 20, 30, 255))
    png, w, h = upload.normalize(_tmp(im), 200, 240)
    chk('透明 PNG 原样保留', (w, h) == (60, 180), (w, h))

    im = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    try:
        upload.normalize(_tmp(im))
        chk('全透明图要报错', False)
    except ValueError as e:
        chk('全透明图要报错', '透明' in str(e), e)

    # 超大图先缩到 600 再分析：不这么做的理由是时间，不是精度
    im = _white_block(w=2000, h=2600, x0=800, x1=1200, y0=1000, y1=2200)
    png, w, h = upload.normalize(_tmp(im), 200, 240)
    chk('超大图不会卡住，输出仍在上限内', w <= 200 and h <= 240 and w > 0, (w, h))

    p = _tmp(Image.new('RGB', (10, 10), (255, 255, 255)), 'empty.png')
    try:
        upload.normalize(p)
        chk('整张全白也能识别成「没内容」', False)
    except ValueError:
        chk('整张全白也能识别成「没内容」', True)


class FakeSp:
    def __init__(self, poses=('idle', 'wave', 'drink', 'heart', 'knock',
                              'sleep', 'jump')):
        self.poses = {n: None for n in poses}
        self.w = 100
        self.h = 200


class FakeSprites(dict):
    def __init__(self, sp=None):
        super().__init__()
        self._sp = sp or FakeSp()

    def get(self, role, default=None):
        return self._sp


class FakeApp:
    _pose_key = staticmethod(App._pose_key)
    _my_edits = App._my_edits
    _upload_targets = App._upload_targets

    def __init__(self, poses=None, custom=None, me='p1', sp=None):
        self._poses = poses or {}
        self._custom = custom or {}
        self.me = {'role': me} if me else None
        self.sprites = FakeSprites(sp)


def test_pose_key():
    chk('wave 取第一个动作 key', App._pose_key('wave') == 'wave',
        App._pose_key('wave'))
    chk('knock 归到「拍一拍」', App._pose_key('knock') == 'pat',
        App._pose_key('knock'))
    chk('idle 不对应任何动作', App._pose_key('idle') == '',
        App._pose_key('idle'))
    chk('不认识的姿势返回空', App._pose_key('c9') == '', App._pose_key('c9'))


def test_my_edits():
    custom = {'c1': {'role': 'p1', 'pose': 'c1', 'label': '跳舞'},
              'c2': {'role': 'p2', 'pose': 'c2', 'label': '别人的'}}
    a = FakeApp(poses={'p1': {'idle': 'r1', 'wave': 'r2', 'c1': 'r3'},
                       'p2': {'idle': 'r9', 'c2': 'r8'}},
                custom=custom)
    edits = a._my_edits('p1')
    kinds = [(e['kind'], e['label']) for e in edits]
    chk('列了站立、打招呼、我的自定义动作',
        kinds == [('pose', '站立'), ('pose', '打招呼'), ('custom', '跳舞')],
        kinds)
    chk('共用同一张图的动作只出现一次',
        len([e for e in edits if e['pose'] == 'wave']) == 1, kinds)
    chk('别人的自定义动作不进我的菜单',
        all(e['key'] != 'c2' for e in edits), kinds)
    chk('站立算「已换」而不是新增',
        [e for e in edits if e['key'] == 'idle'][0]['kind'] == 'pose', edits)
    chk('自定义动作有缩略图路径',
        os.path.basename(edits[-1]['icon']) == 'c1.png', edits[-1]['icon'])

    b = FakeApp(poses={}, custom=custom)
    chk('一张覆盖图都没有时菜单是空的', b._my_edits('p1') == [],
        b._my_edits('p1'))


def test_targets():
    custom = {'c1': {'role': 'p1', 'pose': 'c1', 'label': '跳舞'},
              'c2': {'role': 'p2', 'pose': 'c2', 'label': '别人的'}}
    a = FakeApp(poses={'p1': {'idle': 'r1', 'c1': 'r3'}}, custom=custom)
    t = a._upload_targets('p1')
    keys = [x['key'] for x in t]
    chk('第一项是「新建动作」', keys[0] == '' and t[0]['label'] == '新建动作', keys)
    chk('站立在可换之列', 'idle' in keys, keys)
    chk('没有重复的姿势（wave 只一次）', keys.count('wave') == 1, keys)
    chk('我自己的动作也能换图', 'c1' in keys, keys)
    chk('别人的动作不在候选里', 'c2' not in keys, keys)
    chk('内置动作的缩略图走出厂原图',
        t[1]['icon'].replace('\\', '/').endswith('roles/p1/idle.png'),
        t[1]['icon'])

    # 角色素材里没有的姿势不该出现在候选里（换了也画不出来）
    b = FakeApp(sp=FakeSp(poses=('idle', 'wave')))
    keys2 = [x['key'] for x in b._upload_targets('p1')]
    chk('素材里没有的姿势不进候选', 'sleep' not in keys2 and 'jump' not in keys2,
        keys2)


def test_http_shape():
    """用本地的小服务器验请求长什么样：路径、字段、错误怎么冒出来。

    不连真的网关：那边删文件会惊动沙箱的 safe-delete 钩子，测起来不稳。
    这里只看桌面端发出去的东西对不对得上 web_gateway 的接口。
    """
    seen = []
    reply = {'code': 200, 'body': {'ok': True, 'manifest': {}}}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            seen.append((self.path, json.loads(self.rfile.read(n).decode())))
            raw = json.dumps(reply['body']).encode()
            self.send_response(reply['code'])
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        upload.send('127.0.0.1', port, 'p1', b'\x89PNG-data',
                    label='跳舞', dur=2.6)
        path, body = seen[-1]
        chk('新建动作走 /api/sprites', path == '/api/sprites', path)
        chk('新建时不带 action 字段', 'action' not in body, body)
        chk('新建会把名字和时长一起发出去',
            body.get('label') == '跳舞' and abs(body.get('dur') - 2.6) < 1e-6,
            body)
        chk('图片按 base64 传', __import__('base64').b64decode(body['data'])
            == b'\x89PNG-data', body.get('data'))

        upload.send('127.0.0.1', port, 'p1', b'x', action='idle')
        path, body = seen[-1]
        chk('换图带 action、不带名字',
            'action' in body and body['action'] == 'idle' and 'label' not in body,
            body)

        upload.restore('127.0.0.1', port, 'p1', 'c1')
        path, body = seen[-1]
        chk('还走 /api/sprites/restore',
            path == '/api/sprites/restore'
            and body == {'role': 'p1', 'action': 'c1'}, (path, body))

        reply['code'] = 400
        reply['body'] = {'ok': False, 'error': '没有这个动作'}
        try:
            upload.restore('127.0.0.1', port, 'p1', 'nope')
            chk('服务端说不行时要抛错', False)
        except IOError as e:
            chk('服务端说不行时要抛错，而且保留原话', str(e) == '没有这个动作', e)

        reply['code'] = 500
        reply['body'] = {'oops': True}
        try:
            upload.restore('127.0.0.1', port, 'p1', 'x')
            chk('500 且没有说明时给出状态码', False)
        except IOError as e:
            chk('500 且没有说明时给出状态码', '500' in str(e), e)

        reply['code'] = 200
        reply['body'] = {'ok': False, 'error': '动作太多了'}
        try:
            upload.restore('127.0.0.1', port, 'p1', 'x')
            chk('200 但 ok=false 也算失败', False)
        except IOError as e:
            chk('200 但 ok=false 也算失败', str(e) == '动作太多了', e)
    finally:
        srv.shutdown()
        srv.server_close()

    # 连不上时不能把异常原样丢给界面
    try:
        upload.send('127.0.0.1', 1, 'p1', b'x', label='x')
        chk('连不上时抛的是中文 IOError', False)
    except IOError as e:
        chk('连不上时抛的是中文 IOError', '连不上' in str(e), e)
    except Exception as e:
        chk('连不上时抛的是中文 IOError', False, repr(e))


def main():
    test_normalize()
    test_pose_key()
    test_my_edits()
    test_targets()
    test_http_shape()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败：' + '、'.join(FAIL))
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
