# -*- coding: utf-8 -*-
"""单实例锁的探针：一台机器只许跑一个。

真起两个 GUI 太慢，而且「桌面上出现两套影子角色」这件事肉眼才看得出来，
探针里没法断言。这里只验那把锁本身 —— 第二个进程拿不到，不弹窗、不报错、
安静退出，全在 `run.py` 里那一次 `single.acquire()` 决定。

跑法：python tools/probe_single.py
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


# 子进程：抢锁 → 报一句结果 → 抢到的人停一会儿（够父进程起第二个），
# 没抢到的立刻退出 —— 这正是 run.py 的语义，也让「退得快」这件事可测。
CHILD = (
    'import sys, time;'
    'sys.path.insert(0, %r);'
    'from petclub import single;'
    'ok = single.acquire();'
    'print("WIN" if ok else "LOSE", flush=True);'
    'time.sleep(6.0 if ok else 0.0)'
) % ROOT


def run_child(wait_line=True, timeout=20):
    p = subprocess.Popen([sys.executable, '-c', CHILD],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, cwd=ROOT)
    if not wait_line:
        return p, None
    line = p.stdout.readline().strip()
    return p, line


def main():
    hold, first = run_child()
    check('第一个进程拿到锁', first == 'WIN', repr(first))

    second, line2 = run_child()
    check('第二个进程拿不到锁（一台电脑只能起一个）', line2 == 'LOSE', repr(line2))
    # 拿不到锁必须立刻退，不能挂着 —— 否则用户会以为程序卡住了
    t0 = time.time()
    try:
        second.wait(timeout=10)
        fast = time.time() - t0 < 2.0
    except subprocess.TimeoutExpired:
        second.kill()
        fast = False
    check('第二个进程几乎立刻就退出了', fast, '%.2fs' % (time.time() - t0))
    check('第二个进程很快就自己退出了', fast)
    check('退出码是 0（安静退出，不算错误）', second.returncode == 0,
          repr(second.returncode))

    check('第一个进程全程没被打扰', hold.poll() is None)
    hold.kill()
    hold.wait()

    time.sleep(0.3)
    third, line3 = run_child()
    check('前一个退出后还能再起一个（锁不会粘住）', line3 == 'WIN', repr(line3))
    third.kill()
    third.wait()

    # 回归红线：入口必须真的把锁挂上，否则 sys.exit 之前就建窗口了
    with open(os.path.join(ROOT, 'run.py'), encoding='utf-8') as f:
        src = f.read()
    check('run.py 启动时先抢锁', 'single.acquire()' in src)
    check('抢锁在建 App 之前',
          src.index('single.acquire()') < src.index('App()'), '顺序反了')

    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
