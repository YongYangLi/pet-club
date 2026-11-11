# -*- coding: utf-8 -*-
"""发一个版本到腾讯云 COS：打 exe → 算 sha256 → 写 latest.json → 打印上传命令。

    py tools/make_release.py --base https://pet-club-1300000000.cos.ap-beijing.myqcloud.com/pet-club \
        --note "新增：发图片、表情包、文件"

它会做三件事：
  1. （默认）调 tools/build_exe.py 出一个新的 dist/pet-club.exe
  2. 算大小和 sha256，把 dist/latest.json 写出来
     —— 客户端只认这个清单：版本号、地址、大小、sha256 四样对上才换
  3. 打印上传命令（本机装了 coscli 就直接传，没装就只打印）

**桶里只有一个 exe，文件名固定 pet-club.exe，不带版本号。**
版本号只活在 latest.json 里：地址始终不变，覆盖同一个对象，改清单里的
version 就等于发版。带版本号的文件名会逼着每发一版都换一次地址，桶里越堆越多，
而「哪个才是当前版本」这件事本来清单里就写着，不必在文件名上再说一遍。

代价：回退不再是「把旧文件名改回去」，而是「用旧代码重打一次再覆盖」。
所以留一个纪律 —— 上传前先看下面打印的大小/sha256，和刚打出来的 dist/pet-club.exe
对得上再传；加 --no-build 时尤其要确认 dist 里躺的是这一版的，不是上一版的。

为什么清单和 exe 分开传、而且先传 exe：
  清单是「开关」。先传 exe，全世界都还看不到；最后传清单，所有客户端**同时**
  看到新版本。反过来做的话，中间那几分钟里客户端读到的版本号还是旧的（无害），
  但如果清单先指到新版本而 exe 没换，下载下来 sha256 对不上，客户端会报
  「校验不一致，文件可能下坏了」—— 明明什么都没坏，却像坏了。

上传（顺序不能反）：
  coscli cp dist/pet-club.exe cos://<桶>/pet-club/pet-club.exe
  coscli cp dist/latest.json  cos://<桶>/pet-club/latest.json
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub.config import VERSION          # noqa: E402

EXE = os.path.join(ROOT, 'dist', 'pet-club.exe')
MANIFEST = os.path.join(ROOT, 'dist', 'latest.json')

# 上传到桶里的名字：固定，不带版本号。版本号在清单里，不在文件名里。
NAME = 'pet-club.exe'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description='打包并生成本次发版的 latest.json')
    ap.add_argument('--base', required=True,
                    help='COS 上放 exe 和清单的目录，例：'
                         'https://xxx.cos.ap-beijing.myqcloud.com/pet-club')
    ap.add_argument('--note', default='', help='这次更新了什么（会显示在客户端）')
    ap.add_argument('--no-build', action='store_true', help='不重新打包，用现有的 exe')
    args = ap.parse_args(argv)

    if not args.no_build:
        print('=== 打包 ===')
        rc = subprocess.call([sys.executable, os.path.join(HERE, 'build_exe.py')],
                             cwd=ROOT)
        if rc != 0:
            print('打包失败，退出码 %d' % rc)
            return rc

    if not os.path.exists(EXE):
        print('没有 %s，先打包' % EXE)
        return 1

    size = os.path.getsize(EXE)
    digest = sha256(EXE)
    base = args.base.rstrip('/')

    info = {
        'version': VERSION,
        'url': '%s/%s' % (base, NAME),
        'size': size,
        'sha256': digest,
        'note': args.note,
        'date': time.strftime('%Y-%m-%d %H:%M'),
    }
    with open(MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print('\n=== 本次发版 ===')
    print('版本   %s' % VERSION)
    print('大小   %.1f MB' % (size / 1048576.0))
    print('sha256 %s' % digest)
    print('打包于 %s' % time.strftime(
        '%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(EXE))))
    print('exe    %s' % EXE)
    print('清单   %s' % MANIFEST)

    print('\n=== 上传（先 exe，最后清单）===')
    # 桶名取「:// 到 .cos. 之间」那一段；路径取「.com/ 之后」那一段。
    # 别在桶名里再拼一次 cos:// —— 打印出来的命令是要能直接粘进终端的。
    bucket = base.split('://', 1)[-1].split('.cos.')[0]
    prefix = '/' + base.split('.com/', 1)[1] if '.com/' in base else ''
    print('coscli cp "%s" "cos://%s%s/%s"' % (EXE, bucket, prefix, NAME))
    print('coscli cp "%s" "cos://%s%s/latest.json"' % (MANIFEST, bucket, prefix))

    # COS 的域名里已经带桶名了；路径里再写一遍，几乎一定是复制粘贴错了。
    # 真出过：--base .../dev-cos-1259275319/pet-club → 客户端拉清单 404，
    # 但控制台上传的路径看着一切正常，非常难猜。
    first = prefix.strip('/').split('/')[0] if prefix.strip('/') else ''
    if first and first == bucket:
        print('\n⚠️  --base 的路径里又写了一遍桶名（%s）。' % bucket)
        print('    COS 的域名已经包含桶名，路径不该重复 —— 客户端会 404。')
        print('    正确形状：https://%s.cos.<地域>.myqcloud.com/pet-club' % bucket)

    print('\n=== 核对这两个地址（浏览器能直接打开才算配对）===')
    print('清单  %s/latest.json' % base)
    print('exe   %s' % info['url'])

    print('\n没装 coscli 就用控制台上传。注意两点：')
    print('  1. 桶（或这两个对象）要开「公有读」，客户端是普通 GET，不带签名')
    print('  2. 清单一定要最后传 —— 它是开关，先传会让客户端去拉还不存在的 exe')
    return 0


if __name__ == '__main__':
    sys.exit(main())
