# -*- coding: utf-8 -*-
"""桌面端上传自定义姿势：选图 → 本地归一化 → 传到服务器。

归一化算法和网页版 `web/js/upload.js` 是同一套（那边用 Canvas，这边用 PIL）：

    去白底 → 裁透明边 → 等比缩进角色现有的包围盒

两边都留一份实现，是因为同一个人可能今天在网页版传、明天在桌面端传 ——
算法不一致的话，同一张图从两个入口上去会得到两个尺寸，换姿势时人就会上下跳。

服务端仍然只做校验、不解码图像（那边没装 Pillow），所以缩放必须发生在
发请求之前。网络部分也在这儿，app.py 只管调。
"""

import base64
import io
import json
import urllib.error
import urllib.request

from PIL import Image

CAP = 600          # 分析用工作尺寸：够精细，又不是几百万像素
TOL = 34           # 去背容差（RGB 每通道）
MAX_W = 200        # 和 custom.py 的上限对齐：超了服务端也会拒
MAX_H = 240
TIMEOUT = 20.0      # 多帧动作一次可能好几 MB，给足上传时间


def _keyout(im, tol=TOL):
    """从四边往里灌，只清和四角同色、且连通到边缘的像素。

    人物内部的白色（眼睛高光、白衣服）连不到边缘，不会被误伤 ——
    和网页版 `keyOut` 逐行对应，两边结果才一致。
    """
    w, h = im.size
    data = bytearray(im.tobytes('raw', 'RGBA'))
    alpha = im.getchannel('A').histogram()
    if sum(alpha[:8]) > (w * h) / 20:
        return im               # 已经是抠好的透明图，别再动

    corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
    r = g = b = 0
    for x, y in corners:
        i = (y * w + x) * 4
        r += data[i]
        g += data[i + 1]
        b += data[i + 2]
    n = len(corners)
    r //= n
    g //= n
    b //= n

    seen = bytearray(w * h)
    stack = []

    def push(p):
        if not seen[p]:
            seen[p] = 1
            stack.append(p)

    for x in range(w):
        push(x)
        push((h - 1) * w + x)
    for y in range(h):
        push(y * w)
        push(y * w + w - 1)

    while stack:
        p = stack.pop()
        i = p * 4
        if (abs(data[i] - r) > tol or abs(data[i + 1] - g) > tol
                or abs(data[i + 2] - b) > tol):
            continue
        data[i + 3] = 0
        x = p % w
        y = p // w
        if x > 0:
            push(p - 1)
        if x < w - 1:
            push(p + 1)
        if y > 0:
            push(p - w)
        if y < h - 1:
            push(p + w)

    return Image.frombytes('RGBA', (w, h), bytes(data))


def _alpha_box(im):
    """透明边裁掉。阈值取 8，和网页版一致 —— 边缘那圈半透明像素不算内容。"""
    mask = im.getchannel('A').point(lambda v: 255 if v > 8 else 0)
    return mask.getbbox()


def _prepare(im, keyout):
    im = im.convert('RGBA')
    if max(im.size) > CAP:
        im.thumbnail((CAP, CAP), Image.LANCZOS)
    if keyout:
        im = _keyout(im)
    return im


def _union_box(ims):
    """所有帧透明边界的并集：动画按同一个画布裁，换帧时人不会跳。"""
    box = None
    for im in ims:
        b = _alpha_box(im)
        if b is None:
            continue
        if box is None:
            box = list(b)
        else:
            box[0] = min(box[0], b[0])
            box[1] = min(box[1], b[1])
            box[2] = max(box[2], b[2])
            box[3] = max(box[3], b[3])
    return tuple(box) if box else None


def normalize_frames(paths, max_w=MAX_W, max_h=MAX_H, keyout=True):
    """读一组图，返回 (png 字节列表, 宽, 高)。所有帧输出同一个画布尺寸。"""
    paths = list(paths or ())
    if not paths:
        raise ValueError('先选一张图')
    ims = []
    for i, path in enumerate(paths):
        try:
            im = Image.open(path)
            im.load()
        except (OSError, ValueError):
            raise ValueError('第 %d 张图读不出来' % (i + 1)
                             if len(paths) > 1 else '这张图读不出来')
        ims.append(_prepare(im, keyout))
    box = _union_box(ims)
    if box is None:
        raise ValueError('整张图都是透明的，换一张吧')
    x0, y0, x1, y1 = box
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    # 上限取「这个角色现有的包围盒」：新图绝不撑大窗口，
    # 否则其他姿势会跟着一起缩小。
    max_w = max(1, min(MAX_W, int(max_w)))
    max_h = max(1, min(MAX_H, int(max_h)))
    k = min(max_w / bw, max_h / bh, 1.0)
    ow = max(1, int(round(bw * k)))
    oh = max(1, int(round(bh * k)))
    out = []
    for im in ims:
        part = im.crop(box)
        if k < 1.0:
            part = part.resize((ow, oh), Image.LANCZOS)
        buf = io.BytesIO()
        part.save(buf, 'PNG', optimize=True)
        out.append(buf.getvalue())
    return out, ow, oh


def normalize(path, max_w=MAX_W, max_h=MAX_H, keyout=True):
    """读一张图，返回 (png 字节, 宽, 高)。读不出来就抛 ValueError（中文原因）。"""
    frames, w, h = normalize_frames([path], max_w, max_h, keyout)
    return frames[0], w, h


# ---------------- 网络 ----------------

def _post(host, http_port, path, payload, timeout=TIMEOUT):
    body = json.dumps(payload).encode('utf-8')
    url = 'http://%s:%d%s' % (host, int(http_port), path)
    req = urllib.request.Request(
        url, body, {'Content-Type': 'application/json',
                    'Cache-Control': 'no-store'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        # 服务端把「为什么不行」写在 body 里，别换成一句编号
        detail = ''
        try:
            detail = json.loads(e.read().decode('utf-8')).get('error') or ''
        except (ValueError, OSError):
            pass
        raise IOError(detail or ('服务器返回 %d' % e.code))
    except (urllib.error.URLError, OSError) as e:
        raise IOError('连不上服务器（%s）' % getattr(e, 'reason', e))
    try:
        data = json.loads(raw.decode('utf-8'))
    except ValueError:
        raise IOError('服务器回了个看不懂的响应')
    if not isinstance(data, dict) or not data.get('ok'):
        raise IOError((data or {}).get('error') or '服务器拒绝了这次操作')
    return data


def send(host, http_port, role, frames, action='', label='', dur=2.0, fps=None):
    """action 给了就是换图，没给就是新建动作。

    frames 是一组 PNG 字节；同时带一份 data（第一帧）给还没升级的服务端，
    老服务端会忽略 frames，至少能存成静态图。
    """
    if isinstance(frames, (bytes, bytearray)):
        frames = [bytes(frames)]
    frames = list(frames or ())
    if not frames:
        raise IOError('先选一张图')
    payload = {
        'role': role,
        'data': base64.b64encode(frames[0]).decode('ascii'),
        'frames': [base64.b64encode(f).decode('ascii') for f in frames],
    }
    if fps is not None:
        payload['fps'] = fps
    if action:
        payload['action'] = action
    else:
        payload['label'] = label
        payload['dur'] = dur
    return _post(host, http_port, '/api/sprites', payload)


def restore(host, http_port, role, action):
    return _post(host, http_port, '/api/sprites/restore',
                 {'role': role, 'action': action})
