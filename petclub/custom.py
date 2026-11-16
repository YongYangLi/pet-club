# -*- coding: utf-8 -*-
"""用户上传的自定义动作与姿势图。

存在 `data/custom/` 下，和 `assets/` 完全分开：

  assets/   出厂素材 —— 进 git、打进 exe、参与 build 指纹、随发布包覆盖
  data/     运行期内容 —— 不进版本库、不进发布包、不影响 build 指纹

按这个分法，用户传一张图不会让全群刷新页面（build 不变），也不会被
下一次发布覆盖掉。这是整套「用户自己上传」能成立的前提。

两样东西：

  registry.json        新建的动作（名字 / 时长 / 属于哪个角色）
  <role>/<pose>.png    某个角色的某张图。两种来源共用一套存储：
                       · 新动作的图（pose 就是动作 key）
                       · 换掉内置动作的图（pose 是内置的姿势名，比如 wave）

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

MAX_BYTES = 2 * 1024 * 1024      # 上传体积上限（base64 解码后）
MAX_W = 200                      # 和现有素材同量级：p1 最宽 158、最高 234
MAX_H = 240

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
        return None, '图片超过 %d MB' % (MAX_BYTES // 1024 // 1024)
    size = png_size(data)
    if size is None:
        return None, '不是有效的 PNG'
    w, h = size
    if w > MAX_W or h > MAX_H:
        return None, '画布 %d×%d 超过上限 %d×%d' % (w, h, MAX_W, MAX_H)
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


def _write_png(role, pose, data):
    _role_dir(role)                 # 第一次上传时这个角色还没有目录
    path = image_path(role, pose)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def drop_png(role, pose):
    try:
        os.remove(image_path(role, pose))
        return True
    except OSError:
        return False


# ---------------- 对外：动作表 / 清单 ----------------

def actions():
    """自定义动作，结构和 actions.ACTIONS 一致（外加 role / custom / solo）。"""
    return dict(load_registry().get('actions') or {})


def order():
    """按创建顺序。注册表存的是 dict，Python 3.7+ 保序，够用。"""
    return tuple(load_registry().get('actions') or {})


def rev(role, pose):
    """内容版本号。用 mtime+size 派生：文件被覆盖 mtime 必变。

    不用内容 hash 是因为每张图几百 KB，每次清单请求都读一遍不划算；
    mtime 精度到纳秒，手工替换文件也能立刻被认出来。
    """
    try:
        st = os.stat(image_path(role, pose))
    except OSError:
        return ''
    key = '%d:%d' % (st.st_mtime_ns, st.st_size)
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]


def manifest():
    """客户端同步素材用的清单：有哪些自定义动作、每个角色有哪些覆盖图。"""
    reg = load_registry()
    poses = {}
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
                continue
            r = rev(role, pose)
            if r:
                m[pose] = r
        if m:
            poses[role] = m
    return {
        'actions': dict(reg.get('actions') or {}),
        'poses': poses,
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


def add_action(role, png, label, dur=2.0):
    """新建一个动作。返回 (spec, 错误串)。

    动作 key 由服务端发号（c1、c2…），不用用户输入：中文名做文件名在
    Windows/Linux/网络传输三处都要额外处理，而 key 只做标识，没必要。

    **同一个身份里名字不能重复**：名字是用来配对「跟着做」的键，一个人有
    两个「比耶」就不知道该演哪一个。跨身份重名是必须允许的 —— 正是靠它，
    我做比耶、对方有比耶时才能各比各的。内置动作的名字也不许占：那会让
    「我做打招呼」有两个意思。
    """
    size, err = check_png(png)
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
            # 镜像由「同名动作」承担：别人演的是他自己那份，不是这张图。
            'solo': True,
        }
        _write_png(role, key, png)
        reg['seq'] = seq
        reg.setdefault('actions', {})[key] = spec
        save_registry(reg)
    return dict(spec, key=key), ''


def replace_pose(role, pose, png):
    """换掉某个已有姿势的图，动作表不动。返回 (spec, 错误串)。"""
    _, err = check_png(png)
    if err:
        return None, err
    if not valid_name(role) or not valid_name(pose):
        return None, '参数不合法'
    with _lock:
        _write_png(role, pose, png)
    return {'role': role, 'pose': pose, 'rev': rev(role, pose)}, ''


def remove_action(key):
    """删掉一个自定义动作：注册表条目 + 图片一起清。"""
    with _lock:
        reg = load_registry()
        spec = (reg.get('actions') or {}).pop(key, None)
        if spec is None:
            return False
        save_registry(reg)
        role = spec.get('role')
        pose = spec.get('pose') or key
        if role:
            drop_png(role, pose)
    return True


def restore_pose(role, pose):
    """把被换掉的内置姿势恢复成出厂图（删掉覆盖文件即可）。"""
    if not valid_name(role) or not valid_name(pose):
        return False
    return drop_png(role, pose)
