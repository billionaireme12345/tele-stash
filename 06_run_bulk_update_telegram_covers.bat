@echo off
title Bulk Update Telegram Covers

cd /d "%~dp0"

echo ==========================================
echo       BULK UPDATE TELEGRAM COVERS
echo ==========================================
echo.

"%~dp0.venv-wzgram\Scripts\python.exe" "%~dp0bulk_update_telegram_covers.py" --apply

echo.
pause