# -*- coding: utf-8 -*-
"""桌面端聊天记录的本地存储。

放在用户数据目录，不放在 exe 旁边：

    Windows  %LOCALAPPDATA%\\pet-club\\chat-127.0.0.1-8765.db
    macOS    ~/Library/Application Support/pet-club/chat-<host>-<port>.db

按服务器端点分文件：本地房间和远程房间各有一份历史，测试用随机端口也
不会污染真实记录。每个会话只保留最近 200 条。

图片不存进数据库：media.save_chat_image 把字节落到 files/images/ 下，
这里只存路径。这样 SQLite 不会因为几张图变成几十兆。
"""

import json
import os
import sqlite3

from . import host

LIMIT = 200


def _db_path(host_name, port):
    safe = ''.join(c if (c.isalnum() or c in '.-_') else '_'
                   for c in str(host_name or 'local'))
    return os.path.join(host.data_dir(),
                        'chat-%s-%s.db' % (safe, int(port or 0)))


class History:
    """一条 SQLite 连接，足够这个单人客户端用。"""

    def __init__(self, host_name=None, port=None):
        self.conn = None
        path = _db_path(host_name, port)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.conn = sqlite3.connect(path, timeout=5)
            self.conn.execute(
                'CREATE TABLE IF NOT EXISTS messages ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
                ' conv TEXT NOT NULL,'
                ' ts REAL NOT NULL,'
                ' kind TEXT,'
                ' who TEXT,'
                ' text TEXT,'
                ' mt TEXT,'
                ' char TEXT,'
                ' img TEXT,'
                ' name TEXT,'
                ' size INTEGER,'
                ' path TEXT,'
                ' re TEXT)')
            # 老库没有 re 列：引用回复是 1.6.0 之后加的，直接 ALTER 补上。
            # 已经有过这一列时 SQLite 会报 duplicate column，忽略即可。
            try:
                self.conn.execute('ALTER TABLE messages ADD COLUMN re TEXT')
            except sqlite3.Error:
                pass
            self.conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_messages_conv'
                ' ON messages(conv, id)')
            self.conn.commit()
        except (OSError, sqlite3.Error):
            self.conn = None

    def load(self):
        """返回 {conv: [entry, ...]}，每个会话最多 LIMIT 条。"""
        out = {}
        if self.conn is None:
            return out
        try:
            rows = self.conn.execute(
                'SELECT conv, ts, kind, who, text, mt, char, img, name, size,'
                ' path, re FROM messages ORDER BY id').fetchall()
        except sqlite3.Error:
            return out
        for (conv, ts, kind, who, text, mt, char, img, name, size, path,
             re_raw) in rows:
            entry = {
                'kind': kind or 'msg',
                'ts': float(ts or 0),
                'who': who or '',
                'text': text or '',
                'mt': mt or 'text',
                'char': char or '',
                'img': img or '',
                'name': name or '',
                'size': int(size or 0),
                'path': path or '',
            }
            # 有稳定文件路径时不再把 base64 读回内存
            if entry['path']:
                entry['img'] = ''
            re_obj = None
            if re_raw:
                try:
                    re_obj = json.loads(re_raw)
                except (TypeError, ValueError):
                    re_obj = None
            if isinstance(re_obj, dict):
                entry['re'] = re_obj
            out.setdefault(conv, []).append(entry)
        for conv in out:
            out[conv] = out[conv][-LIMIT:]
        return out

    def add(self, conv, entry):
        if self.conn is None:
            return
        img = entry.get('img') or ''
        path = entry.get('path') or ''
        if path:
            img = ''                # 图片已经落盘，数据库只记路径
        try:
            self.conn.execute(
                'INSERT INTO messages'
                ' (conv, ts, kind, who, text, mt, char, img, name, size, path, re)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (conv, float(entry.get('ts') or 0),
                 entry.get('kind') or 'msg',
                 entry.get('who') or '',
                 entry.get('text') or '',
                 entry.get('mt') or 'text',
                 entry.get('char') or '',
                 img,
                 entry.get('name') or '',
                 int(entry.get('size') or 0),
                 path,
                 (json.dumps(entry['re'], ensure_ascii=False)
                  if isinstance(entry.get('re'), dict) else None)))
            self.conn.execute(
                'DELETE FROM messages WHERE conv = ? AND id NOT IN'
                ' (SELECT id FROM messages WHERE conv = ?'
                '  ORDER BY id DESC LIMIT ?)',
                (conv, conv, LIMIT))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def clear(self, conv=None):
        if self.conn is None:
            return
        try:
            if conv is None:
                self.conn.execute('DELETE FROM messages')
            else:
                self.conn.execute('DELETE FROM messages WHERE conv = ?', (conv,))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
