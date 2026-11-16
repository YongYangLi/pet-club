# -*- coding: utf-8 -*-
"""桌面端和网页端共用的视觉 token。

为什么单独一个模块：`theme.py` 要 import Qt 和平台相关的 `host`，而网页网关
（`web_gateway.py`）跑在服务端，那边没有 Qt。这里只依赖 json / os，两端都能读，
是「一份 token 喂两个渲染器」的唯一入口。

真源是 `assets/theme.json`：它在 assets/ 里，会进 exe、也会被发布脚本带上服务器，
而且参与 build 指纹 —— 改了颜色，网页那边会整页刷新拿到新值，不会出现
「桌面端换了色、网页还是旧的」。

读不到文件就回落到 DEFAULTS：老版本 exe、或者 assets 没同步全的时候，
界面只是少了新颜色，不会崩。
"""

import json
import os
import sys

DEFAULTS = {
    'color': {
        'window': '#ffffff',
        'border': '#e4e7ef',
        'fg': '#111827',
        'sub': '#6b7280',
        'muted': '#9ca3af',
        'line': '#e5e7eb',
        'field': '#f7f8fb',
        'chrome': '#f1f2f6',
        'chrome_line': '#e3e5ee',
        'chrome_hover': '#e7e9f2',
        'chrome_sel': '#ffffff',
        'card_bg': '#f7f8fc',
        'card_line': '#eaecf4',
        'accent': '#5b5bf0',
        'accent_down': '#4a4ade',
        'accent_soft': '#f1f2ff',
        'accent_edge': '#b9bbff',
        'ok': '#0f9d76',
        'ok_bg': '#e9f8f2',
        'err': '#e5484d',
        'taken': '#a8aeba',
        'taken_bg': '#f1f2f5',
    },
    'radius': {
        'sm': 8,
        'md': 10,
        'lg': 14,
        'card': 16,
    },
}


def path_of():
    """theme.json 的位置。打包后在 _MEIPASS/assets，开发时在仓库根 assets。"""
    try:
        from .config import assets_dir
        return os.path.join(assets_dir(), 'theme.json')
    except Exception:                           # 服务端没有 config 依赖链
        here = os.path.dirname(os.path.abspath(__file__))
        base = getattr(sys, '_MEIPASS', None) or os.path.dirname(here)
        return os.path.join(base, 'assets', 'theme.json')


def load():
    """合并后的 token。缺的键用 DEFAULTS 补齐，文件坏了也不影响启动。"""
    data = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        with open(path_of(), 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data
    for sec, vals in raw.items():
        if not isinstance(vals, dict) or sec not in data:
            continue
        for k, v in vals.items():
            if not k.startswith('_'):
                data[sec][k] = v
    return data


def css_vars(tokens=None):
    """颜色 token 拼成 CSS 变量声明，给 style.css 末尾追加用。

    下划线转连字符是唯一约定：Python 侧的 CHROME_LINE 对应 CSS 的 --chrome-line。
    """
    tokens = tokens or load()
    out = []
    for k, v in tokens.get('color', {}).items():
        out.append('--%s:%s;' % (k.replace('_', '-'), v))
    return ''.join(out)
