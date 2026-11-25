@echo off
setlocal
title Stash Telegram - Stop All

:: Try graceful close for Stash first to protect its database
tasklist /FI "IMAGENAME eq stash-win.exe" 2>nul | find /I "stash-win.exe" >nul
if not errorlevel 1 (
    taskkill /IM stash-win.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: Stop WZGram Bridge (8767), Stash Proxy (9998), and Stash (9999) + child processes
for %%P in (8767 9998 9999) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
        taskkill /PID %%A /T /F >nul 2>&1
    )
)

:: Fallback force-close if stash-win.exe is still running
taskkill /IM stash-win.exe /F >nul 2>&1

:: Close this terminal window automatically
exit