@echo off
chcp 936 >nul
cd /d "%~dp0"
title pet-club desktop
setlocal

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)

echo ==========================================
echo   pet-club desktop (local server)
echo ==========================================
echo   Server: 127.0.0.1:8765
echo.

rem The packaged pet-club.exe is a remote build and takes the single-instance lock.
tasklist /FI "IMAGENAME eq pet-club.exe" 2>nul | findstr /I "pet-club.exe" >nul
if not errorlevel 1 (
  echo WARNING: pet-club.exe is already running.
  echo Quit it from the tray first, then run this again.
  pause
  exit /b 1
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
echo   Starting desktop client against 127.0.0.1:8765...
echo   If this exits immediately, another pet-club instance may be in the tray.
echo.
%PYCMD% run.py --local
set CODE=%errorlevel%
echo.
echo desktop client exited with code %CODE%.
pause
