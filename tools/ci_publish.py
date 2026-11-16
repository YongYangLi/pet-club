# -*- coding: utf-8 -*-
"""把桌面产物和更新清单覆盖上传到对象存储（CI / 本机都能跑）。

    python tools/ci_publish.py --platform win
    python tools/ci_publish.py --platform mac
    python tools/ci_publish.py --platform win --dry-run

地址只有一个来源：`petclub/config.py` 里的 `UPDATE_URL`。
它长这样：

    https://<桶名>.cos.<地域>.myqcloud.com/<目录>/latest.json

脚本从里面拆出桶名、地域、目录，把产物按**固定文件名**覆盖上去
（pet-club.exe / pet-club-mac.zip），同时写一份清单。桶里永远只有
最新的那一份，不会越堆越多。前提：桶没有开版本控制。

CI 里只需要两个 secret：COS_SECRET_ID、COS_SECRET_KEY。
本机跑则先装 `pip install cos-python-sdk-v5` 并设好这两个环境变量。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub import config                                # noqa: E402

# 平台 → (本地产物, 桶里的键, 清单名)
TARGETS = {
    'win': ('dist/pet-club.exe', 'pet-club.exe', 'latest.json'),
    'mac': ('dist/pet-club-mac.zip', 'pet-club-mac.zip', 'latest-mac.json'),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def base_and_target(url):
    """从 UPDATE_URL 拆出 (发布目录, 桶, 地域)。"""
    u = urlparse(url or '')
    host = (u.hostname or '').lower()
    if not host.endswith('.myqcloud.com') or '.cos.' not in host:
        raise SystemExit(
            'UPDATE_URL 不是标准 COS 地址：%r\n'
            '期望形状：https://<桶名>.cos.<地域>.myqcloud.com/<目录>/latest.json'
            % url)
    bucket, rest = host.split('.cos.', 1)
    region = rest[:-len('.myqcloud.com')]
    prefix = (u.path or '').strip('/')
    prefix = prefix.rsplit('/', 1)[0] if '/' in prefix else ''
    base = '%s://%s%s' % (u.scheme or 'https', host,
                          ('/' + prefix) if prefix else '')
    return base, bucket, region, prefix


def release_note():
    note = (os.environ.get('RELEASE_NOTE') or '').strip()
    if note:
        return note.splitlines()[0].strip()
    try:
        out = subprocess.check_output(
            ['git', 'log', '-1', '--pretty=%s'], cwd=ROOT)
        return out.decode('utf-8', 'replace').strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


def main(argv=None):
    ap = argparse.ArgumentParser(description='覆盖发布桌面产物到 COS')
    ap.add_argument('--platform', choices=['auto', 'win', 'mac'],
                    default='auto')
    ap.add_argument('--dry-run', action='store_true',
                    help='只生成清单并打印，不真的上传')
    ap.add_argument('--note', default='', help='更新说明（默认取提交标题）')
    args = ap.parse_args(argv)

    target = args.platform
    if target == 'auto':
        target = 'win' if sys.platform == 'win32' else (
            'mac' if sys.platform == 'darwin' else '')
    if target not in TARGETS:
        print('不支持的系统：%s' % sys.platform)
        return 1

    local, remote, manifest_name = TARGETS[target]
    artifact = os.path.join(ROOT, local)
    if not os.path.exists(artifact):
        print('没有产物：%s（先跑 tools/build_app.py）' % artifact)
        return 1

    base, bucket, region, prefix = base_and_target(config.UPDATE_URL)
    size = os.path.getsize(artifact)
    digest = sha256(artifact)
    note = args.note or release_note()
    info = {
        'version': config.VERSION,
        'url': '%s/%s' % (base, remote),
        'size': size,
        'sha256': digest,
        'note': note,
        'date': time.strftime('%Y-%m-%d %H:%M'),
    }

    print('平台   %s' % target)
    print('版本   %s' % config.VERSION)
    print('大小   %.1f MB' % (size / 1048576.0))
    print('sha256 %s' % digest)
    print('桶     %s（%s）' % (bucket, region))
    print('目录   %s' % (prefix or '/'))
    print('产物   %s/%s' % (base, remote))
    print('清单   %s/%s' % (base, manifest_name))
    print('说明   %s' % (note or '（空）'))

    if args.dry_run:
        print('\n--dry-run：没有上传。')
        return 0

    sid = os.environ.get('COS_SECRET_ID') or ''
    skey = os.environ.get('COS_SECRET_KEY') or ''
    if not sid or not skey:
        print('\n缺少 COS_SECRET_ID / COS_SECRET_KEY，无法上传。')
        return 1

    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        print('\n先安装依赖：pip install cos-python-sdk-v5')
        return 1

    # 海外 runner 直连上海桶可能很慢。给每个请求加超时，并允许打开 COS
    # 全球加速（控制台开启后设 COS_RAPID=1，会走 cosrapid.<region> 域名）。
    rapid = (os.environ.get('COS_RAPID') or '').strip() in ('1', 'true', 'yes')
    client = CosS3Client(CosConfig(
        Region=region, SecretId=sid, SecretKey=skey, Scheme='https',
        Timeout=60, EnableRapidDomain=rapid))
    if rapid:
        print('已启用 COS 全球加速域名')

    def key(name):
        return ('%s/%s' % (prefix, name)) if prefix else name

    last = [-1]

    def progress(done, total):
        pct = int(done * 100 / max(1, total))
        if pct >= last[0] + 10 or pct >= 100:
            last[0] = pct
            print('  上传 %s：%d%%（%.1f / %.1f MB）' % (
                remote, pct, done / 1048576.0, total / 1048576.0), flush=True)

    print('\n开始上传 %s（%.1f MB）…' % (remote, size / 1048576.0), flush=True)
    try:
        client.upload_file(
            Bucket=bucket, Key=key(remote), LocalFilePath=artifact,
            PartSize=8, MAXThread=4, EnableMD5=False,
            progress_callback=progress,
            ContentType=('application/zip' if target == 'mac'
                         else 'application/octet-stream'),
            CacheControl='no-cache')
    except Exception as e:                       # 网络 / 权限 / 超时都要说清楚
        print('\n上传失败：%s' % e)
        print('可以先用 --dry-run 看清单，或在网络更好的机器上重跑本脚本。')
        return 1

    body = (json.dumps(info, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    print('开始上传清单 %s…' % manifest_name, flush=True)
    try:
        client.put_object(
            Bucket=bucket, Key=key(manifest_name), Body=body,
            ContentType='application/json; charset=utf-8',
            CacheControl='no-store')
    except Exception as e:
        print('\n清单上传失败：%s' % e)
        return 1

    print('\n已覆盖上传（桶里只保留最新一份）：')
    print('  %s/%s' % (base, remote))
    print('  %s/%s' % (base, manifest_name))
    return 0


if __name__ == '__main__':
    sys.exit(main())
