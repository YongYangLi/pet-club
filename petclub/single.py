# -*- coding: utf-8 -*-
"""一台机器只跑一个实例。

桌宠是常驻后台的小东西，用户很容易连点两下图标，或者开机自启之后又手点一次。
跑起两个进程的后果不是「多一个窗口」这么简单：两个进程会抢同一个身份、互相把
对方顶下线、桌面上还会出现两套影子角色 —— 所有「别人看到我」的语义都乱掉。
所以第二个进来直接安静退出，不弹窗、不报错、什么都不做。

Windows 用命名互斥体（CreateMutexW）：进程活着内核对象就在，进程一死系统自动
回收。不需要退出时清任何东西，也就不存在「上次没退干净导致再也起不来」的锁文件。
非 Windows（开发机上偶尔跑一下）退化成一个加锁的临时文件。
"""

import os

NAME = 'pet-club-single-instance'

# 句柄必须活着：CreateMutexW 返回的 HANDLE 一旦被 CloseHandle（或被 GC 收掉）
# 互斥体就没了，锁等于没加。挂在这里等于挂到进程退出为止。
_holder = []


def acquire(name=NAME):
    """拿到「这台机器上还没有实例」的所有权。拿不到返回 False，调用方直接退出。"""
    if os.name == 'nt':
        return _acquire_win(name)
    return _acquire_posix(name)


def _acquire_win(name):
    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 183
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    k32.CreateMutexW.restype = wintypes.HANDLE

    # Local\ 前缀 = 当前登录会话。不用 Global\：那个需要额外权限，
    # 而「同一个人开了两个桌宠」本来就是这里要防的情况。
    h = k32.CreateMutexW(None, False, 'Local\\' + name)
    if not h:
        # 连互斥体都建不出来（极端受限环境）：宁可让程序跑起来，也别挡在门外
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(h)
        return False
    _holder.append(h)
    return True


def _acquire_posix(name):
    try:
        import fcntl
    except ImportError:             # 既不是 Windows 又没有 fcntl：放弃保护
        return True
    import tempfile

    p = os.path.join(tempfile.gettempdir(), name + '.lock')
    f = open(p, 'a+')
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _holder.append(f)               # 进程退出时文件描述符关闭 = 自动解锁
    return True
