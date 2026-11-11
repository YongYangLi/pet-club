# -*- coding: utf-8 -*-
"""把客户端打包成单文件 exe（PyInstaller）。

打出来的是 `dist/pet-club.exe`：双击即用，目标机器上不需要 Python、不需要
pip install、不需要旁边放 assets 目录 —— 立绘全部塞进 exe，启动时解到临时目录
（config.assets_dir() 认 _MEIPASS）。代价是首次解压约一两秒，换来的是「拷一个
文件过去就能用」。

素材只需要 assets/：素材/ 那 80 多兆原图是给我们自己做图的，跟运行无关。

跑法：python tools/build_exe.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from petclub.config import VERSION as VERSION_TEXT    # noqa: E402

NAME = 'pet-club'
ICON = os.path.join(ROOT, 'assets', 'app.ico')
ENTRY = os.path.join(ROOT, 'run.py')

# 版本号只有一个来源：petclub/config.py 的 VERSION。
# 这里再抄一份的下场是「exe 说自己 1.0.0，自动更新以为自己 1.1.0」，
# 于是同一个版本被反复下载、反复重启。
VERSION = (tuple(int(x) for x in VERSION_TEXT.split('.')) + (0, 0, 0))[:4]

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
        's': '.'.join(str(x) for x in VERSION),
        'n': NAME,
    }
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def main():
    if not os.path.exists(ICON):
        print('先跑 python tools/make_icon.py 生成图标')
        return 1

    build = os.path.join(ROOT, 'build')
    os.makedirs(build, exist_ok=True)
    vfile = version_file(os.path.join(build, 'version_info.txt'))

    # --add-data 的分隔符在 Windows 上是 ';'，别写死成 ':'（PyInstaller 6 两种都收，
    # 但 PATHSEP 更保险）
    add_data = '%s%sassets' % (os.path.join(ROOT, 'assets'), os.pathsep)

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--onefile',                # 单个 exe，拷过去就能用
        '--windowed',               # 不带控制台黑框（桌宠是挂件，不是命令行工具）
        '--name', NAME,
        '--icon', ICON,
        '--version-file', vfile,
        '--add-data', add_data,
        # 客户端不 import 服务端，但项目根同时在 sys.path 上，排除掉省体积
        '--exclude-module', 'server',
        '--exclude-module', 'numpy',
        '--exclude-module', 'pytest',
        '--distpath', os.path.join(ROOT, 'dist'),
        '--workpath', os.path.join(build, 'work'),
        '--specpath', build,
        ENTRY,
    ]
    print(' '.join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print('\n打包失败，退出码 %d' % rc)
        return rc

    exe = os.path.join(ROOT, 'dist', NAME + '.exe')
    print('\n已生成 %s（%.1f MB）' % (exe, os.path.getsize(exe) / 1048576.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
