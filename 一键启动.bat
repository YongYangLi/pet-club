@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club one-click
setlocal

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

echo ==========================================
echo   pet-club one-click
echo ==========================================
echo   Server: 127.0.0.1:8765
echo   Web:    http://127.0.0.1:8766/
echo.

rem 1) Local server
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul 2>nul
if errorlevel 1 (
  echo   Starting local server on 8765...
  start "pet-club server" cmd /c %PYCMD% server.py --host 0.0.0.0 --port 8765
  ping -n 3 127.0.0.1 >nul
) else (
  echo   Local server already listening on 8765.
)

rem 2) Web gateway
netstat -ano | findstr ":8766" | findstr "LISTENING" >nul 2>nul
if errorlevel 1 (
  echo   Starting web gateway on 8766...
  start "pet-club web" cmd /c %PYCMD% web_gateway.py --host 0.0.0.0 --port 8766 --upstream 127.0.0.1:8765
  ping -n 2 127.0.0.1 >nul
) else (
  echo   Web gateway already listening on 8766.
)

rem 3) Desktop client. The packaged pet-club.exe is a remote build and
rem    takes the single-instance lock, so it must be closed first.
tasklist /FI "IMAGENAME eq pet-club.exe" 2>nul | findstr /I "pet-club.exe" >nul
if not errorlevel 1 (
  echo.
  echo WARNING: pet-club.exe is already running.
  echo Quit it from the tray first. Server and web are already started.
  pause
  exit /b 1
)

echo.
echo   Starting desktop client against 127.0.0.1:8765...
echo   Web page: http://127.0.0.1:8766/
echo.
%PYCMD% run.py --local
set CODE=%errorlevel%
echo.
echo desktop client exited with code %CODE%.
pause
