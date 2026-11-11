@echo off
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
