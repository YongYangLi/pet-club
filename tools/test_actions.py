# -*- coding: utf-8 -*-
"""指令表、台词、互动痕迹的自测（纯逻辑，不开窗口）。

跑法：python tools/test_actions.py

这套保证三件事：加新指令时不会漏台词、每条指令都出得了门、痕迹文案对得上。
菜单和面板那层显示逻辑在 probe_live / test_chat_ui 里测。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from petclub import actions as A          # noqa: E402
from petclub.config import ROLES          # noqa: E402
from petclub.sprites import POSES         # noqa: E402

PASS = []
FAIL = []

# 明确不留聊天痕迹的：环境动作，动了就动了，不必在群里报一遍。
# 这份名单和 actions.TIP 互为对照 —— 名单里的人出现在 TIP 里、名单外的人
# 不在 TIP 里，都算错（见 test_tips）。
AMBIENT = ()


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s%s' % ('[ok]  ' if ok else '[FAIL]', name,
                       ('  ← ' + detail) if (detail and not ok) else ''), flush=True)


def test_catalog():
    print('--- 指令表 ---')
    check('顺序表和指令表一一对应',
          set(A.ORDER) == set(A.ACTIONS) and len(A.ORDER) == len(A.ACTIONS),
          repr(set(A.ORDER) ^ set(A.ACTIONS)))
    check('每条指令都有 label / hint',
          all(all(spec.get(k) for k in ('label', 'hint'))
              for spec in A.ACTIONS.values()),
          repr([k for k, v in A.ACTIONS.items()
                if not all(v.get(f) for f in ('label', 'hint'))]))
    # 姿势类指令要有 pose + dur；起飞是物理动作（launch），不摆姿势、也没有时长
    bad_spec = [k for k, s in A.ACTIONS.items()
                if not (s.get('launch') or (s.get('pose') and s.get('dur')))]
    check('姿势类指令有 pose + dur，起飞走 launch', not bad_spec, repr(bad_spec))
    check('起飞不占姿势位（不会误当 idle 演）',
          'pose' not in A.ACTIONS['fly']
          and A.ACTIONS['fly'].get('mirror') == 'launch', repr(A.ACTIONS['fly']))
    check('菜单里不重名',
          len({s['label'] for s in A.ACTIONS.values()}) == len(A.ACTIONS))

    # 全部指令都会发 event 出去：没有「只在我这块屏幕上」的分支了
    check('没有「只在本机生效」的指令了',
          all('scope' not in s for s in A.ACTIONS.values()),
          repr([k for k, s in A.ACTIONS.items() if 'scope' in s]))

    check('每条指令都声明了 mirror（没有也写成 None）',
          all('mirror' in s for s in A.ACTIONS.values()),
          repr([k for k, s in A.ACTIONS.items() if 'mirror' not in s]))

    # 聊天窗里那颗「传个话…」是多余的：人已经在窗口里了
    check('聊天窗的互动条少一颗「传个话」',
          list(A.CHAT_ORDER) == [k for k in A.ORDER if k != 'say']
          and 'say' in A.ORDER, repr(A.CHAT_ORDER))

    # 姿势名写错是静默的 —— Sprites.get 会退回 idle，动作看起来「没做」
    # 'launch' 不是素材，是物理动作，别混进姿势表里比
    poses = {s['pose'] for s in A.ACTIONS.values() if s.get('pose')}
    poses |= {s['mirror'] for s in A.ACTIONS.values()
              if s.get('mirror') and s['mirror'] != 'launch'}
    check('用到的姿势素材都有', poses <= set(POSES), repr(sorted(poses - set(POSES))))

    sm = {k: v.get('mirror') for k, v in A.ACTIONS.items()}
    check('「一起做」的指令有 mirror',
          sm['wave'] and sm['drink'] and sm['heart'], repr(sm))
    check('碰一下就吓一跳：拍一拍 / 摸摸头都让对方跳',
          sm['pat'] == 'jump' and sm['pet'] == 'jump', repr(sm))
    check('传话是单向的，不带 mirror', sm['say'] is None, repr(sm))
    check('睡觉 / 跳 / 起飞对方跟着做',
          sm['sleep'] == 'sleep' and sm['jump'] == 'jump'
          and sm['fly'] == 'launch', repr(sm))
    check('「敲敲窗」和「发个呆」已经撤掉',
          'knock' not in A.ACTIONS and 'dream' not in A.ACTIONS
          and '敲敲窗' not in {s['label'] for s in A.ACTIONS.values()},
          repr(A.ORDER))

    # 集合是唯一一条「别人跟着做的是位移」的指令：不摆 mirror 姿势，
    # 由 App._gather 单独演，排在菜单最后
    g = A.ACTIONS.get('gather') or {}
    check('集合：位移标记 + 没有 mirror + 排在最后',
          g.get('gather') is True and g.get('mirror') is None
          and A.ORDER[-1] == 'gather' and A.CHAT_ORDER[-1] == 'gather',
          (g, A.ORDER[-1], A.CHAT_ORDER[-1]))
    check('集合用的爬行帧在素材表里',
          {'crawl_1', 'crawl_2'} <= set(POSES))

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
            if k == 'say':
                continue            # 传话发的是用户自己打的字，没有固定台词
            if not v.get('s.' + k):
                missing.append('%s/s.%s' % (role, k))
            if ('s.%s.me' % k) not in v:
                missing.append('%s/s.%s.me' % (role, k))
    check('每个角色的每条指令都有台词位', not missing, repr(missing[:6]))

    check('单向指令不回应（传话回空）', A.reply_line('say', 'p2') == '')
    check('起飞两边都有台词（不是单向）',
          A.reply_line('fly', 'p2') and A.say_line('fly', 'p2'),
          (A.reply_line('fly', 'p2'), A.say_line('fly', 'p2')))
    check('集合两边都有台词（爬过来的人也要应一声）',
          all(A.reply_line('gather', r) and A.say_line('gather', r)
              for r in roles))

    # 台词挑出来的必须真的属于那个人 —— 串味比没有台词更难发现
    bad = []
    for role in roles:
        for k in A.ACTIONS:
            if k == 'say':
                continue
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
        if k == 'say':
            continue
        pool = [set(A.VOICE[r].get('s.' + k) or ()) for r in roles]
        if any(pool[i] & pool[j]
               for i in range(len(roles)) for j in range(i + 1, len(roles))):
            same.append('s.' + k)
    check('各身份的台词不重样（口吻真的分开了）', not same, repr(same))

    check('被戳醒有台词', all(A.say_line('wake', r) for r in roles))
    check('传话时用用户打的字，不套台词', A.say_line('say', 'p1') == '')


def test_tips():
    """互动痕迹的文案：聊天面板靠它把「敲了一下你」写成一句人话。"""
    print('\n--- 互动痕迹 ---')
    # TIP 是留痕的唯一开关：在里面就记，不在就是环境动作。名单在这里写死，
    # 加指令时忘了补 TIP（痕迹静默消失）或者多补一条都会红。
    logged = set(A.ORDER) - set(AMBIENT) - {'say'}
    check('TIP 正好覆盖「该留痕的指令」',
          set(A.TIP) == logged,
          '多记=%r 漏记=%r' % (sorted(set(A.TIP) - logged),
                              sorted(logged - set(A.TIP))))
    check('TIP 里没有指令表之外的 key', set(A.TIP) <= set(A.ACTIONS),
          repr(sorted(set(A.TIP) - set(A.ACTIONS))))
    check('不再有第二处留痕开关（log 之类的字段）',
          all('log' not in s for s in A.ACTIONS.values()),
          repr([k for k, s in A.ACTIONS.items() if 'log' in s]))
    check('传话不单独留痕',
          not {'say'} & set(A.TIP),
          repr({'say'} & set(A.TIP)))

    bad = []
    for k in sorted(logged):
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
    test_catalog()
    test_voice()
    test_tips()
    print('\n通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    for n in FAIL:
        print('  - %s' % n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
