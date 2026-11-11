# -*- coding: utf-8 -*-
"""更新脚本「拉起新版本」那一步的探针。

验的是真实链路，不是单元测试里的替身：
  拷一份 exe 当老版本 → 再拷一份当刚下好的 .new → 用 update._script() 生成真脚本
  → **带着「老进程的 PyInstaller 环境变量」** 跑 `cmd /c 脚本`
  → 看新起来的进程是不是真活着，还是只弹了一个

      Failed to load Python DLL '...\\_MEI...\\python315.dll'
      LoadLibrary: 找不到指定的模块。

为什么非要带脏环境跑：单文件 exe 的 bootloader 解包之后，会给子进程设三个变量
（_PYI_APPLICATION_HOME_DIR / _PYI_ARCHIVE_FILE / _PYI_PARENT_PROCESS_LEVEL）。
更新脚本是我们自己进程的子进程，cmd 和 start 一路继承下来；新 exe 一看这三个变量
齐全，就以为「我已经被解包过了」，于是不去解包，直接去 _PYI_APPLICATION_HOME_DIR
里加载 pythonXXX.dll —— 那是**老进程**的临时目录，早跟着老进程一起删了。
症状就是：报错弹窗，但桌面的 exe 其实已经换成新的，双击又能正常启动。

跑法：python tools/probe_relaunch.py
"""

import ctypes
import glob
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub import config, update          # noqa: E402

SRC = os.path.join(ROOT, 'dist', 'pet-club.exe')
WORK = os.path.join(os.environ.get('TEMP') or ROOT, 'pet-club-relaunch-probe')

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[fail]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''),
          flush=True)


def pids_of(name):
    try:
        out = subprocess.check_output(
            ['tasklist', '/FI', 'IMAGENAME eq %s' % name, '/FO', 'CSV', '/NH'],
            text=True, encoding='mbcs', errors='replace').strip()
    except (OSError, subprocess.CalledProcessError):
        return []
    if not out or 'No tasks' in out or out.startswith('信息'):
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def dialog_texts():
    """所有可见对话框里的文字（PyInstaller 的报错就是 #32770 + 一个「确定」）。"""
    u = ctypes.WinDLL('user32')
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        if not u.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_string_buffer(128)
        u.GetClassNameA(hwnd, cls, 128)
        if cls.value != b'#32770':
            return True
        texts = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def inner(child, _m):
            buf = ctypes.create_unicode_buffer(2048)
            u.GetWindowTextW(child, buf, 2048)
            if buf.value.strip():
                texts.append(buf.value)
            return True

        u.EnumChildWindows(hwnd, inner, 0)
        found.append(' / '.join(texts))
        return True

    u.EnumWindows(cb, 0)
    return found


def kill_all():
    for pid in pids_of('pet-club.exe'):
        subprocess.call(['taskkill', '/F', '/T', '/PID', str(pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stale_env():
    """模拟「我是从老进程里被拉起来的」：三个变量齐全，但目录是空的。

    真出事时那个目录是老的 _MEI，里面连 pythonXXX.dll 都没有 —— 空目录
    正好等价，而且不用去猜老进程的目录名叫什么。
    """
    home = os.path.join(os.environ.get('TEMP') or ROOT, '_MEI0000beef0')
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home)
    env = dict(os.environ)
    env['_PYI_APPLICATION_HOME_DIR'] = home
    env['_PYI_ARCHIVE_FILE'] = os.path.join(WORK, 'pet-club.exe')
    env['_PYI_PARENT_PROCESS_LEVEL'] = '1'
    return env, home


def main():
    if not os.path.exists(SRC):
        print('没有 dist/pet-club.exe —— 先跑 python tools/build_exe.py')
        return 1
    kill_all()
    print('目标 %s（%.1f MB，%s）'
          % (SRC, os.path.getsize(SRC) / 1048576.0, config.VERSION))

    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    exe = os.path.join(WORK, 'pet-club.exe')
    new = exe + update.NEW_SUFFIX
    shutil.copy2(SRC, exe)
    shutil.copy2(SRC, new)

    script = update._script(exe, new, True, pid=None, log=None)
    bat = os.path.join(WORK, 'update.bat')
    with open(bat, 'w', encoding='mbcs', errors='replace') as f:
        f.write(script)

    # 脚本自己该做的事：换文件之前把这三个变量清掉。清不掉的话，下面这一步
    # 一定翻车 —— 这条断言是「病因」本身，比后面的黑盒判断更早给出信号。
    body = [ln.strip().lower() for ln in script.splitlines()]
    check('脚本在 start 之前清掉了 _PYI_APPLICATION_HOME_DIR',
          any(ln.startswith('set "_pyi_application_home_dir="') for ln in body),
          '没有这句，新进程会去用老进程的临时目录')
    check('脚本在 start 之前清掉了 _PYI_ARCHIVE_FILE',
          any(ln.startswith('set "_pyi_archive_file="') for ln in body))
    check('脚本在 start 之前清掉了 _PYI_PARENT_PROCESS_LEVEL',
          any(ln.startswith('set "_pyi_parent_process_level="') for ln in body))

    env, home = stale_env()
    before = set(glob.glob(os.path.join(os.environ['TEMP'], '_MEI*')))
    proc = subprocess.Popen(['cmd', '/c', bat], cwd=WORK, env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(12)

    try:
        check('操作跑完，.new 已被换走', not os.path.exists(new),
              '脚本没执行或者 move 失败')
        alive = pids_of('pet-club.exe')
        check('新 exe 的进程起来了', bool(alive), repr(alive))
        errs = [t for t in dialog_texts() if 'Python DLL' in t]
        check('没有弹「Failed to load Python DLL」', not errs, ' / '.join(errs))
        fresh = set(glob.glob(os.path.join(os.environ['TEMP'], '_MEI*'))) - before
        check('新进程自己解包了一个新的临时目录', bool(fresh),
              '没有新 _MEI = 它又去用别人的解包目录了')
    finally:
        kill_all()
        shutil.rmtree(home, ignore_errors=True)
        if proc.poll() is None:
            subprocess.call(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
