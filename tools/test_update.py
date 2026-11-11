# -*- coding: utf-8 -*-
"""自动更新测试：版本号比较、清单解析、下载校验、换文件脚本。

    py tools/test_update.py

不碰真的 COS，也不碰真的 exe：本机起一个 http.server 当「桶」，
用 monkeypatch 把 update.exe_path 指到临时目录，其余全走真代码。
"""

import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub import config, update                     # noqa: E402

TMP = os.path.join(ROOT, '.tmp', 'update-test')
_fail = []
_n = [0]


def check(name, ok, detail=''):
    _n[0] += 1
    print('[%s] %s%s' % ('ok  ' if ok else 'FAIL', name,
                         ('  ' + str(detail)) if detail else ''))
    if not ok:
        _fail.append(name)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory):
    """在临时目录上起一个静态服务器，返回 (base_url, 关掉它的函数)。"""
    handler = lambda *a, **kw: Quiet(*a, directory=directory, **kw)  # noqa: E731
    srv = socketserver.TCPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return 'http://127.0.0.1:%d' % srv.server_address[1], srv.shutdown


def main():
    os.makedirs(TMP, exist_ok=True)
    for name in os.listdir(TMP):
        p = os.path.join(TMP, name)
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass

    print('--- 版本号 ---')
    check('1.2.3 → (1,2,3)', update.parse('1.2.3') == (1, 2, 3))
    check('缺位补 0', update.parse('1.2') == (1, 2, 0))
    check('空串当 0', update.parse('') == (0, 0, 0))
    check('乱七八糟也不崩', update.parse('v1.beta.3x') == (1, 0, 3))
    check('新版本更大', update.is_newer('1.1.1', '1.1.0'))
    check('补丁位变化也算新', update.is_newer('1.0.1', '1.0.0'))
    check('同版本不算新', not update.is_newer('1.1.0', '1.1.0'))
    check('老版本不算新', not update.is_newer('1.0.9', '1.1.0'))
    check('1.10 比 1.9 新（不是字符串比较）', update.is_newer('1.10.0', '1.9.0'))
    check('空清单版本不算新', not update.is_newer('', '1.0.0'))

    print('\n--- 源码模式下不更新 ---')
    check('没打包就不启用', not update.enabled())
    check('源码模式拿不到 exe 路径', update.exe_path() == '')
    ok, why = update.apply()
    check('源码模式 apply 直接拒绝', not ok, why)
    check('源码模式也不认暂存文件', update.apply_pending() is False)

    print('\n--- 清单解析 ---')
    payload = b'{"version": "9.9.9", "url": "x.exe", "size": 3, "sha256": "ab"}'
    with open(os.path.join(TMP, 'latest.json'), 'wb') as f:
        f.write(payload)
    with open(os.path.join(TMP, 'bad.json'), 'wb') as f:
        f.write(b'<html>404</html>')
    with open(os.path.join(TMP, 'noUrl.json'), 'wb') as f:
        f.write(b'{"version": "1.2.0"}')

    base, stop = serve(TMP)
    try:
        info, err = update.manifest(base + '/latest.json')
        check('正常清单能解析', err == '' and info['version'] == '9.9.9', err)
        check('相对地址补成绝对地址',
              info['url'] == base + '/x.exe', info['url'])

        info2, err2 = update.check('1.1.0', base + '/latest.json')
        check('有新版时 check 返回 info', info2 is not None, err2)
        info3, err3 = update.check('10.0.0', base + '/latest.json')
        check('比它新时 check 返回 None', info3 is None and err3 == '')

        _i, err = update.manifest(base + '/bad.json')
        check('JSON 坏了会说清楚', 'JSON' in err, err)
        _i, err = update.manifest(base + '/noUrl.json')
        check('缺 url 会拒', 'url' in err, err)
        _i, err = update.manifest(base + '/none.json')
        check('404 不会抛异常', err != '', err)
    finally:
        stop()

    print('\n--- 下载与校验 ---')
    # 假装自己是个打包好的 exe：新版本放在临时目录里
    exe_dir = os.path.join(TMP, 'app')
    os.makedirs(exe_dir, exist_ok=True)
    fake_exe = os.path.join(exe_dir, 'pet-club.exe')
    with open(fake_exe, 'wb') as f:
        f.write(b'OLD')
    update.exe_path = lambda: fake_exe             # monkeypatch
    update.config = config

    blob = os.urandom(4096)
    blob_path = os.path.join(TMP, 'pet-club-9.9.9.exe')
    with open(blob_path, 'wb') as f:
        f.write(blob)
    digest = hashlib.sha256(blob).hexdigest()

    base, stop = serve(TMP)
    try:
        good = {'url': base + '/pet-club-9.9.9.exe', 'version': '9.9.9',
                'size': len(blob), 'sha256': digest, 'note': '测试'}
        pct = []
        path, err = update.download(good, progress=pct.append)
        check('下载成功', err == '' and os.path.exists(path), err)
        check('落地文件名是 .new', path.endswith('.new'), os.path.basename(path))
        check('内容一模一样',
              open(path, 'rb').read() == blob)
        check('有进度回调', bool(pct) and max(pct) <= 1.0, pct[-1:] or '')
        check('没有留下 .part', not os.path.exists(path + '.part'))

        ok, ver, _ = update.staged_valid()
        check('暂存状态是「已就绪」', ok and ver == '9.9.9', (ok, ver))

        # 坏 sha：必须删掉半成品，否则下次启动会拿它换上去
        bad = dict(good, sha256='0' * 64)
        os.remove(path)
        _p, err = update.download(bad)
        check('sha 不对会报错', err != '' and 'sha' not in err, err)
        check('坏文件当场删掉', not os.path.exists(path))

        # 大小不对
        short = dict(good, size=len(blob) + 100)
        _p, err = update.download(short)
        check('大小不对会报错', '不完整' in err, err)
        check('半成品也不留', not os.path.exists(path))
    finally:
        stop()

    print('\n--- 比我还旧的暂存不当真 ---')
    # 手工把新 exe 拷进一个还躺着旧 .new 的目录时，不能一启动就把自己降级回去
    with open(path, 'wb') as f:
        f.write(b'STALE')
    with open(update.meta_path(), 'w', encoding='utf-8') as f:
        json.dump({'version': '0.0.1'}, f)
    ok, ver, _p = update.staged_valid()
    check('旧版本 .new 不会被当成待安装', not ok, (ok, ver))
    update.cleanup()
    check('cleanup 顺手删掉旧的 .new', not os.path.exists(path))

    # 换成功之后 .new 被 move 走了，它的附注（.new.json）不会跟着走，
    # 会一直躺在 exe 旁边。下次启动要能认出来这是空壳并清掉。
    with open(update.meta_path(), 'w', encoding='utf-8') as f:
        json.dump({'version': '9.9.9'}, f)
    update.cleanup()
    check('孤立的 .new.json 会被清掉', not os.path.exists(update.meta_path()))

    print('\n--- 换文件脚本 ---')
    script = update._script(r'C:\a b\pet-club.exe', r'C:\a b\pet-club.exe.new',
                            relaunch=True)
    check('脚本带了新文件路径', 'pet-club.exe.new' in script)
    check('脚本带了 exe 路径', 'C:\\a b\\pet-club.exe' in script)
    check('用 move 覆盖', 'move /y' in script)
    check('换完会重新拉起', 'start ""' in script)
    check('会自己删自己', 'del "%~f0"' in script)
    check('批处理的 for 用双百分号', '%%i' in script)
    check('循环有上限（不会永远转）', 'for /L %%i in (1,1,' in script)
    check('不重启时不写 start', 'start ""' not in
          update._script(r'C:\p\a.exe', r'C:\p\a.exe.new', relaunch=False))
    check('换之前先摘只读位', 'attrib -r' in script)
    check('等老进程是按 PID 认的', 'PID eq %d' % 4242 in
          update._script(r'C:\p\a.exe', r'C:\p\a.exe.new', True, pid=4242))
    check('换完顺手删掉 .new.json', 'del "%NEW%.json"' in script)
    check('拉起前先等一拍（躲开落地扫描）',
          script.index('ping') < script.index('start ""'))
    check('拉起后会确认进程真的在',
          'set "EXENAME=pet-club.exe"' in script
          and 'IMAGENAME eq %EXENAME%' in script)
    check('没起来会再拉一次（两次 start）', script.count('start ""') == 2)

    print('\n--- 拉起新版本时环境必须干净 ---')
    # 单文件 exe 的 bootloader 解包完会留下这三个变量，告诉子进程「已经解好了」。
    # 更新脚本也是它的子进程，cmd / start 一路继承；新 exe 看到变量齐全就
    # 跳过解包，去老进程那个已经删掉的临时目录找 python3xx.dll →
    # 「Failed to load Python DLL ... 找不到指定的模块」。文件其实换好了，
    # 双击也正常，所以特别像「版本坏了」。tools/probe_relaunch.py 验的是真链路。
    for key in update.PYI_ENV_VARS:
        check('脚本清掉 %s' % key, ('set "%s="' % key) in script)
    check('清变量发生在 start 之前',
          script.index('set "%s="' % update.PYI_ENV_VARS[0])
          < script.index('start ""'))

    saved = {k: os.environ.get(k) for k in
             update.PYI_ENV_VARS + ('_MEIPASS2', '_MEI12345')}
    try:
        os.environ['_PYI_APPLICATION_HOME_DIR'] = r'C:\Temp\_MEI999'
        os.environ['_PYI_ARCHIVE_FILE'] = r'C:\a\pet-club.exe'
        os.environ['_PYI_PARENT_PROCESS_LEVEL'] = '1'
        os.environ['_MEIPASS2'] = r'C:\Temp\_MEI999'
        os.environ['_MEI12345'] = r'C:\Temp\_MEI12345'
        os.environ['PET_CLUB_KEEP'] = 'yes'
        env = update._clean_env()
        left = [k for k in env if k in update.PYI_ENV_VARS
                or k == '_MEIPASS2' or (k.startswith('_MEI') and k[4:].isdigit())]
        check('_clean_env 摘得掉启动器变量', not left, repr(left))
        check('_clean_env 不动别的变量', env.get('PET_CLUB_KEEP') == 'yes')
        check('_clean_env 是副本，不改自己的环境',
              '_PYI_ARCHIVE_FILE' in os.environ)

        # apply() 起脚本时也得带上干净环境 —— 脚本里那三句是兜底，
        # 这一步才是「默认就对」
        import types
        captured = {}

        class FakePopen(object):
            def __init__(self, argv, **kw):
                captured.update(kw)
                captured['argv'] = argv

        class FakeSubprocess(object):
            Popen = FakePopen
            STARTUPINFO = subprocess.STARTUPINFO
            STARTF_USESHOWWINDOW = subprocess.STARTF_USESHOWWINDOW
            DEVNULL = subprocess.DEVNULL

        real = (update.subprocess, update.staged_valid, update.exe_path)
        update.subprocess = FakeSubprocess
        update.staged_valid = lambda: (True, '9.9.9', r'C:\a\pet-club.exe.new')
        update.exe_path = lambda: r'C:\a\pet-club.exe'
        try:
            ok, why = update.apply(relaunch=True)
        finally:
            update.subprocess, update.staged_valid, update.exe_path = real
        got = captured.get('env') or {}
        check('apply 能安排上', ok, why)
        check('apply 传给脚本的环境里没有启动器变量',
              not [k for k in got if k in update.PYI_ENV_VARS])
        check('apply 传了 env（不是继承默认值）', 'env' in captured)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop('PET_CLUB_KEEP', None)

    print('\n--- 真跑一遍换文件（只读目标）---')
    # 桌面上那个 exe 就是只读的：从浏览器/压缩包出来的文件常带只读位，
    # 而 move 覆盖只读文件会「拒绝访问」。这是「下好了却装不上」的根因，
    # 所以这里不测字符串，直接把脚本跑起来看文件有没有换掉。
    swap_dir = os.path.join(TMP, 'swap')
    os.makedirs(swap_dir, exist_ok=True)
    real = os.path.join(swap_dir, 'app.exe')
    fresh = real + '.new'
    with open(real, 'wb') as f:
        f.write(b'OLD')
    os.chmod(real, os.stat(real).st_mode & ~0o200)          # 摘掉写位 = 只读
    check('目标真的是只读', not os.access(real, os.W_OK), oct(os.stat(real).st_mode))
    with open(fresh, 'wb') as f:
        f.write(b'NEW')

    log = os.path.join(swap_dir, 'update.log')
    bat = os.path.join(swap_dir, 'run-update.bat')
    with open(bat, 'w', encoding='mbcs', errors='replace') as f:
        f.write(update._script(real, fresh, relaunch=False, pid=999999, log=log))
    subprocess.call(['cmd', '/c', bat], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    body = open(real, 'rb').read()
    check('只读目标也能换上', body == b'NEW', body)
    check('.new 用完就没了', not os.path.exists(fresh))
    text = open(log, encoding='mbcs', errors='replace').read() if os.path.exists(log) else ''
    check('换了什么都记进日志', '开始更新' in text and '换好了' in text, text.strip())

    update._clear_readonly(real)
    check('_clear_readonly 摘得掉只读位', os.access(real, os.W_OK))

    print('\n--- 配置 ---')
    check('版本号是 三段式', len(config.VERSION.split('.')) == 3, config.VERSION)
    check('没配地址时 enabled 是 False', not update.enabled())
    check('超时是正数', config.UPDATE_TIMEOUT > 0, config.UPDATE_TIMEOUT)
    check('间隔不短于 10 分钟', config.UPDATE_EVERY >= 600,
          config.UPDATE_EVERY)

    print('\n通过 %d 项，失败 %d 项' % (_n[0] - len(_fail), len(_fail)))
    return 1 if _fail else 0


if __name__ == '__main__':
    sys.exit(main())
