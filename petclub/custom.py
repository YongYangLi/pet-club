# -*- coding: utf-8 -*-
"""用户上传的自定义动作与姿势图。

存在 `data/custom/` 下，和 `assets/` 完全分开：

  assets/   出厂素材 —— 进 git、打进 exe、参与 build 指纹、随发布包覆盖
  data/     运行期内容 —— 不进版本库、不进发布包、不影响 build 指纹

按这个分法，用户传一张图不会让全群刷新页面（build 不变），也不会被
下一次发布覆盖掉。这是整套「用户自己上传」能成立的前提。

两样东西：

  registry.json        新建的动作（名字 / 时长 / 属于哪个角色）
  <role>/<pose>.png    某个角色的第一帧。两种来源共用一套存储：
                       · 新动作的图（pose 就是动作 key）
                       · 换掉内置动作的图（pose 是内置的姿势名，比如 wave）
  <role>/<pose>.<n>.png  多帧动作的第 n 帧（n 从 1 开始）。客户端按 8fps 循环。

多帧只给自定义动作：内置姿势（wave 等）永远单张。第一帧沿用 <pose>.png，
所以老客户端不认识多帧时也能显示一张静态图。

纯标准库：服务端只需要校验 PNG 头，不装 Pillow。
"""

import hashlib
import json
import os
import re
import struct
import threading

from .actions import ACTIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'custom')

MAX_BYTES = 2 * 1024 * 1024      # 单帧体积上限（base64 解码后）
MAX_W = 260                      # 和现有素材同量级：p1 最宽 158、最高 234；
                                 # 钢管舞那类「道具 + 大甩臂」的横构图要 242 宽
MAX_H = 240
MAX_FRAMES = 48                  # 一个动作最多几帧：40 帧 12fps 够跑 3 秒的连贯小动画，
                                 # 再往上总体积会先被 MAX_TOTAL 挡住
MAX_TOTAL = 6 * 1024 * 1024      # 一次上传所有帧的总体积上限
FPS = 8                          # 多帧动作的默认帧率
MIN_FPS = 2
MAX_FPS = 24

# 动作播放时可以带一条移动轨迹（御剑飞行那种）。只开放几种模板，
# 不让客户端传任意坐标：屏幕尺寸每个客户端都不一样，存比例最稳。
MOVE_KINDS = ('line', 'arc', 'orbit', 'hover')
MOVE_MIN_DUR = 1.2               # 带移动时动作至少这么久，不然像瞬移


def clamp_fps(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return FPS
    return round(min(MAX_FPS, max(MIN_FPS, value)), 1)


def clamp_move(value):
    """把客户端传来的移动配置收成白名单 + 几个夹紧的比例。

    返回 None 表示「静止」。kind 不认识时当作静止，不报错 —— 老客户端
    或手滑传错一个字段，不该让整次上传失败。
    """
    if not isinstance(value, dict):
        return None
    kind = str(value.get('kind') or '').strip()
    if kind not in MOVE_KINDS:
        return None
    direction = str(value.get('dir') or 'lr')
    if direction not in ('lr', 'rl'):
        direction = 'lr'
    try:
        height = float(value.get('height'))
    except (TypeError, ValueError):
        height = 0.35
    try:
        distance = float(value.get('distance'))
    except (TypeError, ValueError):
        distance = 0.8
    return {
        'kind': kind,
        'dir': direction,
        'height': round(min(0.8, max(0.05, height)), 2),
        'distance': round(min(1.0, max(0.2, distance)), 2),
    }

# 动作总数上限。6 个身份 × 每人十几个已经是极限，再多右键菜单就没法看了；
# 顺带挡住「往上传几百张把磁盘塞满」。
MAX_ACTIONS = 60

# 只允许小写字母开头的短名字。既挡住 `../` 这类路径穿越，
# 也顺手守住 server.py 的 clean(kind, 16) —— 动作 key 超长会被截断。
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,15}$')

_lock = threading.Lock()


def data_dir():
    os.makedirs(DATA, exist_ok=True)
    return DATA


def _role_dir(role):
    d = os.path.join(data_dir(), role)
    os.makedirs(d, exist_ok=True)
    return d


def image_path(role, pose):
    return os.path.join(data_dir(), role, pose + '.png')


def frame_path(role, pose, index):
    """第 index 帧的路径。0 就是 <pose>.png（老客户端只认这一张）。"""
    if not index:
        return image_path(role, pose)
    return os.path.join(data_dir(), role, '%s.%d.png' % (pose, index))


def frame_files(role, pose):
    """某个姿势现有的所有帧，按序号排列。

    <pose>.png 是第一帧，<pose>.1.png、<pose>.2.png 是后续帧。
    目录里还可能有别的姿势，靠前缀 + 纯数字后缀精确匹配。
    """
    paths = []
    first = image_path(role, pose)
    if os.path.isfile(first):
        paths.append(first)
    d = os.path.join(data_dir(), role)
    try:
        names = os.listdir(d)
    except OSError:
        return paths
    pattern = re.compile(r'^%s\.(\d+)\.png$' % re.escape(pose))
    extra = []
    for name in names:
        m = pattern.match(name)
        if m:
            extra.append((int(m.group(1)), os.path.join(d, name)))
    paths.extend(p for _, p in sorted(extra))
    return paths


def valid_name(name):
    return bool(name) and bool(NAME_RE.match(name))


def png_size(data):
    """读 PNG 的 IHDR 拿宽高。不是合法 PNG 返回 None。

    只看头不解码：IHDR 里的宽高就是解码器要分配的画布尺寸，
    卡住它等于卡住解压炸弹（一个几 KB 的 PNG 可以声称 60000×60000）。
    """
    if len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    if data[12:16] != b'IHDR':
        return None
    w, h = struct.unpack('>II', data[16:24])
    if not w or not h:
        return None
    return w, h


def check_png(data):
    """校验一张上传的图。返回 (宽, 高)；不合格返回 (None, 原因)。"""
    if not data:
        return None, '图片是空的'
    if len(data) > MAX_BYTES:
        return None, '单张图片超过 %d MB' % (MAX_BYTES // 1024 // 1024)
    size = png_size(data)
    if size is None:
        return None, '不是有效的 PNG'
    w, h = size
    if w > MAX_W or h > MAX_H:
        return None, '画布 %d×%d 超过上限 %d×%d' % (w, h, MAX_W, MAX_H)
    return size, ''


def check_frames(frames):
    """校验一组帧。返回 ((宽, 高), 原因)；所有帧必须同尺寸，否则播放会跳。"""
    if not frames:
        return None, '至少要有一帧图片'
    if len(frames) > MAX_FRAMES:
        return None, '最多 %d 帧' % MAX_FRAMES
    if sum(len(f) for f in frames) > MAX_TOTAL:
        return None, '所有帧加起来超过 %d MB' % (MAX_TOTAL // 1024 // 1024)
    size = None
    for i, data in enumerate(frames):
        s, err = check_png(data)
        if err:
            return None, ('第 %d 帧：%s' % (i + 1, err)) if len(frames) > 1 else err
        if size is None:
            size = s
        elif s != size:
            return None, '每一帧的画布大小必须一致（第 1 帧 %d×%d）' % size
    return size, ''


# ---------------- 注册表 ----------------

def _registry_path():
    return os.path.join(data_dir(), 'registry.json')


def _empty():
    return {'version': 1, 'seq': 0, 'actions': {}}


def load_registry():
    """读注册表。坏文件当空表 —— 一张坏 JSON 不该让整个上传功能瘫痪。"""
    try:
        with open(_registry_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get('actions'), dict):
        data['actions'] = {}
    try:
        data['seq'] = int(data.get('seq') or 0)
    except (TypeError, ValueError):
        data['seq'] = 0
    return data


def save_registry(reg):
    """原子写：先落 .tmp 再 replace。写到一半断电不会留下半截 JSON。"""
    path = _registry_path()
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def _write_frames(role, pose, frames):
    """写一组帧，多出来的旧帧顺手删掉。"""
    _role_dir(role)                 # 第一次上传时这个角色还没有目录
    for i, data in enumerate(frames):
        path = frame_path(role, pose, i)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    for stale in frame_files(role, pose)[len(frames):]:
        try:
            os.remove(stale)
        except OSError:
            pass


def drop_frames(role, pose):
    """删掉一个姿势的所有帧。返回是否真的删掉了东西。"""
    hit = False
    for path in frame_files(role, pose):
        try:
            os.remove(path)
            hit = True
        except OSError:
            pass
    return hit


# ---------------- 对外：动作表 / 清单 ----------------

def actions():
    """自定义动作，结构和 actions.ACTIONS 一致（外加 role / custom / solo）。"""
    return dict(load_registry().get('actions') or {})


def order():
    """按创建顺序。注册表存的是 dict，Python 3.7+ 保序，够用。"""
    return tuple(load_registry().get('actions') or {})


def rev(role, pose):
    """内容版本号。用所有帧的 mtime+size 派生：任何一帧被覆盖都会变。

    不用内容 hash 是因为每张图几百 KB，每次清单请求都读一遍不划算；
    mtime 精度到纳秒，手工替换文件也能立刻被认出来。
    """
    paths = frame_files(role, pose)
    if not paths:
        return ''
    h = hashlib.sha1()
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        h.update(('%d:%d;' % (st.st_mtime_ns, st.st_size)).encode('utf-8'))
    return h.hexdigest()[:8]


def manifest():
    """客户端同步素材用的清单：动作表、覆盖图，以及多帧动作的帧数和帧率。"""
    reg = load_registry()
    poses = {}
    anims = {}
    fps_by_pose = {}
    for spec in (reg.get('actions') or {}).values():
        if isinstance(spec, dict) and spec.get('pose'):
            fps_by_pose[spec['pose']] = clamp_fps(spec.get('fps'))
    base = data_dir()
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for role in names:
        d = os.path.join(base, role)
        if not os.path.isdir(d) or not valid_name(role):
            continue
        m = {}
        for name in sorted(os.listdir(d)):
            if not name.endswith('.png') or name.startswith('.'):
                continue
            pose = name[:-4]
            if not valid_name(pose):
                continue            # c1.1.png 这种后续帧不进 poses
            r = rev(role, pose)
            if not r:
                continue
            m[pose] = r
            frames = len(frame_files(role, pose))
            if frames > 1:
                anims.setdefault(role, {})[pose] = {
                    'frames': frames,
                    'fps': fps_by_pose.get(pose, FPS),
                    'rev': r}
        if m:
            poses[role] = m
    return {
        'actions': dict(reg.get('actions') or {}),
        'poses': poses,
        'anims': anims,
        'seq': int(reg.get('seq') or 0),
    }


# ---------------- 写入 ----------------

def norm_label(label):
    """动作名的比较键：去首尾空白、内部空白归一、大小写不敏感。

    名字是**跨身份共享**的那个东西 —— 我做「比耶」，对方也有「比耶」，
    他跟着做的是他自己那张图（见 App._perform_custom / web main.js 的同名
    分支）。所以两端归一化规则必须一模一样：网页端 actions.js 的 normName
    是这份实现的等价版本，改一边就得改另一边。
    """
    return ' '.join(str(label or '').split()).casefold()


def _norm_frames(frames):
    """允许调用方传 bytes（老接口）或 list。"""
    if isinstance(frames, (bytes, bytearray)):
        return [bytes(frames)]
    return list(frames or ())


def add_action(role, frames, label, dur=2.0, fps=FPS, move=None):
    """新建一个动作。frames 是一组 PNG 字节（1 张就是静态图）。

    动作 key 由服务端发号（c1、c2…），不用用户输入：中文名做文件名在
    Windows/Linux/网络传输三处都要额外处理，而 key 只做标识，没必要。

    **同一个身份里名字不能重复**：名字是用来配对「跟着做」的键，一个人有
    两个「比耶」就不知道该演哪一个。跨身份重名是必须允许的 —— 正是靠它，
    我做比耶、对方有比耶时才能各比各的。内置动作的名字也不许占：那会让
    「我做打招呼」有两个意思。
    """
    frames = _norm_frames(frames)
    _, err = check_frames(frames)
    if err:
        return None, err
    label = (label or '').strip()[:20]
    if not label:
        return None, '动作名不能为空'
    want = norm_label(label)
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = 2.0
    dur = min(8.0, max(0.6, dur))
    fps = clamp_fps(fps)
    move = clamp_move(move)
    if move:
        # 飞行动作太短会像瞬移，给一个下限。
        dur = max(dur, MOVE_MIN_DUR)
    if not valid_name(role):
        return None, '身份不合法'

    with _lock:
        reg = load_registry()
        for item in (reg.get('actions') or {}).values():
            if item.get('role') == role and norm_label(item.get('label')) == want:
                return None, '你已经有「%s」了，换个名字' % (
                    item.get('label') or label)
        for item in ACTIONS.values():
            if norm_label(item.get('label')) == want:
                return None, '「%s」是内置动作的名字，换一个吧' % label
        if len(reg.get('actions') or {}) >= MAX_ACTIONS:
            return None, '自定义动作最多 %d 个，先删几个再传' % MAX_ACTIONS
        seq = int(reg.get('seq') or 0) + 1
        key = 'c%d' % seq
        spec = {
            'label': label,
            'role': role,
            'pose': key,
            'dur': round(dur, 2),
            'hint': '自定义动作（%s）' % label,
            'custom': True,
            'frames': len(frames),
            'fps': fps,
            # 镜像由「同名动作」承担：别人演的是他自己那份，不是这张图。
            'solo': True,
        }
        if move:
            spec['move'] = move
        _write_frames(role, key, frames)
        reg['seq'] = seq
        reg.setdefault('actions', {})[key] = spec
        save_registry(reg)
    return dict(spec, key=key), ''


def replace_pose(role, pose, frames, fps=None, move=None, set_move=False,
                 dur=None, set_dur=False, label=None, set_label=False):
    """换掉某个已有姿势的图，动作表不动。

    自定义动作允许多帧；内置姿势只允许一张 —— 内置的时长和语义都是写死的，
    多帧动画只属于用户自己新建的动作。

    `set_move=True` 才动移动配置：传 None 是「改回静止」，不传是「保持原样」。
    `set_dur=True` 才改时长（内置姿势忽略，它们的时长是语义的一部分）。
    `set_label=True` 才改名字（只对自定义动作有效）。名字同时是「跟着做」的
    配对键，所以校验规则和新建完全一样：不能和自己已有的重名，也不能占
    内置动作的名字；跨身份重名仍然允许。
    """
    frames = _norm_frames(frames)
    _, err = check_frames(frames)
    if err:
        return None, err
    if not valid_name(role) or not valid_name(pose):
        return None, '参数不合法'
    new_label = None
    with _lock:
        reg = load_registry()
        item = None
        item_key = None
        for key, spec in (reg.get('actions') or {}).items():
            if spec.get('pose') == pose or key == pose:
                item, item_key = spec, key
                break
        if item is None and len(frames) > 1:
            return None, '内置姿势只能放一张图'
        if set_label and item is not None and label is not None:
            new_label = str(label).strip()[:20]
            if not new_label:
                return None, '动作名不能为空'
            if norm_label(new_label) == norm_label(item.get('label')):
                new_label = None        # 没改，别白写一次注册表
            else:
                want = norm_label(new_label)
                for k, other in (reg.get('actions') or {}).items():
                    if k == item_key or other.get('role') != item.get('role'):
                        continue
                    if norm_label(other.get('label')) == want:
                        return None, '你已经有「%s」了，换个名字' % (
                            other.get('label') or new_label)
                for builtin in ACTIONS.values():
                    if norm_label(builtin.get('label')) == want:
                        return None, '「%s」是内置动作的名字，换一个吧' % new_label
        _write_frames(role, pose, frames)
        if item is not None:
            item['frames'] = len(frames)
            if fps is not None:
                item['fps'] = clamp_fps(fps)
            if set_move:
                mv = clamp_move(move)
                if mv:
                    item['move'] = mv
                    item['dur'] = round(max(float(item.get('dur') or 2.0),
                                            MOVE_MIN_DUR), 2)
                else:
                    item.pop('move', None)
            if set_dur and dur is not None:
                try:
                    d = float(dur)
                except (TypeError, ValueError):
                    d = 2.0
                d = min(8.0, max(0.6, d))
                if item.get('move'):
                    d = max(d, MOVE_MIN_DUR)
                item['dur'] = round(d, 2)
            if new_label:
                item['label'] = new_label
                item['hint'] = '自定义动作（%s）' % new_label
            save_registry(reg)
    return {'role': role, 'pose': pose, 'frames': len(frames),
            'fps': clamp_fps(fps) if fps is not None else None,
            'move': clamp_move(move) if set_move else None,
            'label': new_label,
            'rev': rev(role, pose)}, ''


def remove_action(key):
    """删掉一个自定义动作：注册表条目 + 所有帧一起清。"""
    with _lock:
        reg = load_registry()
        spec = (reg.get('actions') or {}).pop(key, None)
        if spec is None:
            return False
        save_registry(reg)
        role = spec.get('role')
        pose = spec.get('pose') or key
        if role:
            drop_frames(role, pose)
    return True


def restore_pose(role, pose):
    """把被换掉的内置姿势恢复成出厂图（删掉覆盖文件即可）。"""
    if not valid_name(role) or not valid_name(pose):
        return False
    return drop_frames(role, pose)
