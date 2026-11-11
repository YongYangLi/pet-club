# -*- coding: utf-8 -*-
"""指令表、台词、冷却的自测（纯逻辑，不开窗口）。

跑法：python tools/test_actions.py

这套保证三件事：加新指令时不会漏台词、「到达范围」标得对、冷却算得对。
菜单里那层显示逻辑在 probe_live 里测。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import actions as A          # noqa: E402
from petclub.config import ROLES          # noqa: E402
from petclub.cooldown import Cooldown     # noqa: E402
from petclub.sprites import POSES         # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def test_cooldown():
    print('--- 冷却 ---')
    cd = Cooldown()
    check('新实例所有指令都能用', cd.ready('a', 100.0))

    cd.mark('a', 100.0, 2.0)
    check('用完立刻不可用', not cd.ready('a', 100.0))
    check('剩余时间算得对', abs(cd.left('a', 100.0) - 2.0) < 1e-9, cd.left('a', 100.0))
    check('过了一秒还剩一秒', abs(cd.left('a', 101.0) - 1.0) < 1e-9)
    check('到了时间就恢复', cd.ready('a', 102.0))
    check('剩余时间不会是负数', cd.left('a', 999.0) == 0.0, cd.left('a', 999.0))

    check('冷却按 key 各算各的', cd.ready('b', 100.0))

    cd.mark('c', 100.0, 0)
    check('跨度 0 = 不冷却', cd.ready('c', 100.0))

    cd.reset()
    check('reset 之后全部可用', cd.ready('a', 100.0))


def test_catalog():
    print('\n--- 指令表 ---')
    check('顺序表和指令表一一对应',
          set(A.ORDER) == set(A.ACTIONS) and len(A.ORDER) == len(A.ACTIONS),
          repr(set(A.ORDER) ^ set(A.ACTIONS)))
    check('每条指令都有 label / pose / dur / hint / scope',
          all(all(spec.get(k) for k in ('label', 'pose', 'dur', 'hint', 'scope'))
              for spec in A.ACTIONS.values()),
          repr([k for k, v in A.ACTIONS.items()
                if not all(v.get(f) for f in
                           ('label', 'pose', 'dur', 'hint', 'scope'))]))
    check('菜单里不重名',
          len({s['label'] for s in A.ACTIONS.values()}) == len(A.ACTIONS))

    scopes = {k: s['scope'] for k, s in A.ACTIONS.items()}
    check('到达范围只有两种', set(scopes.values()) <= {'share', 'local'},
          repr(set(scopes.values())))
    check('两种范围都有（不是全成一类）',
          'share' in scopes.values() and 'local' in scopes.values(), repr(scopes))

    # 姿势名写错是静默的 —— Sprites.get 会退回 idle，动作看起来「没做」
    poses = {s['pose'] for s in A.ACTIONS.values()}
    poses |= {s['mirror'] for s in A.ACTIONS.values() if s.get('mirror')}
    check('用到的姿势素材都有', poses <= set(POSES), repr(sorted(poses - set(POSES))))

    sm = {k: v.get('mirror') for k, v in A.ACTIONS.items()}
    check('传出去的「一起做」指令有 mirror',
          sm['wave'] and sm['drink'] and sm['heart'] and sm['pat'], repr(sm))
    check('戳人/传话是单向的，不带 mirror',
          sm['knock'] is None and sm['say'] is None, repr(sm))
    check('不出门的指令不需要 mirror',
          all(not sm[k] for k in A.ACTIONS if A.is_local(k)), repr(sm))

    check('is_local 认得出来', A.is_local('sleep') and not A.is_local('wave'))
    check('get 不认识的 key 返回 None', A.get('不存在') is None)


def test_voice():
    print('\n--- 台词 ---')
    roles = list(A.VOICE)
    check('每个身份都有台词库', sorted(roles) == sorted(ROLES),
          repr(roles))

    missing = []
    for role in roles:
        v = A.VOICE[role]
        for k in A.ACTIONS:
            if A.is_local(k):
                if not v.get('l.' + k):
                    missing.append('%s/l.%s' % (role, k))
            else:
                # 「传个话」发的是用户自己打的字，本来就没有固定台词
                if not v.get('s.' + k) and k != 'say':
                    missing.append('%s/s.%s' % (role, k))
                if ('s.%s.me' % k) not in v:
                    missing.append('%s/s.%s.me' % (role, k))
    check('每个角色的每条指令都有台词位', not missing, repr(missing[:6]))

    check('单向指令不回应（敲窗/传话回空）',
          A.reply_line('knock', 'p1') == '' and A.reply_line('say', 'p2') == '')

    # 台词挑出来的必须真的属于那个人 —— 串味比没有台词更难发现
    bad = []
    for role in roles:
        for k in A.ACTIONS:
            if A.is_local(k):
                line = A.say_line(k, role, scope='l')
                if line not in A.VOICE[role]['l.' + k]:
                    bad.append('%s/l.%s=%s' % (role, k, line))
            else:
                line = A.say_line(k, role)
                if line and line not in A.VOICE[role]['s.' + k]:
                    bad.append('%s/s.%s=%s' % (role, k, line))
                reply = A.reply_line(k, role)
                if reply and reply not in A.VOICE[role]['s.%s.me' % k]:
                    bad.append('%s/s.%s.me=%s' % (role, k, reply))
    check('挑出来的台词都属于当前角色（没串味）', not bad, repr(bad[:6]))

    # 同一句挂在多个人头上就等于没分角色
    same = []
    for k in A.ACTIONS:
        pre = 'l.' if A.is_local(k) else 's.'
        pool = [set(A.VOICE[r].get(pre + k) or ()) for r in roles]
        if any(pool[i] & pool[j]
               for i in range(len(roles)) for j in range(i + 1, len(roles))):
            same.append(pre + k)
    check('各身份的台词不重样（口吻真的分开了）', not same, repr(same))

    check('被戳醒有台词', all(A.say_line('wake', r, scope='l') for r in roles))
    check('传话时用用户打的字，不套台词', A.say_line('say', 'p1') == '')


def test_tips():
    """互动痕迹的文案：聊天面板靠它把「敲了一下你」写成一句人话。"""
    print('\n--- 互动痕迹 ---')
    share = [k for k in A.ACTIONS if not A.is_local(k) and k != 'say']
    missing = [k for k in share if k not in A.TIP]
    check('每条传出去的指令都有提示文案', not missing, repr(missing))
    check('不出门的指令不留痕迹',
          all(k not in A.TIP for k in A.ACTIONS if A.is_local(k)),
          repr([k for k in A.TIP if A.is_local(k)]))

    bad = []
    for k in share:
        # 四种视角：我→某人、我→全员、别人→我、别人→全员
        texts = [A.tip_text(k, '你', one=True, target='胡大神'),
                 A.tip_text(k, '你'),
                 A.tip_text(k, '胡大神', one=True),
                 A.tip_text(k, '胡大神')]
        if not all(texts) or len(set(texts)) != 4:
            bad.append((k, texts))
    check('四种视角都出字，而且互不雷同', not bad, repr(bad[:2]))

    check('提示里带上了对象的名字',
          '胡大神' in A.tip_text('pat', '你', one=True, target='胡大神'),
          A.tip_text('pat', '你', one=True, target='胡大神'))
    check('不认识的指令没有提示（不瞎记）', A.tip_text('不存在', '你') == '')


def main():
    test_cooldown()
    test_catalog()
    test_voice()
    test_tips()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
