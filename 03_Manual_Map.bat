
@echo off
setlocal

cd /d "%~dp0"

title Stash Telegram Manual Mapper

echo ==========================================
echo       STASH - TELEGRAM MANUAL MAPPER
echo ==========================================
echo.

if not exist ".venv-wzgram\Scripts\python.exe" (
    echo ERROR: Wzgram virtual environment not found.
    pause
    exit /b 1
)

if not exist "manual_map_telegram.py" (
    echo ERROR: manual_map_telegram.py not found.
    pause
    exit /b 1
)

".venv-wzgram\Scripts\python.exe" "manual_map_telegram.py"

echo.
echo ==========================================
echo              PROCESS FINISHED
echo ==========================================
pause
