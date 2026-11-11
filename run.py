# -*- coding: utf-8 -*-
"""pet-club 启动入口。

启动就直连内置服务器（config.SERVER_HOST），连上后认领身份 ——
没有填地址、没有房间码、没有命令行参数：

    python run.py

一台机器只跑一个：第二个进程会在这里安静退出（见 petclub/single.py）。

想换服务器只能改 `petclub/config.py` 里的 SERVER_HOST / SERVER_PORT 再重新打包。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from petclub import single, update   # noqa: E402


def main():
    # 先抢锁再建窗口：第二个实例连 QApplication 都不该建，更不能闪一下再消失
    if not single.acquire():
        return
    # 上次下好的新版本：这一次启动就换掉它。放在建窗口之前 ——
    # 换的过程要退出进程，先开界面再退出等于让人看见窗口闪一下。
    update.cleanup()
    if update.apply_pending():
        return
    from petclub.app import App

    app = App()          # 必须留个引用：run() 返回后 App 被回收，
    app.run()            # QApplication 跟着析构，解释器退出时会崩


if __name__ == '__main__':
    main()
