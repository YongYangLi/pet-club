@echo off
cd /d "%~dp0"
title pet-club server
setlocal

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

echo ==========================================
echo   pet-club server
echo ==========================================
echo   Listen: 0.0.0.0:8765
echo   Local:  http://127.0.0.1:8766/  (web gateway)
echo   LAN:    use this machine's IPv4 with port 8765
echo   Stop:   Ctrl+C
echo.
%PYCMD% server.py --host 0.0.0.0 --port 8765
set CODE=%errorlevel%
echo.
echo server exited with code %CODE%.
pause
