@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club web
setlocal

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

echo ==========================================
echo   pet-club web (local server)
echo ==========================================
echo   Server: 127.0.0.1:8765
echo   Web:    http://127.0.0.1:8766/
echo.

netstat -ano | findstr ":8766" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo Web gateway already listening on 8766.
  pause
  exit /b 0
)

rem Start the local server if nothing is LISTENING on 8765.
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul 2>nul
if errorlevel 1 (
  echo   Starting local server on 8765...
  start "pet-club server" cmd /c %PYCMD% server.py --host 0.0.0.0 --port 8765
  ping -n 3 127.0.0.1 >nul
) else (
  echo   Local server already listening on 8765.
)

echo.
echo   LAN: use this machine's IPv4 with port 8766
echo.
%PYCMD% web_gateway.py --host 0.0.0.0 --port 8766 --upstream 127.0.0.1:8765
set CODE=%errorlevel%
echo.
echo web gateway exited with code %CODE%.
pause
