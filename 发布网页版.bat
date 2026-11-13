@echo off
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
%PYCMD% tools\make_web_build.py
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
