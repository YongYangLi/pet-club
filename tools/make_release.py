# -*- coding: utf-8 -*-
"""发一个版本到腾讯云 COS：打包 → 算 sha256 → 写清单 → 打印上传命令。

    py tools/make_release.py --base https://pet-club-1300000000.cos.ap-beijing.myqcloud.com/pet-club \
        --note "新增：发图片、表情包、文件"

它会做三件事：
  1. （默认）调 tools/build_app.py 出本平台的产物
  2. 算大小和 sha256，把清单写出来
     —— 客户端只认这个清单：版本号、地址、大小、sha256 四样对上才换
  3. 打印上传命令（本机装了 coscli 就直接传，没装就只打印）

**两个平台各发各的，互不影响。** 产物名、清单名都按平台区分：

    Windows   dist/pet-club.exe       → 桶里 pet-club.exe       + latest.json
    macOS     dist/pet-club-mac.zip   → 桶里 pet-club-mac.zip   + latest-mac.json

为什么要分成两份清单：桶里那份 latest.json 是 Windows 客户端的「开关」，
Mac 客户端读的是 latest-mac.json，两边版本可以不同步地发（mac 上没有自更新，
清单只用来记录当前版本）。如果共用一份，谁后发就会把对方的版本号覆盖掉。

**桶里只放一个 exe / 一个 zip，文件名不带版本号。**
版本号只活在清单里：地址始终不变，覆盖同一个对象，改清单里的 version 就等于发版。
带版本号的文件名会逼着每发一版都换一次地址，桶里越堆越多，而「哪个才是当前版本」
这件事本来清单里就写着，不必在文件名上再说一遍。

代价：回退不再是「把旧文件名改回去」，而是「用旧代码重打一次再覆盖」。
所以留一个纪律 —— 上传前先看下面打印的大小/sha256，和刚打出来的产物对得上再传；
加 --no-build 时尤其要确认 dist 里躺的是这一版的，不是上一版的。

为什么清单和产物分开传、而且先传产物：
  清单是「开关」。先传产物，全世界都还看不到；最后传清单，所有客户端**同时**
  看到新版本。反过来做的话，中间那几分钟里客户端读到的版本号还是旧的（无害），
  但如果清单先指到新版本而文件没换，下载下来 sha256 对不上，客户端会报
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

DIST = os.path.join(ROOT, 'dist')

# 平台 → (本地产物, 桶里的名字, 清单名)
TARGETS = {
    'win': ('pet-club.exe', 'pet-club.exe', 'latest.json'),
    'mac': ('pet-club-mac.zip', 'pet-club-mac.zip', 'latest-mac.json'),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build(target):
    cmd = [sys.executable, os.path.join(HERE, 'build_app.py'),
           '--target', target]
    if target == 'mac':
        cmd.append('--zip')             # 发版要的是能分发的 zip
    print('=== 打包（%s）===' % target)
    return subprocess.call(cmd, cwd=ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser(description='打包并生成本次发版的清单')
    ap.add_argument('--base', required=True,
                    help='COS 上放产物和清单的目录，例：'
                         'https://xxx.cos.ap-beijing.myqcloud.com/pet-club')
    ap.add_argument('--note', default='', help='这次更新了什么（会显示在客户端）')
    ap.add_argument('--target', choices=['auto', 'win', 'mac'], default='auto',
                    help='默认 auto：按当前系统')
    ap.add_argument('--no-build', action='store_true', help='不重新打包，用现有的产物')
    args = ap.parse_args(argv)

    target = args.target
    if target == 'auto':
        target = ('win' if sys.platform == 'win32' else
                  'mac' if sys.platform == 'darwin' else '')
    if target not in TARGETS:
        print('不支持的系统：%s' % sys.platform)
        return 1

    local_name, remote_name, manifest_name = TARGETS[target]
    artifact = os.path.join(DIST, local_name)
    manifest = os.path.join(DIST, manifest_name)

    if not args.no_build:
        rc = build(target)
        if rc != 0:
            print('打包失败，退出码 %d' % rc)
            return rc

    if not os.path.exists(artifact):
        print('没有 %s，先打包（去掉 --no-build）' % artifact)
        return 1

    size = os.path.getsize(artifact)
    digest = sha256(artifact)
    base = args.base.rstrip('/')

    info = {
        'version': VERSION,
        'url': '%s/%s' % (base, remote_name),
        'size': size,
        'sha256': digest,
        'note': args.note,
        'date': time.strftime('%Y-%m-%d %H:%M'),
    }
    with open(manifest, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print('\n=== 本次发版（%s）===' % target)
    print('版本   %s' % VERSION)
    print('大小   %.1f MB' % (size / 1048576.0))
    print('sha256 %s' % digest)
    print('打包于 %s' % time.strftime(
        '%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(artifact))))
    print('产物   %s' % artifact)
    print('清单   %s' % manifest)

    print('\n=== 上传（先产物，最后清单）===')
    # 桶名取「:// 到 .cos. 之间」那一段；路径取「.com/ 之后」那一段。
    # 别在桶名里再拼一次 cos:// —— 打印出来的命令是要能直接粘进终端的。
    bucket = base.split('://', 1)[-1].split('.cos.')[0]
    prefix = '/' + base.split('.com/', 1)[1] if '.com/' in base else ''
    print('coscli cp "%s" "cos://%s%s/%s"' % (artifact, bucket, prefix, remote_name))
    print('coscli cp "%s" "cos://%s%s/%s"' % (manifest, bucket, prefix, manifest_name))

    # COS 的域名里已经带桶名了；路径里再写一遍，几乎一定是复制粘贴错了。
    # 真出过：--base .../dev-cos-1259275319/pet-club → 客户端拉清单 404，
    # 但控制台上传的路径看着一切正常，非常难猜。
    first = prefix.strip('/').split('/')[0] if prefix.strip('/') else ''
    if first and first == bucket:
        print('\n⚠️  --base 的路径里又写了一遍桶名（%s）。' % bucket)
        print('    COS 的域名已经包含桶名，路径不该重复 —— 客户端会 404。')
        print('    正确形状：https://%s.cos.<地域>.myqcloud.com/pet-club' % bucket)

    print('\n=== 核对这两个地址（浏览器能直接打开才算配对）===')
    print('清单  %s/%s' % (base, manifest_name))
    print('产物  %s' % info['url'])

    print('\n没装 coscli 就用控制台上传。注意两点：')
    print('  1. 桶（或这两个对象）要开「公有读」，客户端是普通 GET，不带签名')
    print('  2. 清单一定要最后传 —— 它是开关，先传会让客户端去拉还不存在的文件')
    if target == 'mac':
        print('  3. mac 客户端没有自更新（见 host.can_self_update），'
              '这份清单只用来记录版本')
    return 0


if __name__ == '__main__':
    sys.exit(main())
