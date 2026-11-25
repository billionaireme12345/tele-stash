@echo off
setlocal
title Stash Telegram - Master Launcher
cd /d "%~dp0"

:: Set the full path to stash-win.exe if it is not in this folder or Windows PATH
set "STASH_EXE=stash-win.exe"
if exist "%~dp0stash-win.exe" set "STASH_EXE=%~dp0stash-win.exe"

:: Supports both venv-wzgram (fresh install) and .venv-wzgram (existing setup)
set "PYTHON_EXE=%~dp0venv-wzgram\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0.venv-wzgram\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python virtual environment not found. Run 00_Install_Setup.bat first.
    pause
    exit /b 1
)

:: 1. Start Stash (skip if already running)
tasklist /FI "IMAGENAME eq stash-win.exe" 2>nul | find /I "stash-win.exe" >nul
if errorlevel 1 (
    start "Stash - 9999" /min "%STASH_EXE%" --nobrowser
    timeout /t 3 /nobreak >nul
)

:: 2. Start Wzgram Bridge minimized
start "Wzgram Bridge - 8767" /min /D "%~dp0" "%PYTHON_EXE%" bridge_wzgram_v2.py

timeout /t 3 /nobreak >nul

:: 3. Start Stash Proxy minimized
start "Stash Proxy - 9998" /min /D "%~dp0" "%PYTHON_EXE%" proxy.py

:: Optional: uncomment the next two lines to auto-open Stash via the proxy
:: timeout /t 2 /nobreak >nul
:: start "" "http://127.0.0.1:9998"

:: Close this launcher window automatically
exit