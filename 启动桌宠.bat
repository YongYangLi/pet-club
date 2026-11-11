@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club 客户端

rem 服务器地址写死在 petclub\config.py（SERVER_HOST / SERVER_PORT）：
rem 没有填地址这一步，也没有配置文件 —— 打开就进同一个房间。
rem 一台电脑只允许跑一个：再点一次不会开第二个，第二个进程会自己安静退出。

rem 有打包好的 exe 就先用 exe（拷给别人时只要这一个文件）
if exist "dist\pet-club.exe" (
  start "" "dist\pet-club.exe"
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
