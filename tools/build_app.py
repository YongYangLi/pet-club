# -*- coding: utf-8 -*-
"""打包：Windows 出单文件 exe，macOS 出 .app（可再压成 zip / dmg）。

    python tools/build_app.py            # 按当前系统出对应产物
    python tools/build_app.py --zip      # macOS 顺带压一个分发用的 zip
    python tools/build_app.py --dmg      # macOS 顺带做一个 dmg（要 hdiutil）

**PyInstaller 不能交叉编译。** 在 Windows 上打不出 .app，在 Mac 上也打不出 .exe
——它靠的是目标系统的 bootloader、动态库和代码签名工具。所以「一次出两个平台」
只能靠在各自系统上各跑一次（CI 里就是两个 job，见 .github/workflows/build.yml）。
这里能保证的是：同一份脚本、同一份排除清单、同一个版本号，谁在哪个系统上跑
就出哪个系统的包，行为一致。

两个平台的差异只有三处，其余参数完全共用：
  · Windows 要 --onefile（拷一个 exe 就能用）+ 版本资源
  · macOS 要 onedir（.app 里本来就带目录，onedir 启动快，不用每次解 56MB）
    并回填 Info.plist：LSUIElement（不进 Dock）+ 版本号 + Retina 声明
  · macOS 改过 Info.plist 之后**必须重新 ad-hoc 签名**，否则签名封条对不上，
    系统会当成「已损坏」直接拒绝启动 —— 这是最容易漏的一步。
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub.config import VERSION as VERSION_TEXT    # noqa: E402

NAME = 'pet-club'
ENTRY = os.path.join(ROOT, 'run.py')
ASSETS = os.path.join(ROOT, 'assets')
ICON_WIN = os.path.join(ASSETS, 'app.ico')
ICON_MAC = os.path.join(ASSETS, 'app.icns')
BUNDLE_ID = 'cn.petclub.app'

DIST = os.path.join(ROOT, 'dist')
BUILD = os.path.join(ROOT, 'build')

# 版本号只有一个来源：petclub/config.py 的 VERSION。
# 这里再抄一份的下场是「exe 说自己 1.0.0，自动更新以为自己 1.1.0」，
# 于是同一个版本被反复下载、反复重启。
VERSION = (tuple(int(x) for x in VERSION_TEXT.split('.')) + (0, 0, 0))[:4]
VERSION_STR = VERSION_TEXT

# 两个平台共用：只用到 QtCore / QtGui / QtWidgets，剩下的每个都几十兆
COMMON_EXCLUDES = [
    'server',            # 客户端不 import 服务端，但根目录在 sys.path 上
    'numpy',
    'pytest',
    'tkinter',           # 老版本用过，别让残留的引用拖进来
    'unittest',
    'pydoc',
    'PySide6.QtNetwork',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtSql',
    'PySide6.QtTest',
    'PySide6.QtMultimedia',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtPdf',
    'PySide6.QtPrintSupport',
]

# Windows 文件属性里显示的那些字。没有它，exe 的「说明」是空的，
# 任务管理器里只会看到一个光秃秃的 run.exe。
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(v)s,
    prodvers=%(v)s,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'pet-club'),
        StringStruct('FileDescription', 'pet-club 桌面宠物'),
        StringStruct('FileVersion', '%(s)s'),
        StringStruct('InternalName', '%(n)s'),
        StringStruct('OriginalFilename', '%(n)s.exe'),
        StringStruct('ProductName', 'pet-club'),
        StringStruct('ProductVersion', '%(s)s')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def version_file(path):
    text = VERSION_INFO % {
        'v': tuple(VERSION),
        's': VERSION_STR,
        'n': NAME,
    }
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def base_cmd():
    """两个平台共用的那一段 PyInstaller 参数。"""
    # --add-data 的分隔符：Windows 是 ';'，macOS 是 ':'。用 os.pathsep，别写死
    add_data = '%s%sassets' % (ASSETS, os.pathsep)
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--windowed',               # 不带控制台（桌宠是挂件，不是命令行工具）
        '--name', NAME,
        '--add-data', add_data,
        '--distpath', DIST,
        '--workpath', os.path.join(BUILD, 'work'),
        '--specpath', BUILD,
    ]
    for mod in COMMON_EXCLUDES:
        cmd += ['--exclude-module', mod]
    return cmd + [ENTRY]


# ---------------- Windows ----------------

def build_win():
    if not os.path.exists(ICON_WIN):
        print('先跑 python tools/make_icon.py 生成图标')
        return 1
    os.makedirs(BUILD, exist_ok=True)
    vfile = version_file(os.path.join(BUILD, 'version_info.txt'))

    cmd = base_cmd()
    # --onefile：单个 exe，拷过去就能用
    cmd += ['--onefile', '--icon', ICON_WIN, '--version-file', vfile]

    print(' '.join(cmd))
    if subprocess.call(cmd, cwd=ROOT) != 0:
        print('\n打包失败')
        return 1
    exe = os.path.join(DIST, NAME + '.exe')
    print('\n已生成 %s（%.1f MB）' % (exe, os.path.getsize(exe) / 1048576.0))
    return 0


# ---------------- macOS ----------------

def _patch_plist(app_dir):
    """回填 Info.plist，然后重新 ad-hoc 签名。

    LSUIElement=1：不占 Dock、不进 Cmd-Tab —— 桌宠挂件该这样，退出走菜单栏。
    代价是 Cmd+Q 不起作用，用户得从托盘菜单退。所以托盘菜单里「退出」不能删。
    """
    plist = os.path.join(app_dir, 'Contents', 'Info.plist')
    if not os.path.exists(plist):
        print('没有 Info.plist，跳过回填：%s' % plist)
        return
    with open(plist, 'rb') as f:
        data = plistlib.load(f)
    data['CFBundleDisplayName'] = NAME
    data['CFBundleShortVersionString'] = VERSION_STR
    data['CFBundleVersion'] = VERSION_STR
    data['CFBundleIdentifier'] = BUNDLE_ID
    data['LSMinimumSystemVersion'] = data.get('LSMinimumSystemVersion', '11.0')
    data['LSUIElement'] = True              # 菜单栏应用
    data['NSHighResolutionCapable'] = True  # Retina，不然整个界面是模糊的
    with open(plist, 'wb') as f:
        plistlib.dump(data, f)
    print('已回填 Info.plist（%s，LSUIElement）' % VERSION_STR)

    # 改 plist = 破坏签名封条。不重签的话双击会报「应用已损坏」，非常吓人。
    sign = shutil.which('codesign')
    if not sign:
        print('⚠️  找不到 codesign，没有重新签名 —— 这个 app 很可能打不开')
        return
    rc = subprocess.call([sign, '--force', '--deep', '--sign', '-', app_dir],
                         stdout=subprocess.DEVNULL)
    print('已重新 ad-hoc 签名' if rc == 0 else '⚠️  重新签名失败（rc=%d）' % rc)


def _zip(app_dir):
    out = os.path.join(DIST, NAME + '-mac.zip')
    if os.path.exists(out):
        os.remove(out)
    # ditto 才保得住 .app 里的符号链接和扩展属性；zipfile 会把符号链接展开成
    # 副本，Qt 框架的 Versions/Current 一展开，包就废了。
    ditto = shutil.which('ditto')
    if ditto:
        subprocess.check_call([ditto, '-c', '-k', '--sequesterRsrc',
                               '--keepParent', app_dir, out])
    else:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for base, _dirs, files in os.walk(app_dir):
                for fn in files:
                    p = os.path.join(base, fn)
                    z.write(p, os.path.relpath(p, os.path.dirname(app_dir)))
    print('已生成 %s（%.1f MB）' % (out, os.path.getsize(out) / 1048576.0))
    return out


def _dmg(app_dir):
    hdiutil = shutil.which('hdiutil')
    if not hdiutil:
        print('这台机器没有 hdiutil，跳过 dmg')
        return ''
    out = os.path.join(DIST, NAME + '-mac.dmg')
    if os.path.exists(out):
        os.remove(out)
    rc = subprocess.call([hdiutil, 'create', '-volname', NAME, '-srcfolder',
                          app_dir, '-ov', '-format', 'UDZO', out])
    if rc != 0:
        print('⚠️  dmg 生成失败（rc=%d）' % rc)
        return ''
    print('已生成 %s（%.1f MB）' % (out, os.path.getsize(out) / 1048576.0))
    return out


def build_mac(zip_it=False, dmg_it=False):
    if not os.path.exists(ICON_MAC):
        print('先跑 python tools/make_mac_icons.py 生成 assets/app.icns')
        return 1
    cmd = base_cmd()
    # 不加 --onefile：.app 本身就是目录，onedir 启动快（不用每次解 56MB）
    cmd += ['--icon', ICON_MAC, '--osx-bundle-identifier', BUNDLE_ID]

    print(' '.join(cmd))
    if subprocess.call(cmd, cwd=ROOT) != 0:
        print('\n打包失败')
        return 1

    app_dir = os.path.join(DIST, NAME + '.app')
    if not os.path.isdir(app_dir):
        print('没找到 %s，打包结果和预期不一样' % app_dir)
        return 1
    _patch_plist(app_dir)
    if zip_it:
        _zip(app_dir)
    if dmg_it:
        _dmg(app_dir)
    print('\n已生成 %s' % app_dir)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='打包客户端（按当前系统出对应产物）')
    ap.add_argument('--target', choices=['auto', 'win', 'mac'], default='auto',
                    help='默认 auto：在哪个系统上跑就出哪个系统的包')
    ap.add_argument('--zip', action='store_true', help='macOS 额外压一个 zip')
    ap.add_argument('--dmg', action='store_true', help='macOS 额外做一个 dmg')
    args = ap.parse_args(argv)

    target = args.target
    if target == 'auto':
        target = ('win' if sys.platform == 'win32' else
                  'mac' if sys.platform == 'darwin' else '')
    if not target:
        print('不支持的系统：%s（只有 Windows 和 macOS）' % sys.platform)
        return 1
    if target == 'win' and sys.platform != 'win32':
        print('打 Windows 的 exe 必须在 Windows 上跑 —— PyInstaller 不支持交叉编译。')
        print('在 Mac 上请用 --target mac（或者什么都不传）。')
        return 1
    if target == 'mac' and sys.platform != 'darwin':
        print('打 macOS 的 .app 必须在 macOS 上跑 —— PyInstaller 不支持交叉编译。')
        print('在 Windows 上请用 --target win（或者什么都不传）。')
        print('想两边一起出，用 .github/workflows/build.yml 里那两个 job。')
        return 1

    os.makedirs(DIST, exist_ok=True)
    return build_win() if target == 'win' else build_mac(args.zip, args.dmg)


if __name__ == '__main__':
    sys.exit(main())
