# -*- coding: utf-8 -*-
"""打包出来的 exe 的探针：能起来，且第二次双击不起作用。

只验「在源码里测不到」的那部分：
  · 单文件 exe 能不能自己解出 assets（起不来就说明 _MEIPASS 那条路断了）
  · 真的开出了窗口（进程活着但没窗口 = 卡在启动里，同样不算成功）
  · 再启一个 exe，第二个必须自己退出，第一个不受影响

跑法：python tools/probe_exe.py
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, 'dist', 'pet-club.exe')

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def pids_of(name):
    """tasklist 里所有同名进程（单文件 exe 是「父解压 + 子运行」两个进程）。"""
    try:
        out = subprocess.check_output(
            ['tasklist', '/FI', 'IMAGENAME eq %s' % name, '/FO', 'CSV', '/NH'],
            text=True, encoding='mbcs', errors='replace')
    except (OSError, subprocess.CalledProcessError):
        return []
    out = out.strip()
    if not out or out.startswith('信息') or 'No tasks' in out:
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


def window_count(pids):
    """属于这些 PID 的可见顶层窗口数。"""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return len(found)


def tray_window(pids):
    """托盘那个隐藏窗口在不在。

    打包后是 --windowed，没有控制台 —— 托盘起不来只会静默失败（图标不出现，
    没有任何提示）。所以直接去找那个 HWND_MESSAGE 窗口：
    类名是 pet-club-tray-<pid>，用 FindWindowEx 在消息窗口下面翻。
    """
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    for pid in pids:
        hwnd = user32.FindWindowExW(wintypes.HWND(-3), None,
                                    'pet-club-tray-%d' % pid, None)
        if hwnd:
            return hwnd
    return None


def kill_tree(pid):
    subprocess.call(['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_gone(pids, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if not [p for p in pids if p in pids_of('pet-club.exe')]:
            return True
        time.sleep(0.2)
    return False


def main():
    if not os.path.exists(EXE):
        print('没有 dist/pet-club.exe —— 先跑 python tools/build_exe.py')
        return 1
    print('目标 %s（%.1f MB）' % (EXE, os.path.getsize(EXE) / 1048576.0))

    try:
        first = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
        # 单文件 exe 要先把自己解到临时目录，冷启动一两秒是正常的
        time.sleep(6)
        check('exe 起来了（没在加载素材时崩掉）', first.poll() is None,
              '退出码 %r' % (first.poll(),))

        pids = pids_of('pet-club.exe')
        check('进程在跑', bool(pids), repr(pids))
        wins = window_count(pids)
        check('真的开出了窗口（不是卡在启动里）', wins > 0, '窗口数 %d' % wins)
        # 托盘是关掉所有窗口以后唯一入口，打包后它必须也在
        check('托盘图标也挂上了（隐藏窗口建出来了）',
              bool(tray_window(pids)), '找不到 pet-club-tray-* 窗口')

        # 第二次双击：必须自己安静退出，第一个不受影响
        second = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
        try:
            rc = second.wait(timeout=20)
            gone = True
        except subprocess.TimeoutExpired:
            rc, gone = None, False
            kill_tree(second.pid)
        check('第二个 exe 自己退出了（一台电脑只跑一个）', gone,
              '还在跑，PID %d' % second.pid)
        check('退出码是 0（安静退出，不弹错误）', rc == 0, repr(rc))

        alive = pids_of('pet-club.exe')
        check('第一个实例没被第二个顶掉', first.poll() is None and bool(alive),
              repr(alive))
        # 窗口数只做「没有成倍长」的判断：这台机器连着公网服务器，
        # 别人认领/离开时会有角色和自己的气泡冒出来又消失，数字本来就会动。
        # 第二个实例要是真的开了窗口，至少会多一整只角色 + 认领窗，不止 +1。
        later = window_count(alive)
        check('桌面上没有第二个实例的窗口', later <= wins + 1,
              '%d → %d' % (wins, later))
    finally:
        if first.poll() is None:
            kill_tree(first.pid)
            wait_gone([first.pid])
        for pid in pids_of('pet-club.exe'):
            kill_tree(pid)

    leftover = pids_of('pet-club.exe')
    check('探针结束时没留下进程', not leftover, repr(leftover))

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
