# -*- coding: utf-8 -*-
"""认领口令管理（在**服务器本机**跑）。

    python tools/claim_codes.py --init
    python tools/claim_codes.py --status
    python tools/claim_codes.py --reset p3      # 忘口令：生成一条新的
    python tools/claim_codes.py --disable p3    # 取消该身份的口令保护
    python tools/claim_codes.py --disable all
    python tools/claim_codes.py --file /path/claim.json --init

--init 只需要在第一次启用时跑一次：一次生成六个身份的口令，只在控制台
打印这一次；文件里只写 PBKDF2 盐 + 哈希，事后来看也拿不回明文。口令请
私下发给对应的人，用户登录后可以在客户端里改成自己的。

日常发版、重启服务端都不需要再跑。只有「口令忘了 / 想换一条」才需要
--reset（生成新口令并让旧设备令牌全部失效）。--disable 是应急回退：
删掉口令后该身份回到先占先得，慎用。
"""
import argparse
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_FILE = os.path.join(ROOT, 'data', 'claim.json')

ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # 去掉 I/O/0/1


def new_code():
    """12 位、分三段：好念也好抄，约 59 bit。"""
    chars = [secrets.choice(ALPHABET) for _ in range(12)]
    return '%s-%s-%s' % (''.join(chars[:4]), ''.join(chars[4:8]),
                         ''.join(chars[8:]))


def new_codes(roles):
    """一次生成一组互不相同的口令 —— 两条一样就分不清是谁了。"""
    codes = {}
    for role in roles:
        code = new_code()
        while code in codes.values():
            code = new_code()
        codes[role] = code
    return codes


def main(argv=None):
    ap = argparse.ArgumentParser(description='认领口令管理')
    ap.add_argument('--file', default=DEFAULT_FILE,
                    help='口令文件路径（默认 data/claim.json）')
    ap.add_argument('--init', action='store_true', help='生成六个身份的口令')
    ap.add_argument('--status', action='store_true', help='看哪些身份已设口令')
    ap.add_argument('--reset', default='', metavar='ROLE',
                    help='忘口令：为某个身份（p1~p6）或 all 生成新口令')
    ap.add_argument('--disable', default='', metavar='ROLE',
                    help='取消口令保护（p1~p6 或 all），恢复到先占先得')
    ap.add_argument('--force', action='store_true',
                    help='--init 时覆盖已有口令文件')
    args = ap.parse_args(argv)

    os.environ['PETCLUB_CLAIM_FILE'] = os.path.abspath(args.file)
    sys.path.insert(0, ROOT)
    import server                                       # noqa: E402

    if args.init:
        if os.path.exists(args.file) and not args.force:
            print('已经有口令文件了：%s' % args.file)
            print('要重新生成请加 --force（旧口令和已登录设备都会失效）。')
            return 1
        codes = new_codes(server.ROLES)
        server.auth_init(codes)
        print('已生成认领口令（只显示这一次，请私下发给对应的人）：\n')
        for role in server.ROLES:
            print('  %s  %-6s  %s' % (role, server.ROLE_NAMES[role],
                                      codes[role]))
        print('\n文件：%s' % args.file)
        print('用户登录后可以在客户端里改成自己的口令；忘了就 --reset 重发。')
        return 0

    if args.disable:
        if args.disable == 'all':
            server.auth_reset(None)
            print('已取消全部口令，所有身份回到先占先得（应急模式）。')
        elif args.disable in server.ROLES:
            server.auth_reset(args.disable)
            print('已取消 %s %s 的口令保护。'
                  % (args.disable, server.ROLE_NAMES.get(args.disable, '')))
        else:
            print('不认识的身份证：%s' % args.disable)
            return 1
        return 0

    if args.reset:
        if args.reset == 'all':
            codes = new_codes(server.ROLES)
            server.auth_init(codes)
            print('已为六个身份生成新口令（旧口令和所有设备令牌全部失效）：\n')
            for role in server.ROLES:
                print('  %s  %-6s  %s' % (role, server.ROLE_NAMES[role],
                                          codes[role]))
            print('\n请私下发给对应的人；用户登录后可以自己再改。')
        elif args.reset in server.ROLES:
            code = new_code()
            while not server.auth_set_code(args.reset, code):
                code = new_code()
            print('已为 %s %s 生成新口令（旧口令和设备令牌已失效）：\n\n  %s\n'
                  % (args.reset, server.ROLE_NAMES.get(args.reset, ''), code))
        else:
            print('不认识的身份证：%s' % args.reset)
            return 1
        return 0

    # 默认 / --status
    roles = server._auth_load().get('roles') or {}
    if not roles:
        print('还没启用口令（%s 不存在或为空）。' % args.file)
        return 0
    print('已启用认领口令：')
    for role in server.ROLES:
        item = roles.get(role) or {}
        tokens = len(item.get('tokens') or [])
        print('  %s  %-6s  %s  已登录设备 %d 台' % (
            role, server.ROLE_NAMES[role],
            '有口令' if item.get('hash') else '未设置', tokens))
    return 0


if __name__ == '__main__':
    sys.exit(main())
