# -*- coding: utf-8 -*-
"""图片消息的编解码与缓存。

聊天里发一张图，要过两道关，两道都会把人卡住：

  1. **网络上**：一条 JSON 行，图片是 base64 塞在 `img` 字段里的。
     手机截图 1080×2400、PNG 两三兆 —— base64 再涨三分之一，一条消息四五兆，
     发的时候界面像死了，收的时候对面得先解四兆的字符串。所以发之前在本地
     缩到 480px 长边、重新编码；服务端那边也只收 300KB 以内的。

  2. **屏幕上**：对话面板每次来消息都整体重建一次。要是每条都当场解码原图，
     聊到几十条就开始卡。所以解码 + 缩放一次就进缓存，之后直接拿 QPixmap。

缓存键是 (crc32, 边长) 而不是 b64 字符串本身：几十 KB 的字符串当字典键，
每次查表都要把整串哈希一遍，反而比解码还慢。

本模块只依赖 Pillow，不碰 Qt —— 图片怎么变成屏幕上的像素是 chat.py 的事。
"""

import base64
import io
import os
import zlib
from collections import OrderedDict

from PIL import Image


MAX_SIDE = 1280             # 发之前缩到这个长边：气泡里用不到，点开看的时候要够清楚
MAX_BYTES = 1200 * 1024     # 图片编码后的上限（base64 后约 1.6MB，服务端 MAX_IMG 2.9MB）
MAX_FILE = 2 * 1024 * 1024  # 文件原样传，上限 2MB（base64 后约 2.7MB，服务端收得下）
JPEG_Q = 88

# 屏幕上两种媒体的大小上限（像素）
IMG_BOX = 200               # 普通图片：白框里的那张，长边不超过 200
STK_BOX = 128               # 表情：它本身就是一张成品卡片，小一点更像素

# 解码缓存：桌宠常驻，缓存不设上限就是慢慢泄漏。40 张 × 约 160KB 够了。
_CACHE = OrderedDict()
_CACHE_MAX = 40

def encode_image(path, side=MAX_SIDE, png=False):
    """读一个图片文件，缩到能发的尺寸，返回 (base64, 宽, 高)。

    失败抛 ValueError，文案可以直接显示在提示行上 —— 「这张图太大了」
    比一个 traceback 有用得多。
    """
    try:
        im = Image.open(path)
        im.load()
    except (OSError, ValueError, SyntaxError,
            Image.DecompressionBombError):
        # PIL 对不同格式抛的异常五花八门（不是图片抛 OSError，截断的
        # 文件可能抛 SyntaxError），这里一律当「打不开」
        raise ValueError('这个文件打不开，换一张试试')
    return encode_pil(im, side, png)


def encode_pil(im, side=MAX_SIDE, png=False):
    """已经在内存里的图（剪贴板粘上来的就是）→ (base64, 宽, 高)。"""
    if im.width < 1 or im.height < 1:
        raise ValueError('这张图是空的')

    data = b''
    for s in (side, 960, 720, 480, 320, 240, 160):
        cur = _fit(im, s)
        data = _dump(cur, png)
        if len(data) <= MAX_BYTES:
            break
    else:
        raise ValueError('这张图太大了，换一张小点的')

    return (base64.b64encode(data).decode('ascii'),
            cur.width, cur.height)


def _fit(im, side):
    """等比缩到长边 side。已经更小就不放大（放大只会变糊、还更大）。"""
    k = min(1.0, float(side) / max(im.width, im.height))
    if k >= 1.0:
        return im
    w = max(1, int(round(im.width * k)))
    h = max(1, int(round(im.height * k)))
    return im.resize((w, h), Image.LANCZOS)


def _dump(im, png):
    buf = io.BytesIO()
    if png:
        im.convert('RGBA').save(buf, 'PNG', optimize=True)
        return buf.getvalue()
    if im.mode in ('RGBA', 'LA', 'P'):
        # JPEG 没有 alpha 通道：不铺白底的话，透明处会变成黑的
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    else:
        im = im.convert('RGB')
    im.save(buf, 'JPEG', quality=JPEG_Q, optimize=True)
    return buf.getvalue()


def encode_file(path):
    """读一个任意文件，返回 (base64, 字节数)。

    文件不缩、不转码 —— 它是要给对方**用**的，不是看的。所以只能限个大小：
    一条 JSON 行里塞两兆已经够呛，再大就该走别的通道了（这点小玩具没有分片）。
    """
    try:
        size = os.path.getsize(path)
        if size <= 0:
            raise ValueError('这个文件是空的')
        if size > MAX_FILE:
            raise ValueError('文件太大了（上限 %s）' % _size_text(MAX_FILE))
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        raise ValueError('这个文件读不出来')
    return base64.b64encode(raw).decode('ascii'), len(raw)


def _size_text(n):
    return '%.0fMB' % (n / 1048576.0) if n >= 1048576 else '%.0fKB' % (n / 1024.0)


def decode(b64):
    """base64 → PIL 图。坏数据返回 None（宁可画一个「图打不开」，别崩）。"""
    if not isinstance(b64, str) or not b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im.convert('RGBA')
    except (ValueError, OSError, SyntaxError, TypeError):
        return None


def thumb(b64='', path='', limit=IMG_BOX):
    """一条消息里的图 → 缩进 limit 宽的 PIL 图（带 LRU 缓存）。

    base64 优先（网络来的），没有才读本地路径（自己刚发出去的那条）。
    两个都读不出来返回 None —— 调用方画一句「图打不开」就行，别崩。
    """
    src = b64 or path
    if not src:
        return None
    key = (zlib.crc32(src.encode('utf-8')), limit)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit

    im = None
    if b64:
        im = decode(b64)
    elif os.path.exists(path):
        try:
            im = Image.open(path)
            im.load()
            im = im.convert('RGBA')
        except (OSError, ValueError, SyntaxError):
            im = None
    if im is None:
        return None
    if im.width > limit:
        h = max(1, int(round(im.height * limit / float(im.width))))
        im = im.resize((limit, h), Image.LANCZOS)

    _CACHE[key] = im
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return im


# 点开看大图时用的「原图」：只按长边兜一个上限，避免一张 4K 截图
# 在内存里铺成几十兆。缓存也小 —— 大图不值当多留。
FULL_SIDE = 2560
_FULL = OrderedDict()
_FULL_MAX = 4


def full(b64='', path='', side=FULL_SIDE):
    """一条消息里的图 → 接近原始的 PIL 图（长边不超过 side）。

    气泡里的缩略图是 240 宽，拿它放大就是糊的；点开预览必须重新解一次，
    自己刚发的那张还要优先读本地原文件 —— 本地文件比编码后的副本更清楚。
    """
    src = path if (path and os.path.exists(path)) else b64
    if not src:
        return None
    key = (zlib.crc32((src or '').encode('utf-8')), side)
    hit = _FULL.get(key)
    if hit is not None:
        _FULL.move_to_end(key)
        return hit

    im = None
    if path and os.path.exists(path):
        try:
            im = Image.open(path)
            im.load()
            im = im.convert('RGBA')
        except (OSError, ValueError, SyntaxError):
            im = None
    if im is None and b64:
        im = decode(b64)
    if im is None:
        return None

    longest = max(im.width, im.height)
    if longest > side:
        k = float(side) / longest
        im = im.resize((max(1, int(round(im.width * k))),
                        max(1, int(round(im.height * k)))), Image.LANCZOS)

    _FULL[key] = im
    if len(_FULL) > _FULL_MAX:
        _FULL.popitem(last=False)
    return im


# ---------------- 表情 ----------------
#
# 不打包任何表情图片，也不自己渲染位图：Qt 6 在 Windows 上走 DirectWrite，
# Segoe UI Emoji 的彩色层（COLR/CPAL）能直接画出来，字体字号交给样式表就行。
# 这里只需要一份字符表给拾取面板排列。
EMOJI_CHARS = ('\U0001F600', '\U0001F604', '\U0001F605', '\U0001F602',
               '\U0001F60A', '\U0001F609', '\U0001F60D', '\U0001F618',
               '\U0001F61C', '\U0001F633', '\U0001F62D', '\U0001F621',
               '\U0001F631', '\U0001F634', '\U0001F644', '\U0001F44D',
               '\U0001F44F', '\U0001F64F')

# 表情页最前面那一格留给一张自定义贴图：微信那只狗头不是 Unicode 字符，
# 只能是一张图。约定放在 assets/emoji/doge.png —— **有就占第一格，没有就不排**
# （不留空格子）。它走 sticker 通道（PNG 原样过去），聊天里按原尺寸显示，
# 比 30pt 的字符大一圈，和微信里的观感一致。
# 换图直接覆盖这个文件即可（正方形，边长 96 的整数倍）。
DOGE_LABEL = '狗头'
DOGE_FILE = 'doge.png'


def doge_path(assets):
    return os.path.join(assets, 'emoji', DOGE_FILE)


# ---------------- 表情包（角色卡片） ----------------

STICKER_POSES = ('idle', 'wave', 'drink', 'heart', 'jump', 'sleep', 'knock', 'drag')


def sticker_dir(assets, role):
    return os.path.join(assets, 'stickers', role)


def sticker_paths(assets, role):
    """这个角色的表情包文件（按 STICKER_POSES 的顺序，缺的就跳过）。"""
    d = sticker_dir(assets, role)
    out = []
    for pose in STICKER_POSES:
        p = os.path.join(d, pose + '.png')
        if os.path.exists(p):
            out.append(p)
    return out
