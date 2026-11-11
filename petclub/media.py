# -*- coding: utf-8 -*-
"""图片消息的编解码与缓存。

聊天里发一张图，要过两道关，两道都会把人卡住：

  1. **网络上**：一条 JSON 行，图片是 base64 塞在 `img` 字段里的。
     手机截图 1080×2400、PNG 两三兆 —— base64 再涨三分之一，一条消息四五兆，
     发的时候界面像死了，收的时候对面得先解四兆的字符串。所以发之前在本地
     缩到 480px 长边、重新编码；服务端那边也只收 300KB 以内的。

  2. **屏幕上**：画布每次重绘都把 50 条历史重画一遍。要是每条都留一份原图，
     滚动会一卡一卡的。所以解码一次就进缓存，之后重绘直接拿 PhotoImage。

缓存键是 (crc32, 边长) 而不是 b64 字符串本身：几十 KB 的字符串当字典键，
每次查表都要把整串哈希一遍，反而比解码还慢。
"""

import base64
import io
import os
import zlib
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .surface import IS_WIN

MAX_SIDE = 480              # 发之前缩到这个长边（再大在气泡里也看不出来）
MAX_BYTES = 300 * 1024      # 图片编码后的上限，和 server.MAX_IMG 对齐
MAX_FILE = 2 * 1024 * 1024  # 文件原样传，上限 2MB（base64 后约 2.7MB，服务端收得下）
JPEG_Q = 85

# 屏幕上两种媒体的大小上限（像素）
IMG_BOX = 200               # 普通图片：白框里的那张，长边不超过 200
STK_BOX = 128               # 表情：它本身就是一张成品卡片，小一点更像素

# 解码缓存：桌宠常驻，缓存不设上限就是慢慢泄漏。40 张 × 约 160KB 够了。
_CACHE = OrderedDict()
CACHE_MAX = 40


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
    for s in (side, 360, 256, 160):
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


def photo(b64, box):
    """base64 → 缩进 box 的 PhotoImage（带缓存）。解码失败返回 None。"""
    if not isinstance(b64, str) or not b64:
        return None
    key = (zlib.crc32(b64.encode('ascii', 'ignore')), box)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit

    im = decode(b64)
    if im is None:
        _CACHE[key] = None          # 坏的也记住，别每次重绘都解一遍
        return None
    im = _fit(im, box)
    ph = ImageTk.PhotoImage(im, master=None)
    _CACHE[key] = ph
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)
    return ph


def clear_cache():
    _CACHE.clear()


# ---------------- 表情 ----------------
#
# 不打包任何表情图片：Windows 自带 Segoe UI Emoji，它就是一套很好的表情包。
# 用 PIL 把它按需要的尺寸渲成位图，画到画布上就完了 —— 零素材体积，任意 DPI 都清楚。
#
# 为什么不用 Tk 直接写这个字符：
#   Tk 8.6 走 GDI 画字，遇到 U+1F600 以上这些（四个字节的）字符基本只会画出豆腐块。
#   同一台机器上 PIL 拿 COLR/CPAL 的色彩层一渲就正常 —— 所以走位图这条路。

EMOJI_CHARS = ('😀', '😄', '😅', '😂', '😊', '😉',
               '😍', '😘', '😜', '😳', '😭', '😡',
               '😱', '😴', '🙄', '👍', '👏', '🙏')

EMOJI_FONTS = ('C:/Windows/Fonts/seguiemj.ttf',
               'C:/Windows/Fonts/segoeui.ttf',
               '/System/Library/Fonts/Apple Color Emoji.ttc')

_FONT_CACHE = {}
_EMOJI_CACHE = {}


def emoji_font(px):
    """按像素找一次字体，找不到就返回 None（调用方退回文字）。"""
    if px in _FONT_CACHE:
        return _FONT_CACHE[px]
    font = None
    for p in EMOJI_FONTS:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, px)
                break
            except OSError:
                continue
    _FONT_CACHE[px] = font
    return font


def emoji_image(ch, px):
    """把一个表情字符渲成 px 见方的 RGBA 图（等比缩进方框，居中）。

    三步是有原因的：
      1. 在 3 倍大的画布上以 2 倍字号画 —— 直接按目标尺寸画，边缘是毛的；
      2. 按 alpha 裁掉四周空白 —— 不同字形的留白差很多，不裁就对不齐；
      3. 等比装进方框 —— 拉成正方会把 🙏 压扁。
    """
    key = (ch, px)
    if key in _EMOJI_CACHE:
        return _EMOJI_CACHE[key]
    font = emoji_font(px * 2)
    if font is None:
        _EMOJI_CACHE[key] = None
        return None
    pad = px
    canvas = Image.new('RGBA', (px * 3, px * 3), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    # anchor='mm' 按字形的**中心**对齐：不同表情的字形在基线上高低不一（🙏 朝下、
    # 👍 朝上），用左上角定位必然有几个被画布边缘切掉。
    mid = px * 1.5
    try:
        draw.text((mid, mid), ch, font=font, anchor='mm', embedded_color=True)
    except TypeError:
        # 老 Pillow 不认 embedded_color：退成单色，至少能看
        draw.text((mid, mid), ch, font=font, anchor='mm', fill=(40, 40, 40, 255))

    box = canvas.split()[3].getbbox()
    if not box:
        _EMOJI_CACHE[key] = None
        return None
    im = canvas.crop(box)
    k = min(px / float(im.width), px / float(im.height))
    im = im.resize((max(1, int(round(im.width * k))),
                    max(1, int(round(im.height * k)))), Image.LANCZOS)
    _EMOJI_CACHE[key] = im
    return im


def emoji_photo(ch, size):
    """表情 → PhotoImage（带缓存）。字体缺失时返回 None。"""
    key = ('ph', ch, size)
    if key in _EMOJI_CACHE:
        return _EMOJI_CACHE[key]
    im = emoji_image(ch, size)
    if im is None:
        _EMOJI_CACHE[key] = None
        return None
    ph = ImageTk.PhotoImage(im, master=None)
    _EMOJI_CACHE[key] = ph
    return ph


EMOJI_BOX = 44              # 聊天里那个表情有多大


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


def thumb(path, size):
    """文件 → 小缩略图 PhotoImage（表情面板里那格）。读不出来返回 None。"""
    key = ('thumb', path, size)
    if key in _EMOJI_CACHE:
        return _EMOJI_CACHE[key]
    try:
        im = Image.open(path).convert('RGBA')
    except (OSError, ValueError):
        _EMOJI_CACHE[key] = None
        return None
    im.thumbnail((size, size), Image.LANCZOS)
    ph = ImageTk.PhotoImage(im, master=None)
    _EMOJI_CACHE[key] = ph
    return ph


# ---------------- 剪贴板 ----------------

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tif', '.tiff')

PASTE_MAX = 3               # 一次粘贴最多发几个，防止有人粘贴一整个文件夹
PASTE_HARD = 20             # 剪贴板里真有几百个文件时，认前 20 个就够判断了


def clipboard_items(limit=PASTE_HARD):
    """剪贴板里能直接发的东西：[(mt, payload)]。

    Windows 剪贴板里跟这里有关的一共三种形态：
      · 截图 / 复制的图  → CF_DIB，PIL 会给回一张 Image
      · 资源管理器里复制的文件 → CF_HDROP，PIL 会给回一个路径列表
      · 纯文字           → PIL 认不出来，返回 None（调用方去走正常的文字粘贴）

    payload 是 PIL 图或路径字符串；返回空列表就表示「这里没有能发的东西」。
    **这里不按 PASTE_MAX 截断**：真要截也得知会一声能截多少 —— 由调用方切片并
    在提示行上说明，静默少发是最难受的一种「成功」。
    """
    if not IS_WIN:
        return []
    try:
        from PIL import ImageGrab
        data = ImageGrab.grabclipboard()
    except Exception:
        # 剪贴板被别的程序占着、里面有 PIL 解析不了的格式 —— 都不该让聊天崩掉
        return []
    if isinstance(data, Image.Image):
        return [('img', data)]
    if isinstance(data, list):
        out = []
        for p in data[:limit]:
            if isinstance(p, str) and os.path.isfile(p):
                ext = os.path.splitext(p)[1].lower()
                out.append(('img' if ext in IMAGE_EXTS else 'file', p))
        return out
    return []

