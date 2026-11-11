# -*- coding: utf-8 -*-
"""固定服务器地址的自测。

地址写死之后，这套测试的职责变了：不再是「解析各种写法」，
而是守住三条不变量 —— 地址本身合法、客户端确实用它、没有任何后门能改它。

跑法：python tools/test_config.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import app as A          # noqa: E402
from petclub import config as C       # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def ipv4_ok(s):
    parts = str(s).split('.')
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not (0 <= int(p) <= 255):
            return False
    return True


def test_endpoint():
    print('--- 内置服务器 ---')
    check('主机是合法的 IPv4', ipv4_ok(C.SERVER_HOST), C.SERVER_HOST)
    check('端口在 1~65535 之间', 1 <= int(C.SERVER_PORT) <= 65535, str(C.SERVER_PORT))
    check('ENDPOINT 与两个常量一致',
          C.ENDPOINT == (C.SERVER_HOST, C.SERVER_PORT), repr(C.ENDPOINT))
    check('拼出来的显示串正确',
          C.format_endpoint(C.SERVER_HOST, C.SERVER_PORT) ==
          '%s:%d' % (C.SERVER_HOST, C.SERVER_PORT),
          C.format_endpoint(C.SERVER_HOST, C.SERVER_PORT))


def test_no_override():
    """这一节是防回归：以后谁把「填地址」加回来，这里会先红。"""
    print('\n--- 没有可配置的入口 ---')
    for name in ('parse_endpoint', 'resolve_endpoint', 'load_settings',
                 'save_settings', 'settings_path', 'data_dir'):
        check('%s 已经没有了' % name, not hasattr(C, name))
    for name in ('ENV_HOST', 'ENV_PORT', 'DEFAULT_HOST', 'DEFAULT_PORT'):
        check('%s 已经没有了' % name, not hasattr(C, name))

    from petclub import ui
    check('界面上没有服务器输入框', not hasattr(ui, 'ServerDialog'))
    check('界面上没有「改地址」小窗', not hasattr(ui, 'ask_server'))


def test_app_uses_it():
    print('\n--- 客户端确实用它 ---')
    check('App 不传参 = 内置地址',
          A.endpoint() == (C.SERVER_HOST, C.SERVER_PORT), repr(A.endpoint()))
    check('传参能覆盖（只给探针用）',
          A.endpoint('127.0.0.1', 9999) == ('127.0.0.1', 9999))
    check('只传 host 时端口仍是内置的',
          A.endpoint('10.0.0.1') == ('10.0.0.1', C.SERVER_PORT),
          repr(A.endpoint('10.0.0.1')))


def test_assets():
    print('\n--- 素材 ---')
    d = C.assets_dir()
    check('assets 目录存在', os.path.isdir(d), d)
    check('每个角色的素材目录都在',
          all(os.path.isdir(os.path.join(d, 'roles', r)) for r in C.ROLES),
          d)


def test_frozen():
    """打包成 exe 之后素材在哪儿：PyInstaller 把 --add-data 解到 sys._MEIPASS，
    找不到这条路，exe 一启动就会在 load_all 里炸掉。"""
    print('\n--- 打包后的路径 ---')
    import sys
    had = hasattr(sys, '_MEIPASS')
    old = getattr(sys, '_MEIPASS', None)
    sys._MEIPASS = os.path.join('X:', '临时解包')
    try:
        check('打包后 assets 取自解包目录',
              C.assets_dir() == os.path.join('X:', '临时解包', 'assets'),
              C.assets_dir())
        check('打包后 app_dir 是 exe 所在目录',
              C.app_dir() == os.path.dirname(os.path.abspath(sys.executable)),
              C.app_dir())
    finally:
        if had:
            sys._MEIPASS = old
        else:
            del sys._MEIPASS
    check('源码模式下 assets 在项目根',
          C.assets_dir() == os.path.join(C.app_dir(), 'assets'), C.assets_dir())


def main():
    test_endpoint()
    test_no_override()
    test_app_uses_it()
    test_assets()
    test_frozen()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
