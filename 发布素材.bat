@echo off
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

%PYCMD% tools\make_web_build.py
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
