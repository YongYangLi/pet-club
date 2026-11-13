# -*- coding: utf-8 -*-
"""pet-club 客户端常量。

**服务器地址是固定的，客户端不接受配置。** 一台服务器就是一个房间，
所有人连同一台 —— 没有房间码、没有地址输入框、没有配置文件、没有环境变量。
想换服务器只有一条路：改这里的 `SERVER_HOST` / `SERVER_PORT` 重新打包。

这个取舍是有意的：
  地址可配的时候，每个人都要理解「填谁的 IP」这件事，还得处理填错、连不上、
  配置被环境变量覆盖、换地址后老地址还在配置里 —— 全部是纯负担。
  一个固定的房间，打开就是进去，这才配得上「桌面挂件」的定位。

`App(host, port)` 仍然可以传参，那是给测试用的（探针要在本机临时起服务端），
不是给用户的入口。
"""

import json
import os
import sys
import tempfile

from . import host

ROLES = ('p1', 'p2', 'p3', 'p4', 'p5', 'p6')
ROLE_NAME = {'p1': '猥琐', 'p2': '胡大神', 'p3': '杨大师', 'p4': '婧婧',
             'p5': '雷大神', 'p6': '左大神'}

# 唯一的服务器。改这两行 = 改整个客户端的去处。
SERVER_HOST = '47.94.21.34'
SERVER_PORT = 8765
ENDPOINT = (SERVER_HOST, SERVER_PORT)

HEARTBEAT_SEC = 5.0        # 心跳间隔，服务端 30 秒收不到就判离线
RECONNECT_SEC = 3.0        # 断线重连的初始退避（会一路退到 20 秒）
POLL_MS = 33               # 主循环节拍（约 30fps）

# ---------------- 桌面角色的大小 ----------------
#
# 素材是物理像素。Qt 版按屏幕 dpr 换算，1 个素材像素恒等于 1 个屏幕物理
# 像素，所以任何缩放下角色的屏幕宽度都固定。旧 Tk 版是把素材像素当**逻辑**
# 像素交给窗口管理器，系统在 175% 缩放下又乘了一次 dpr —— 等效放大 1.75 倍，
# 观感上比 Qt 版大一圈。换算本身没错，不是 bug，这里给一个显式的观感系数：
#
#   PET_SCALE = 0   跟随当前屏幕 dpr（每台机器都复现旧版观感，默认）
#   PET_SCALE = 1.0 一比一，按素材物理像素显示（Qt 版原样）
#   PET_SCALE = 1.75 固定放大 1.75 倍（手动锁定 175% 那台机器的旧版观感）
#
# 界面上不再让人改这个数字，改成三档「小 / 默认 / 大」（见下面 PET_SIZE_*），
# 相乘在 dpr 上 —— 换一台分辨率不同的机器，档位的观感还是一致的。
#
# 只是放大绘制与命中区域，业务坐标（脚底中心、地面、拖动）仍是物理像素，
# 服务端和旧客户端都不需要改。
PET_SCALE = 0.0

PET_SIZE_ORDER = ('small', 'default', 'large')
PET_SIZE_NAME = {'small': '小', 'default': '默认', 'large': '大'}
PET_SIZE_FACTOR = {'small': 0.78, 'default': 1.0, 'large': 1.3}

# 运行时改这一项（App.set_pet_size + prefs.json 里的 pet_size）
PET_SIZE = 'default'


def pet_scale(dpr):
    """角色相对素材的显示倍数。dpr 用屏幕真实的 devicePixelRatio。"""
    if PET_SCALE > 0:
        return float(PET_SCALE)
    return float(dpr) * PET_SIZE_FACTOR.get(PET_SIZE, 1.0)

# ---------------- 自动更新 ----------------
#
# 版本号写在代码里，打包时同步进 exe 的文件属性 / .app 的 Info.plist
# （tools/build_app.py 读这里），发版时 tools/make_release.py 再把同一个号写进
# COS 上的清单。三处必须是同一个来源，否则会出现「客户端说自己 1.1.0，清单也说 1.1.0，
# 但内容其实不一样」这种最难受的情况。
VERSION = '1.6.0'

# latest.json 的公网地址。**留空 = 关掉自动更新**（源码模式、内网都是这个状态）。
# 形状：https://<桶名>.cos.<地域>.myqcloud.com/<目录>/latest.json
# 桶开「公有读」就行：客户端只是普通 HTTP GET，不涉及签名。
# 注意：COS 的域名里已经有桶名了，路径**不再重复一次桶名**。
# 早期这里多写了一段 /dev-cos-1259275319/，结果客户端一路 404（拉不到清单）。
UPDATE_URL = ('https://dev-cos-1259275319.cos.ap-shanghai.myqcloud.com'
              '/pet-club/latest.json')

UPDATE_TIMEOUT = 8          # 拉清单的超时（秒）
UPDATE_EVERY = 3 * 3600     # 后台多久看一次有没有新版
UPDATE_START_DELAY = 4.0    # 启动后等多久再去看（别跟连服务器抢）


def version():
    return VERSION


def format_endpoint(host, port):
    """只用于显示和日志：好让人一眼看出「现在连的是哪儿」。"""
    return '%s:%d' % (host, int(port))


# ---------------- 目录 ----------------

def app_dir():
    """源码模式 = 项目根；打包后 = exe 所在目录。"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def assets_dir():
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'assets')
    return os.path.join(app_dir(), 'assets')


def files_dir():
    """聊天里收到/发出的附件落在哪儿。

    打包后**不能**放 exe 旁边：那可能是 C:\\Program Files，普通用户没有写权限，
    而且卸载时会被一起删掉。所以交给 host 按平台给一个用户目录
    （Windows: %LOCALAPPDATA%\\pet-club，macOS: ~/Library/Application Support/pet-club）。
    源码模式下放项目里的 petclub-data/，看一眼就知道刚才那张图存哪儿了。
    """
    if hasattr(sys, '_MEIPASS'):
        d = os.path.join(host.data_dir(), 'files')
    else:
        d = os.path.join(app_dir(), 'petclub-data', 'files')
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        # 极端受限（只读盘、被策略挡住）：临时目录总能写
        d = os.path.join(tempfile.gettempdir(), 'pet-club-files')
        os.makedirs(d, exist_ok=True)
        return d


# ---------------- 本机偏好（prefs.json） ----------------
#
# 只存「这台机器上的观感选择」，不存身份、不存服务器地址。
# 场景很小，一个扁平的 JSON 就够了，坏了就当没有。

PREFS_NAME = 'prefs.json'


def prefs_path():
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(host.data_dir(), PREFS_NAME)
    return os.path.join(app_dir(), 'petclub-data', PREFS_NAME)


def load_prefs():
    try:
        with open(prefs_path(), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(data):
    """写盘失败不抛：偏好丢了顶多回到默认大小，不该影响启动。"""
    path = prefs_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
