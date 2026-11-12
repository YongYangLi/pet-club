# -*- coding: utf-8 -*-
"""打包出来的 exe 的探针：能起来，且第二次双击不起作用。

只验「在源码里测不到」的那部分：
  · 单文件 exe 能不能自己解出 assets 和 Qt 插件（起不来就说明 _MEIPASS 那条路断了）
  · Qt 的运行时真的被冻进去了（模块列表里有 Qt6Widgets / qwindows）
  · 再启一个 exe，第二个必须自己退出，第一个不受影响

⚠️ 这里**不数窗口**：客户端连不上服务器时本来就没有窗口（不认领就没角色，
认领窗要等服务端回话）。窗口该长什么样是 probe_live.py 的事。

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

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE = ctypes.c_void_p(-1).value

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''),
          flush=True)


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


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD),
                ('th32ModuleID', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('GlblcntUsage', wintypes.DWORD),
                ('ProccntUsage', wintypes.DWORD),
                ('modBaseAddr', ctypes.c_void_p),
                ('modBaseSize', wintypes.DWORD),
                ('hModule', wintypes.HMODULE),
                ('szModule', ctypes.c_char * 256),
                ('szExePath', ctypes.c_char * 260)]


def modules(pid):
    """这个进程加载了哪些 DLL（要管理员权限才能看别的位数/别的用户的进程）。"""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    snap = k32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if not snap or snap == INVALID_HANDLE:
        return []
    out = []
    try:
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(MODULEENTRY32)
        if k32.Module32First(snap, ctypes.byref(me)):
            while True:
                out.append(me.szModule.decode('mbcs', 'replace'))
                if not k32.Module32Next(snap, ctypes.byref(me)):
                    break
    finally:
        k32.CloseHandle(snap)
    return out


def kill_tree(pid):
    subprocess.call(['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    if not os.path.exists(EXE):
        print('没有 dist/pet-club.exe —— 先跑 python tools/build_app.py')
        return 1
    print('目标 %s（%.1f MB）' % (EXE, os.path.getsize(EXE) / 1048576.0))

    first = None
    try:
        first = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
        # 单文件 exe 要先把自己解开（这次带上了 Qt，一百多兆），冷启动几秒很正常
        end = time.time() + 30
        pids = []
        while time.time() < end:
            time.sleep(0.5)
            pids = pids_of('pet-club.exe')
            if pids:
                time.sleep(5)          # 再给一会儿，让它能崩的话崩掉
                break
        check('exe 起来了（没在加载素材/Qt 时崩掉）', first.poll() is None,
              '退出码 %r / 启动的 PID %d / 当前进程 %r'
              % (first.poll(), first.pid, pids))
        pids = pids_of('pet-club.exe')
        check('进程在跑', bool(pids), repr(pids))

        mods = []
        for pid in pids:
            mods += modules(pid)
        low = [m.lower() for m in mods]
        check('Qt 的运行时被冻进去了（Qt6Core/Qt6Widgets）',
              any('qt6core' in m for m in low) and any('qt6widgets' in m
                                                       for m in low),
              mods[:8])
        check('Qt 的平台插件也加载了（qwindows.dll）',
              any(m.startswith('qwindows') for m in low),
              [m for m in mods if 'q' in m.lower()][:8])
        check('Python 运行时是打包进去的（不是靠机器上装的）',
              any(m.startswith('python3') for m in low),
              [m for m in mods if m.startswith('python')])
        check('没有拖进来 tkinter', not any('tk8' in m or 'tcl' in m
                                       for m in low), mods[:8])

        # 第二次双击：必须自己安静退出，第一个不受影响
        second = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
        try:
            rc = second.wait(timeout=40)
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
    finally:
        if first is not None and first.poll() is None:
            kill_tree(first.pid)
        for pid in pids_of('pet-club.exe'):
            kill_tree(pid)
        time.sleep(1)

    leftover = pids_of('pet-club.exe')
    check('探针结束时没留下进程', not leftover, repr(leftover))

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
