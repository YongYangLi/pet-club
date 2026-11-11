# -*- coding: utf-8 -*-
"""动作冷却 —— 防止连点把队友的气泡刷满。

【为什么在客户端做，不在服务端】
  这是「别打扰别人」的礼貌约束，不是安全边界。客户端做，连点的人在菜单上
  立刻看得到「还要等 3 秒」；服务端做只能把消息静默丢掉，用户会以为程序坏了。
  真要防恶意刷屏，服务端那份仍然得单独加（现在没加）。

【为什么时间要从外面传】
  `now` 是调用方给的，这里不读 time.monotonic —— 测试要能喂假时间，
  否则每条断言都得真睡几秒。

【为什么全员共用一个桶】
  见 App.cd_key：如果按动作分桶，把「打招呼/喝水/送心」挨个点一遍就等于没冷却。
"""


class Cooldown:

    def __init__(self):
        self._until = {}

    def left(self, key, now):
        """距离这个 key 可用还剩几秒。0 表示现在就能用。"""
        return max(0.0, self._until.get(key, 0.0) - now)

    def ready(self, key, now):
        return self.left(key, now) <= 0.0

    def mark(self, key, now, span):
        """记一次使用。跨度非正数等于不冷却（留着方便单独关掉某个动作）。"""
        if span > 0:
            self._until[key] = now + span
        else:
            self._until.pop(key, None)

    def reset(self):
        """全部清空。换服务器、重新认领之后应该清一下 —— 那是另一段人生。"""
        self._until.clear()
