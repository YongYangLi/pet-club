# -*- coding: utf-8 -*-
"""生成两个启动用的 .bat。

⚠️ Windows 的 bat 必须是 GBK + CRLF：
   本文件是 UTF-8 源码，直接写出去的中文会被 cmd 按 936 代码页解析成乱码；
   纯 LF 换行还会让 cmd 的 if / 括号块解析出错。所以统一在这里转码，别手写。
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SERVER = '''@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club 服务端

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

echo ==========================================
echo   pet-club 组队服务端
echo ==========================================
echo   本机测试：客户端直接用 127.0.0.1:8765
echo   局域网：  把下面列出的 IP 告诉队友
echo   停止：    按 Ctrl+C
echo.
ipconfig | findstr /i "IPv4"
echo.
%PYCMD% server.py --host 0.0.0.0 --port 8765
echo.
echo 服务端已退出。
pause
'''

CLIENT = '''@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club 客户端

rem 服务器地址写死在 petclub\\config.py（SERVER_HOST / SERVER_PORT）：
rem 没有填地址这一步，也没有配置文件 —— 打开就进同一个房间。
rem 一台电脑只允许跑一个：再点一次不会开第二个，第二个进程会自己安静退出。

rem 有打包好的 exe 就先用 exe（拷给别人时只要这一个文件）
if exist "dist\\pet-club.exe" (
  start "" "dist\\pet-club.exe"
  exit /b 0
)
if exist "pet-club.exe" (
  start "" "pet-club.exe"
  exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw run.py
  exit /b 0
)
echo 没找到 pythonw，改用 python 前台运行（关掉这个窗口就退出）。
python run.py
pause
'''


def write(name, text):
    p = os.path.join(ROOT, name)
    data = text.replace('\n', '\r\n').encode('gbk')
    with open(p, 'wb') as f:
        f.write(data)
    print('生成 %s（%d 字节，GBK+CRLF）' % (name, len(data)))


if __name__ == '__main__':
    write('启动服务端.bat', SERVER)
    write('启动桌宠.bat', CLIENT)
