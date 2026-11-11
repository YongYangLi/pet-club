# -*- coding: utf-8 -*-
"""客户端网络层。

铁律：**网络在子线程，UI 在主线程**。
Tk 不是线程安全的 —— 从网络线程直接去碰窗口，轻则闪烁重则崩溃，
而且是那种偶发、难复现、pythonw 下连报错都看不到的崩溃。
所以子线程只做一件事：把收到的消息塞进 inbox 队列；
主线程在 tick 里 poll() 取出来串行处理。Tk 里的东西自始至终只有一个线程碰。

反过来，主线程发消息直接调 send() —— socket.sendall 本身线程安全，
再加一把锁防止两条消息交叠。
"""

import json
import queue
import socket
import threading
import time

from . import config


class Net:
    """一条到服务端的长连接，自带心跳不负责、只负责收发与重连。"""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.inbox = queue.Queue()
        self.sock = None
        self.wlock = threading.Lock()
        self.connected = False
        self._stop = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        backoff = config.RECONNECT_SEC
        while not self._stop:
            try:
                s = socket.create_connection((self.host, self.port), timeout=8)
            except OSError as e:
                # 屏幕上只说「连不上」，异常原文进控制台：里面可能带着地址，
                # 而地址属于实现细节，不该出现在气泡、提示、菜单里。
                print('[pet-club] 连接 %s:%d 失败：%s'
                      % (self.host, self.port, e))
                self.inbox.put({'t': '_down', 'first': True,
                                'msg': '连不上服务器'})
                time.sleep(backoff)
                backoff = min(backoff * 1.6, 20.0)      # 退避，别把服务器打爆
                continue
            backoff = config.RECONNECT_SEC
            self.sock = s
            self.connected = True
            self.inbox.put({'t': '_up'})
            try:
                for line in s.makefile('rb'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode('utf-8'))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(msg, dict):
                        self.inbox.put(msg)
            except OSError:
                pass
            self.connected = False
            with self.wlock:
                self.sock = None
            if not self._stop:
                self.inbox.put({'t': '_down', 'first': False,
                                'msg': '与服务器断开了，正在重连…'})

    def send(self, **kw):
        with self.wlock:
            s = self.sock
            if s is None:
                return False
            try:
                s.sendall((json.dumps(kw, ensure_ascii=False) + '\n').encode('utf-8'))
                return True
            except OSError:
                return False

    def poll(self):
        """主线程调用：把此刻已到达的消息全部取走。"""
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def stop(self):
        self._stop = True
        with self.wlock:
            s = self.sock
            self.sock = None
        if s:
            try:
                s.close()
            except OSError:
                pass
