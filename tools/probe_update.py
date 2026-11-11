# -*- coding: utf-8 -*-
"""自动更新的端到端探针：真的跑一遍 apply()。

    py tools/probe_update.py

要验的三件在单元测试里验不了的事：

  1. **换完之后屏幕上看不见任何窗口**。
     以前 `DETACHED_PROCESS|CREATE_NO_WINDOW` 一起传，后者被 Windows 忽略，
     cmd 自己分配一个控制台 → 屏幕角落弹出一个黑窗在里面 ping 127.0.0.1。
     这个必须由**没有控制台的父进程**来跑才有意义，所以本探针把自己挂到
     pythonw.exe 下面再干活（用 python.exe 跑的话 cmd 会继承父进程的控制台，
     根本不会新建窗口，测了等于没测）。
  2. **老进程还活着的时候不动手**：脚本在等 PID 消失，文件必须原封不动。
  3. **老进程一退出，文件就被换掉**，.new 消失，日志里留下记录。

全程只在 .tmp/update-e2e/ 里折腾，不碰 dist/ 里那个真 exe。
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub import update                        # noqa: E402

TMP = os.path.join(ROOT, '.tmp', 'update-e2e')
RESULT = os.path.join(TMP, 'result.json')
OLD = b'OLD-EXE-BYTES'
NEW = b'NEW-EXE-BYTES' * 512

user32 = ctypes.WinDLL('user32', use_last_error=True)

# 只管控制台窗口。桌面上的窗口本来就不停在变（输入法状态栏、Edge 的输入浮层
# 都会在我们盯着的那三秒里冒出来），全量比对只会误报。
CONSOLE_CLASSES = ('ConsoleWindowClass',          # conhost 自己的窗口
                   'CASCADIA_HOSTING_WINDOW_CLASS',  # Windows Terminal
                   'PseudoConsoleWindow')         # Terminal 的伪控制台

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + str(detail)) if detail else ''), flush=True)


def visible_windows():
    """所有可见控制台窗口的 (hwnd, class, title)。"""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value not in CONSOLE_CLASSES:
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        out.append((hwnd, cls.value, title.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


# ---------------- 子阶段：在 pythonw 下跑，跑完自己退出 ----------------

def child():
    exe = os.path.join(TMP, 'pet-club.exe')
    with open(exe, 'wb') as f:
        f.write(OLD)
    new = exe + '.new'
    with open(new, 'wb') as f:
        f.write(NEW)
    with open(exe + '.new.json', 'w', encoding='utf-8') as f:
        json.dump({'version': '9.9.9', 'size': len(NEW)}, f)

    update.exe_path = lambda: exe                 # monkeypatch：只作用在这份拷贝上
    before = {w[0] for w in visible_windows()}

    ok, msg = update.apply(relaunch=False)
    appeared = []
    swapped_early = None
    deadline = time.time() + 3.0
    while time.time() < deadline:                 # 盯着这三秒，任何窗口都算问题
        for w in visible_windows():
            if w[0] not in before and w not in appeared:
                appeared.append(w)
        swapped_early = open(exe, 'rb').read() != OLD
        if swapped_early:
            break
        time.sleep(0.15)

    with open(RESULT, 'w', encoding='utf-8') as f:
        json.dump({'ok': ok, 'msg': msg, 'appeared': appeared,
                   'swapped_early': swapped_early}, f)
    # 这里退出 = 老进程消失，脚本才可能开始 move


# ---------------- 父阶段 ----------------

def run_child():
    """用 pythonw（无控制台）跑子阶段。"""
    pw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.exists(pw):
        print('找不到 pythonw.exe，无法模拟无控制台环境：%s' % pw)
        return False
    p = subprocess.Popen([pw, os.path.abspath(__file__), '--child'],
                         cwd=ROOT)
    p.wait(timeout=90)
    return os.path.exists(RESULT)


def main():
    if '--child' in sys.argv:
        child()
        return 0

    if os.path.isdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)

    if not run_child():
        print('子阶段没跑出结果')
        return 1

    with open(RESULT, encoding='utf-8') as f:
        res = json.load(f)
    check('apply() 安排上了', res['ok'], res['msg'])
    check('没弹出任何窗口', not res['appeared'], res['appeared'])
    check('老进程还在时不动文件', res['swapped_early'] is False,
          res['swapped_early'])

    # 子进程退出后脚本才会动手；给它几秒
    exe = os.path.join(TMP, 'pet-club.exe')
    end = time.time() + 20
    body = b''
    while time.time() < end:
        body = open(exe, 'rb').read()
        if body == NEW:
            break
        time.sleep(0.2)
    check('进程退出后文件被换掉', body == NEW, '%d 字节' % len(body))
    check('.new 已经用掉', not os.path.exists(exe + '.new'))

    log = update.log_path()
    text = ''
    if os.path.exists(log):
        try:
            text = open(log, encoding='mbcs', errors='replace').read()
        except OSError:
            text = ''
    check('日志里记了「换好了」', '换好了' in text, text.strip().splitlines()[-1:] or '')

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
