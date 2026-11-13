# -*- coding: utf-8 -*-
"""发布网页版前的一步：校验版本号、生成 web/build.json。

build 号 = 版本号 + 内容指纹（web/ + assets/）的短 hash。这样：
  · 内容没变，build 就没变 —— 重复发布不会让所有人的页面白刷一次
  · 只改了一行 JS，build 也会变 —— 前端能立刻发现该刷新了
  · 只换了一张图，build 同样会变 —— 素材也走同一个指纹，否则改了图
    前端不会提示刷新，用户还会拿缓存里的旧图看一小时

assets/ 里有两类东西：roles、stickers 是运行必需的；_gen/ 是做图过程的
预览图（.gitignore 里也忽略了）。_gen 不参与指纹，也不进发布包 —— 它一变
就让大家刷页面，没有道理。

版本号散在三处（__init__.py / config.py / server.py），这里一次性校验。
对不齐就直接退出：宁可发不出去，也别把「网页说 1.7.0、服务端说 1.6.0」
这种东西放到线上 —— 那种不一致查起来最费时间。

用法：
    python tools/make_web_build.py                本地发布（发布网页版.bat 会调它）
    python tools/make_web_build.py --tag v1.6.0   CI 里用：顺带核对 tag 和版本号
"""

import hashlib
import json
import os
import re
import sys
import time
from argparse import ArgumentParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, 'web')
ASSETS = os.path.join(ROOT, 'assets')
BUILD_FILE = os.path.join(WEB, 'build.json')

# 参与指纹 + 进发布包的目录。顺序固定，不然同一份代码算出来的 build 会变。
TRACKED = (('web', WEB), ('assets', ASSETS))

# 相对路径以这些开头的一律跳过：build.json 是自己写的（自指），
# _gen/ 是做图预览（不进包，也不该让大家刷页面）。
SKIP_FILES = ('web/build.json',)
SKIP_DIRS = ('assets/_gen/',)

SOURCES = (
    ('petclub/__init__.py', r"^__version__\s*=\s*'([^']+)'"),
    ('petclub/config.py', r"^VERSION\s*=\s*'([^']+)'"),
    ('server.py', r"^SERVER_VERSION\s*=\s*'([^']+)'"),
)


def grab(rel, pattern):
    path = os.path.join(ROOT, rel)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            m = re.search(pattern, f.read(), re.M)
    except OSError as e:
        print('读不到 %s：%s' % (rel, e))
        return None
    return m.group(1) if m else None


def check_versions():
    found = {}
    for rel, pattern in SOURCES:
        found[rel] = grab(rel, pattern)
    bad = [k for k, v in found.items() if not v]
    if bad:
        print('这些文件里找不到版本号：%s' % '、'.join(bad))
        return None
    values = set(found.values())
    if len(values) != 1:
        print('版本号对不上，先统一再发：')
        for rel, ver in found.items():
            print('  %-24s %s' % (rel, ver))
        return None
    return values.pop()


def _skipped(rel):
    if rel in SKIP_FILES:
        return True
    return any(rel.startswith(d) for d in SKIP_DIRS)


def content_digest():
    """算 web/ + assets/ 的内容指纹。

    路径带上了顶层目录名（web/js/main.js、assets/roles/p1/idle.png），
    所以两边同名文件不会互相抵消。
    """
    h = hashlib.sha1()
    count = 0
    for top, base in TRACKED:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                rel = top + '/' + os.path.relpath(path, base).replace(os.sep, '/')
                if _skipped(rel):
                    continue
                h.update(rel.encode('utf-8'))
                try:
                    with open(path, 'rb') as f:
                        h.update(f.read())
                except OSError:
                    continue
                count += 1
    return h.hexdigest()[:8], count


def main():
    ap = ArgumentParser()
    ap.add_argument('--tag', default='',
                    help='git tag（CI 传 github.ref_name）。v 开头的会和版本号核对，'
                         '对不上就退出 —— 打错 tag 发出去，线上就是「网页 1.7、服务端 1.6」。')
    args = ap.parse_args()

    version = check_versions()
    if not version:
        return 1

    tag = args.tag.strip()
    # 手动跑 workflow 时 ref 是分支名（master），不是版本 tag，跳过核对
    if re.match(r'^v\d', tag):
        want = tag[1:]
        if want != version:
            print('tag 是 %s，代码里是 %s，对不上' % (tag, version))
            print('要么补一处版本号，要么换个 tag 再发。')
            return 1
        print('tag 核对通过：%s' % tag)

    digest, count = content_digest()
    build = '%s-%s' % (version, digest)
    data = {
        'version': version,
        'build': build,
        'date': time.strftime('%Y-%m-%d %H:%M'),
    }
    with open(BUILD_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')

    print('版本 %s' % version)
    print('build %s（指纹覆盖 %d 个文件）' % (build, count))
    return 0


if __name__ == '__main__':
    sys.exit(main())
