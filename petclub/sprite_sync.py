# -*- coding: utf-8 -*-
"""把用户上传的姿势图同步到本地缓存。

分工是这样切的：

  远程（/api/sprites + /sprites/…）   只负责**分发**：有新图就下下来
  本地缓存                            负责**渲染**：角色开窗只读磁盘，绝不等网络

所以断网、服务器挂、清单请求超时，都不影响角色出现 —— 只是看不到新图，
用的是上次同步下来的那一份（再没有就是 exe 里出厂的那份）。

下载完的文件放在用户目录（config.sprite_cache_dir），和 exe 里的 assets
互不干扰：出厂素材永远在，缓存只是覆盖在它上面的一层。
"""

import json
import os
import struct
import urllib.request

TIMEOUT = 6.0
MAX_BYTES = 3 * 1024 * 1024      # 单张上限。服务端限制 200×240，这里是兜底
MAX_SIDE = 512                   # 像素上限：挡住「几 KB 的 PNG 解出几 GB」的炸弹


def manifest_url(host, http_port):
    return 'http://%s:%d/api/sprites' % (host, int(http_port))


def image_url(host, http_port, role, pose, rev):
    return 'http://%s:%d/sprites/%s/%s.png?v=%s' % (
        host, int(http_port), role, pose, rev)


def _get(url, timeout):
    req = urllib.request.Request(url, headers={'Cache-Control': 'no-store'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def png_size(data):
    """只看 IHDR。宽高就是解码器要分配的画布，卡住它等于卡住解压炸弹。"""
    if len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    if data[12:16] != b'IHDR':
        return None
    w, h = struct.unpack('>II', data[16:24])
    if not w or not h:
        return None
    return w, h


def _revs_path(cache_dir):
    return os.path.join(cache_dir, 'revs.json')


def load_revs(cache_dir):
    try:
        with open(_revs_path(cache_dir), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_revs(cache_dir, revs):
    path = _revs_path(cache_dir)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(revs, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _prune(cache_dir, poses):
    """删掉远程已经不存在的图。返回删掉的张数。

    必须删：某个姿势被「恢复默认」之后，远程清单里就没有它了，
    缓存还留着的话桌面端会一直显示那张自定义图，怎么刷都不回去。
    删掉的张数要回传：只有「下载」和「删除」都算素材变了，调用方才知道
    该不该重新读图（恢复默认时一张都不会下载）。
    """
    removed = 0
    for role in os.listdir(cache_dir):
        d = os.path.join(cache_dir, role)
        if not os.path.isdir(d):
            continue
        want = set((poses.get(role) or {}).keys())
        for name in os.listdir(d):
            if name.endswith('.png') and name[:-4] not in want:
                try:
                    os.remove(os.path.join(d, name))
                    removed += 1
                except OSError:
                    pass
    return removed


def sync(host, http_port, cache_dir, timeout=TIMEOUT):
    """拉清单 → 只下变的图 → 删掉远程已删除的图。

    返回 (manifest, 下载张数, 删除张数)。失败抛异常，调用方决定怎么兜底 ——
    这里不做「静默失败」：上层要能区分「没更新」和「连不上」。
    """
    raw = _get(manifest_url(host, http_port), timeout)
    manifest = json.loads(raw.decode('utf-8'))
    if not isinstance(manifest, dict):
        raise ValueError('清单格式不对')

    poses = manifest.get('poses') or {}
    revs = load_revs(cache_dir)
    got = 0

    for role, per in poses.items():
        if not isinstance(per, dict):
            continue
        for pose, rev in per.items():
            if not isinstance(rev, str) or not rev:
                continue
            target = os.path.join(cache_dir, role, pose + '.png')
            if revs.get(role, {}).get(pose) == rev and os.path.exists(target):
                continue
            try:
                data = _get(image_url(host, http_port, role, pose, rev), timeout)
            except OSError:
                continue                    # 单张失败不影响其他张
            if len(data) > MAX_BYTES:
                continue
            size = png_size(data)
            if size is None or size[0] > MAX_SIDE or size[1] > MAX_SIDE:
                continue
            try:
                _write(target, data)
            except OSError:
                continue
            revs.setdefault(role, {})[pose] = rev
            got += 1

    pruned = _prune(cache_dir, poses)
    save_revs(cache_dir, revs)
    return manifest, got, pruned
