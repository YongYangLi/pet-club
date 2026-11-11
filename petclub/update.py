# -*- coding: utf-8 -*-
"""从腾讯云 COS 拉新版 exe，换掉自己。

一次更新分三步，每一步都可能失败，所以每一步都只用「文件系统里能验证的东西」
来判断成功与否（大小、sha256），不靠「下载函数返回了没报错」。

  1. 看清单      GET latest.json → 比版本号
  2. 下载        存成 <程序目录>\\pet-club.exe.new，校验大小和 sha256
                 校验失败的半成品当场删掉 —— 留着的坏文件会在下次启动时被
                 当成「已就绪」直接换上去，那就是把人变砖
  3. 换          跑着的 exe 是锁着的，谁都删不掉改不了，**包括它自己**。
                 所以「换」只能发生在进程退出之后，由一个 detach 的 .bat 干：
                 等老进程退出 → 摘掉只读位 → move → 成功再把新 exe 拉起来。

为什么非要在退出后才换：Windows 对映射中的映像文件加了写锁，MoveFileEx 也绕不过去。
重命名「自己」是可行的（同目录改名不破坏映像锁），然后启动新 exe —— 这是另一条
常见路子，代价是每次更新都在磁盘上留一个 pet-club.exe.old，且旧名会被杀软盯上。
.bat 那条更土，但只多一个临时文件，失败了还能看出来（.new 还在 = 没换成）。

三个真实踩过的坑，修复都在本文件里：
  · 目标 exe 带只读位 → `move /y` 直接「拒绝访问」，重试多少次都换不上；
  · `DETACHED_PROCESS` 和 `CREATE_NO_WINDOW` 一起传给 cmd，后者被忽略 →
    屏幕角落弹出一个黑窗在里面 ping 本机（见下面常量的注释）；
  · 拉起新版本时**继承了老进程的 PyInstaller 环境变量** → 新 exe 以为
    自己已经被解包过，不去解包，直接去老进程的临时目录里找 python3xx.dll，
    弹一个「Failed to load Python DLL ... 找不到指定的模块」然后退出。
    桌面上的 exe 其实已经换好了，双击也正常 —— 只有自动重启这一步是坏的。
    见 PYI_ENV_VARS 和 _clean_env()。
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import config

EXE_NAME = 'pet-club.exe'
NEW_SUFFIX = '.new'
META_SUFFIX = '.new.json'

# 【别把 DETACHED_PROCESS 和 CREATE_NO_WINDOW 一起用】
# MSDN 上 CREATE_NO_WINDOW 那条写着：「与 CREATE_NEW_CONSOLE 或 DETACHED_PROCESS
# 一起用时本标志被忽略」。两个一起传的结果就是回到 DETACHED_PROCESS 的行为 ——
# cmd.exe 没有可继承的控制台，于是自己分配一个，**弹出一个可见的黑窗**，
# 里面跑着我们那句 `ping -n 2 127.0.0.1`。
# 用户就是这么看到的：点「更新」之后屏幕角落多一个 cmd 窗口在 ping 本机。
# 只留 CREATE_NO_WINDOW 才是真的没有窗口（tools/probe_update.py 验的就是这个）。
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
SW_HIDE = 0

# 起更新脚本时依次尝试的创建标志。tools/probe_update.py 拿它去验「真的没有窗口」。
FLAG_CANDIDATES = (CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB,
                   CREATE_NO_WINDOW)

# 换文件失败时脚本记的日志。放在 TEMP 里，用户不会看见，出问题能查。
LOG_NAME = 'pet-club-update.log'

# 【拉起新版本之前必须清掉这几个变量】
# 单文件 exe 的 bootloader 解包完会在环境里留下这三个变量，意思是
# 「我已经解好了，临时目录在 _PYI_APPLICATION_HOME_DIR」。它们是给**自己那个
# 子进程**看的，可我们更新脚本也是它的子进程，cmd 和 start 一路原样继承下去。
# 新 exe 一看三个变量齐全，就跳过解包，直接去老进程的临时目录里加载
# python3xx.dll —— 那个目录随老进程退出已经删了，于是弹窗
#   Failed to load Python DLL '...\\_MEI...\\python315.dll'
#   LoadLibrary: 找不到指定的模块。
# 而文件其实早换好了，双击又能正常启动，所以看起来特别像「版本坏了」。
# 三个都要清：缺一个 bootloader 就不会走进那条「我已经解过包」的分支。
PYI_ENV_VARS = ('_PYI_APPLICATION_HOME_DIR',
                '_PYI_ARCHIVE_FILE',
                '_PYI_PARENT_PROCESS_LEVEL')


def _clean_env():
    """去掉 PyInstaller 留下的启动器变量，别让新进程以为自己被解包过。

    除了上面三个，老版本 PyInstaller 还会往环境里塞 `_MEIPASS2` 和
    `_MEI<数字>`，一并清掉 —— 留着它们同样会把新进程带进老目录。
    """
    env = dict(os.environ)
    for key in list(env):
        if key in PYI_ENV_VARS or key == '_MEIPASS2':
            del env[key]
        elif key.startswith('_MEI') and key[4:].isdigit():
            del env[key]
    return env


# ---------------- 版本号 ----------------

def parse(text):
    """'1.2.3' → (1, 2, 3)。看不懂的一律当 0，绝不抛异常。

    版本号是要在启动路径上比较的东西，为一个畸形字符串崩掉整个程序不值得。
    """
    out = []
    for part in str(text or '').replace('-', '.').split('.'):
        digits = ''.join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(remote, local):
    return parse(remote) > parse(local)


def enabled():
    """有没有开自动更新：得配了地址，而且得是打包后的 exe。"""
    return bool(config.UPDATE_URL) and is_frozen()


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


# ---------------- 路径 ----------------

def exe_path():
    """当前正在跑的 exe。源码模式下没有（也就没法自更新）。"""
    return os.path.abspath(sys.executable) if is_frozen() else ''


def new_path():
    p = exe_path()
    return p + NEW_SUFFIX if p else ''


def meta_path():
    p = new_path()
    return p + '.json' if p else ''


# ---------------- 清单 ----------------

def _get(url, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': 'pet-club-updater'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def manifest(info_url=None, timeout=None):
    """拉并校验清单。返回 (info, err)，err 是给日志看的一句话。"""
    url = info_url or config.UPDATE_URL
    if not url:
        return None, '没有配置更新地址'
    timeout = timeout or config.UPDATE_TIMEOUT
    try:
        raw = _get(url, timeout)
    except urllib.error.HTTPError as e:
        # HTTPError 是 URLError 的子类，必须排在前面。
        # 带上状态码：404 = 地址写错 / 桶里还没传清单，500 = 桶那边的问题 ——
        # 只写「拉不到清单」的话，这两种情况在界面上完全一样，没法排查。
        return None, '拉不到清单：HTTP %d' % e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, '拉不到清单：%s' % (getattr(e, 'reason', e),)
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None, '清单不是合法的 JSON'
    if not isinstance(data, dict):
        return None, '清单格式不对'

    ver = data.get('version')
    target = data.get('url') or data.get('file')
    if not ver or not target:
        return None, '清单缺 version 或 url'
    if not isinstance(target, str):
        return None, '清单里的 url 不是字符串'
    # 允许写相对地址：清单和 exe 通常在同一层目录里
    target = urllib.parse.urljoin(url, target)

    size = data.get('size')
    return {'version': str(ver),
            'url': target,
            'size': int(size) if isinstance(size, int) else 0,
            'sha256': str(data.get('sha256') or '').lower(),
            'note': str(data.get('note') or '').strip(),
            'date': str(data.get('date') or ''),
            'raw': data}, ''


def check(cur=None, info_url=None, timeout=None):
    """看有没有新版。返回 (info, err)，info 为 None = 已是最新。"""
    cur = cur or config.VERSION
    info, err = manifest(info_url, timeout)
    if err:
        return None, err
    if not is_newer(info['version'], cur):
        return None, ''
    return info, ''


# ---------------- 下载与暂存 ----------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def download(info, progress=None, timeout=60):
    """下载到 .new.part，校验通过再改名成 .new。返回 (路径, err)。

    先写临时名再改名，是为了让「.new 存在」这件事本身就等于「这份下好了、校验过了」。
    中途断网留下的是 .part，不会被当成可用文件。
    """
    new = new_path()
    if not new:
        return '', '这个版本不支持自动更新'
    part = new + '.part'
    try:
        req = urllib.request.Request(
            info['url'], headers={'User-Agent': 'pet-club-updater'})
        with urllib.request.urlopen(req, timeout=timeout) as r, \
                open(part, 'wb') as f:
            total = info.get('size') or 0
            done = 0
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / float(total)))
    except (urllib.error.URLError, OSError, ValueError) as e:
        _unlink(part)
        return '', '下载失败：%s' % (getattr(e, 'reason', e),)

    size = os.path.getsize(part)
    if info.get('size') and size != info['size']:
        _unlink(part)
        return '', '下载不完整（%d / %d 字节）' % (size, info['size'])
    if info.get('sha256'):
        got = _sha256(part)
        if got != info['sha256']:
            _unlink(part)
            return '', '校验不一致，文件可能下坏了'
    try:
        os.replace(part, new)
    except OSError as e:
        _unlink(part)
        return '', '存不下来：%s' % (e,)
    try:
        with open(meta_path(), 'w', encoding='utf-8') as f:
            json.dump({'version': info['version'], 'sha256': info.get('sha256'),
                       'size': size, 'note': info.get('note', '')}, f)
    except OSError:
        pass                        # 旁注文件写不了不影响换，只是菜单里少个版本号
    return new, ''


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def staged():
    """已经下好、等下次启动换上的那份。返回 (路径, 版本号) 或 ('', '')。"""
    p = new_path()
    if not p or not os.path.exists(p):
        return '', ''
    ver = ''
    try:
        with open(meta_path(), encoding='utf-8') as f:
            ver = str(json.load(f).get('version') or '')
    except (OSError, ValueError):
        pass
    return p, ver


def staged_valid():
    """暂存的那份还在不在、大小对不对得上（.new 只会在校验通过后出现）。

    另外挡一种很坑的情况：**手边这份 .new 比我自己还旧**。
    比如用户把新 exe 手工拷到一个还躺着旧 .new 的目录里（桌面上就是这么摆的），
    启动时若无条件 apply，会把自己「降级」回旧版本，再更新、再降级，来回打转。
    """
    p, ver = staged()
    if not p:
        return False, '', ''
    if ver and not is_newer(ver, config.VERSION):
        return False, '', ''
    try:
        size = os.path.getsize(p)
    except OSError:
        return False, '', ''
    return size > 0, ver, p


# ---------------- 换上 ----------------

def log_path():
    return os.path.join(os.environ.get('TEMP') or os.path.expanduser('~'),
                        LOG_NAME)


def _script(exe, new, relaunch, pid=None, log=None):
    """生成那段「等老进程退出 → 换 → 拉起」的批处理。

    路径里可能有中文和空格（桌宠常被放在「桌面\\新建文件夹」里），所以每处都加引号；
    .bat 用 ANSI 代码页写，cmd 才认得中文路径。

    两处细节都是踩过的坑：

    · `attrib -r` 必须先跑。Windows 的 move 覆盖只读文件会直接「拒绝访问」，
      重试一辈子也换不上 —— 而下载来的 exe 从浏览器/压缩包里出来时常带只读位。
      用户遇到的就是这个：.new 下好了、重试了 60 次、老 exe 一个字节没变。
    · 等老进程退出用 `tasklist /FI "PID eq <pid>"` 认人，不再靠「move 失败就等一秒」。
      盲等等于把「文件被占用」和「路径写错」「只读」「被杀软锁住」当成同一件事，
      反正都是 move 不成功，日志里看不出区别。
    """
    lines = [
        '@echo off',
        'setlocal',
        'set "NEW=%s"' % new,
        'set "EXE=%s"' % exe,
        # 拉起来之后靠它认进程（tasklist 的 IMAGENAME 只要文件名，不带路径）
        'set "EXENAME=%s"' % os.path.basename(exe),
        # 外部命令一律走 System32 的全路径。装在 PATH 里的 `find` 未必是 Windows 那个
        # （Git for Windows 就自带一个 POSIX find，先命中它就会报
        # 「find: '1234': No such file or directory」，而我们把它当成「进程已经没了」）。
        'set "SYS=%SystemRoot%\\System32"',
    ]
    if log:
        lines += [
            'set "LOG=%s"' % log,
            # 注意：这一句不走 % 格式化，所以这里写单个 %DATE%、%LOG%
            # 就是批处理要看到的样子。写成 %%DATE%% 会被 cmd 当成转义，
            # 日志里就出现一行字面量 [%DATE% %TIME%]（踩过）。
            '>>"%LOG%" echo [%DATE% %TIME%] 开始更新',
        ]
        logger = '>>"%LOG%" echo [%DATE% %TIME%] '
    else:
        logger = None

    # 等自己退出：拿到 PID 就点名等，拿不到就跳过（老调用方没传）
    if pid:
        # 这一行里有 %SYS%，不能用 % 格式化（%S 会被 Python 当成格式符），
        # 所以 PID 用字符串拼接塞进去。
        lines += [
            'for /L %%i in (1,1,60) do (',
            '  "%SYS%\\tasklist.exe" /FI "PID eq ' + str(pid) + '" /NH 2>nul'
            ' | "%SYS%\\findstr.exe" /C:"' + str(pid) + '" >nul || goto gone',
            '  ping -n 2 127.0.0.1 >nul',
            ')',
        ]
        if logger:
            lines.append(logger + ('等不到老进程退出（PID %d）' % pid))
    lines.append(':gone')

    lines += [
        'for /L %%i in (1,1,30) do (',
        '  attrib -r "%EXE%" >nul 2>&1',
        '  move /y "%NEW%" "%EXE%" >nul 2>&1',
        '  if not exist "%NEW%" goto swapped',
        '  ping -n 2 127.0.0.1 >nul',
        ')',
    ]
    if logger:
        lines.append(logger + '换不上：move 反复失败（只读、被占用或路径不对）')
    lines.append('goto end')
    lines.append(':swapped')
    if logger:
        lines.append(logger + '换好了')
    # .new 被 move 走了，它的附注不会跟着走，留着就是一份垃圾
    lines.append('del "%NEW%.json" >nul 2>&1')
    if relaunch:
        lines += [
            # 这三句是「新版本弹 Failed to load Python DLL」的解药，别删。
            # 脚本是桌宠自己的子进程，环境里带着 bootloader 留下的解包地址；
            # 不清掉的话，新 exe 会跳过解包、去老进程那个已经删掉的临时目录
            # 里找 python3xx.dll（见 PYI_ENV_VARS 上面的长注释）。
            # apply() 那边已经传了干净环境，这里是第二道锁 —— 脚本也可能被
            # 别的东西（计划任务、手工双击 bat）拉起来，那时候环境是脏的。
            'set "%s="' % PYI_ENV_VARS[0],
            'set "%s="' % PYI_ENV_VARS[1],
            'set "%s="' % PYI_ENV_VARS[2],
            # 先等一秒再拉。文件是刚被 move 过来的，杀软、索引器、
            # EDR 的落地扫描都可能正捏着它。
            'ping -n 2 127.0.0.1 >nul',
            'start "" "%EXE%"',
            # 起来了吗？onefile 要先解包 24MB，给 6 秒。
            # 解包失败时 bootloader 只弹一个「Failed to load Python DLL」
            # 然后退出，进程表里干干净净 —— 那种情况再拉一次通常就好了，
            # 所以这里不认命。重拉最多产生一个多余实例，而单实例锁会让
            # 它立刻自己退掉，代价可以忽略。
            'for /L %%i in (1,1,6) do (',
            '  "%SYS%\\tasklist.exe" /FI "IMAGENAME eq %EXENAME%" /NH 2>nul'
            ' | "%SYS%\\findstr.exe" /I /C:"%EXENAME%" >nul && goto launched',
            '  ping -n 2 127.0.0.1 >nul',
            ')',
        ]
        if logger:
            lines.append(logger + '没看到进程起来，再拉一次')
        lines += ['start "" "%EXE%"', ':launched']
    else:
        lines.append('rem 下次启动再生效')
    lines += [':end', '(goto) 2>nul & del "%~f0"', '']
    return '\r\n'.join(lines)


def _clear_readonly(path):
    """把我自己那个 exe 的只读位摘掉 —— 不摘的话 move 一定失败（见 _script）。"""
    try:
        if os.path.exists(path) and not os.access(path, os.W_OK):
            os.chmod(path, os.stat(path).st_mode | 0o200)
    except OSError:
        pass                        # 摘不掉就让脚本里那句 attrib -r 再试一次


def apply(relaunch=True):
    """把暂存的 exe 换上去。返回 (是否已经安排上了, 说明)。

    调用方拿到 True 之后应该**立刻退出** —— 脚本正等着我们消失才能 move。
    """
    ok, ver, new = staged_valid()
    exe = exe_path()
    if not exe:
        return False, '源码模式不支持自动更新'
    if not ok:
        return False, '没有下载好的新版本'
    _clear_readonly(exe)
    bat = os.path.join(os.environ.get('TEMP') or os.path.dirname(exe),
                       'pet-club-update.bat')
    script = _script(exe, new, relaunch, pid=os.getpid(), log=log_path())
    try:
        with open(bat, 'w', encoding='mbcs', errors='replace') as f:
            f.write(script)
    except (OSError, LookupError):
        try:
            with open(bat, 'w', encoding='utf-8') as f:
                f.write(script)
        except OSError as e:
            return False, '写不了更新脚本：%s' % (e,)

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    common = dict(startupinfo=si, close_fds=True,
                  stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL,
                  # 关键：不能把我们自己的 PyInstaller 启动器变量传给脚本，
                  # 否则脚本 start 起来的新 exe 会拒绝解包（见 PYI_ENV_VARS）
                  env=_clean_env())
    argv = ['cmd', '/c', bat]
    # 先试「脱离作业对象」：万一我们是被安装器/计划任务放在一个
    # KILL_ON_JOB_CLOSE 的作业里，不脱离的话脚本会被连同我们一起杀掉。
    # 有些作业不允许脱离，那就退回到不脱离 —— 那种情况下本来也不会被杀。
    for flags in FLAG_CANDIDATES:
        try:
            subprocess.Popen(argv, creationflags=flags, **common)
            return True, ver
        except OSError:
            continue
    return False, '起不了更新脚本'


def apply_pending():
    """启动时调用：上次下好了就换上并退出。返回 True = 调用方该退出了。"""
    ok, ver, _new = staged_valid()
    if not ok:
        return False
    done, _ = apply(relaunch=True)
    if done:
        print('[pet-club] 装上 %s，正在重启' % ver)
    return done


def cleanup():
    """清掉上次更新留下的垃圾（.part / 旧 .old / 已经比我旧的 .new / 孤立的 .new.json）。"""
    p = new_path()
    if not p:
        return
    for suffix in ('.part', '.old'):
        _unlink(p + suffix)
    # 比我旧的 .new 不会再用上了（见 staged_valid），留着只会占一份 exe 的空间，
    # 下次手工拷个新版本进来还会被误当成「待安装」
    staged_file, ver = staged()
    if not staged_file:
        # 没有 .new 却有 .new.json = 上次换成功之后剩下的空壳。
        # move 只搬 .new，附注留在原地，不清就一直躺在那儿。
        _unlink(meta_path())
    elif ver and not is_newer(ver, config.VERSION):
        _unlink(staged_file)
        _unlink(meta_path())


# ---------------- 后台检查（给 App 用，不碰控件） ----------------

class Checker:
    """后台线程做三件事：拉清单 → 判断 → 下载。结果塞进队列，主线程来取。

    和网络层同一个纪律：子线程只往 queue 里放东西，一个控件都不碰。
    """

    def __init__(self, on_event):
        self.on_event = on_event        # (kind, payload) -> None，在子线程里被调用
        self._busy = False
        self._lock = threading.Lock()

    def start(self, cur=None, force=False):
        """开始一轮检查。正在忙就忽略（点两下菜单不该起两个线程）。"""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(target=self._run, args=(cur, force), daemon=True).start()
        return True

    def _run(self, cur, force):
        try:
            self.on_event('checking', None)
            info, err = check(cur)
            if err:
                self.on_event('error', err)
                return
            if info is None:
                if force:
                    self.on_event('latest', '')
                else:
                    self.on_event('idle', '')
                return
            self.on_event('found', info)
            path, err = download(
                info, progress=lambda k: self.on_event('progress', k))
            if err:
                self.on_event('error', err)
                return
            self.on_event('ready', info)
        finally:
            with self._lock:
                self._busy = False
