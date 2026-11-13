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


# ---------------- 发布 ----------------
#
# 服务器地址写死在脚本顶部：和客户端一样，一个固定的房间，不做配置。
# 换服务器就改这三行。

PUBLISH_WEB = '''@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club 发布网页版

rem 一键发布：校验版本号 -> 算 build 号 -> 打包 -> 上传 -> 解压（并重启）
rem
rem   发布网页版.bat         日常用这个：代码 + 网页 + 素材，全量发，传完重启两个进程
rem   发布网页版.bat fast    同样全量传，但不重启。仅限没动过 .py 的时候
rem
rem 为什么默认要重启：web/ 和 assets/ 是每次请求读盘的，换掉就生效；
rem 但 .py 已经被解释器读进内存了，不重启就等于没传。
rem
rem 为什么素材也全量：拆开传的话，线上很容易长期停在「新 JS 配旧图片」，
rem 而且事后没人说得清服务器上到底是哪一版。6 MB 一个流，几秒的事。

set HOST=47.94.21.34
set USER=root
set PORT=22
set REMOTE=/opt/pet-club

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

echo ==========================================
echo   发布网页版到 %HOST%
echo ==========================================
%PYCMD% tools\\make_web_build.py
if errorlevel 1 goto fail

if not exist release mkdir release
echo 打包（全量，含素材）…
tar -czf release/pet-club-web.tar.gz --exclude __pycache__ --exclude assets/_gen server.py web_gateway.py petclub web assets deploy
if errorlevel 1 goto fail
for %%F in (release/pet-club-web.tar.gz) do echo 包大小 %%~zF 字节

echo 上传（下面要输服务器密码）…
scp -P %PORT% release/pet-club-web.tar.gz %USER%@%HOST%:%REMOTE%/
if errorlevel 1 goto fail

set "CMD=cd %REMOTE% && tar -xzf pet-club-web.tar.gz && rm -f pet-club-web.tar.gz"
set RESTART=1
if /i "%~1"=="fast" set RESTART=0
if "%RESTART%"=="1" set "CMD=%CMD% && sh deploy/stop.sh && sh deploy/start.sh"

echo 解压…
ssh -p %PORT% %USER%@%HOST% "%CMD%"
if errorlevel 1 goto fail

echo.
echo 发布完成：http://%HOST%:8766/
echo 已经打开的页面会在 1 分钟内提示有新版本（后台标签页会直接刷掉）。
pause
exit /b 0

:fail
echo.
echo 发布失败，看上面的报错。
pause
exit /b 1
'''

PUBLISH_ASSETS = '''@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club 发布素材

rem 只传 assets/ + build.json（角色立绘、表情包、图标，约 6 MB）。
rem 平时用「发布网页版.bat」全量发布就够了 —— 这条留着急救：
rem 只想换几张图、不想重启进程、也不想打 tag 的时候用。
rem
rem 会重算 build 号（素材也参与指纹），所以换图后已打开的页面照样会提示刷新，
rem 新图立刻生效，不用等浏览器那 1 小时缓存过期。

set HOST=47.94.21.34
set USER=root
set PORT=22
set REMOTE=/opt/pet-club

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

%PYCMD% tools\\make_web_build.py
if errorlevel 1 goto fail

if not exist release mkdir release
echo 打包素材…
tar -czf release/pet-club-assets.tar.gz --exclude assets/_gen assets web/build.json
if errorlevel 1 goto fail

echo 上传（下面要输服务器密码）…
scp -P %PORT% release/pet-club-assets.tar.gz %USER%@%HOST%:%REMOTE%/
if errorlevel 1 goto fail

echo 解压…
ssh -p %PORT% %USER%@%HOST% "cd %REMOTE% && tar -xzf pet-club-assets.tar.gz && rm -f pet-club-assets.tar.gz"
if errorlevel 1 goto fail

echo.
echo 素材已更新（没重启进程，build 号已变，页面 1 分钟内会提示刷新）。
pause
exit /b 0

:fail
echo.
echo 发布失败，看上面的报错。
pause
exit /b 1
'''


if __name__ == '__main__':
    write('启动服务端.bat', SERVER)
    write('启动桌宠.bat', CLIENT)
    write('发布网页版.bat', PUBLISH_WEB)
    write('发布素材.bat', PUBLISH_ASSETS)
